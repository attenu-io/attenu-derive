"""
attenu-sample — observe-mode sampling of a CrewAI crew (orchestrator + specialist coworkers via CrewAI's
own `Delegate work to coworker` tool) over a real repository, with Anthropic Haiku through `crewai.LLM`.
Records every delegation and tool call through the delegation-guard audit log (redacted at capture,
ADR-05) and exports corpus rows. 4th and last framework of the G2 quartet (T15).

    python -m attenu_derive.sample.run_crewai --repo <path> --out data/ --limit 2

Observe mode uses the shim's CrewAI bridge hooks (`default_policy`, `default_delegation_authority`):
every tool call is authorized-and-recorded with the redacted feature context; the delegation tool's
`task` text is the coworker's task on the spawn record (mirror only; hashed in the corpus).

Guardrails (same class as T1/T9): hard per-task INPUT-TOKEN budget counted across ALL agents
(`BudgetHook` = a global `after_llm_call` hook reading CrewAI's own per-LLM usage tracking — coworkers'
calls included, proven by tests/test_budget_guard_crewai.py; abort = HookAborted), agent `max_iter`,
hard per-task wall-clock timeout, incremental per-task export, `est_cost_usd` in every manifest.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Optional

os.environ.setdefault("OTEL_SDK_DISABLED", "true"); os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true"); os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from delegation_guard import Authority, Guard, __version__ as DG_VERSION

from attenu_derive import __version__ as AD_VERSION
from attenu_derive.corpus.export import _task_features, audit_to_corpus_rows
from attenu_derive.sample.features import extract_features

OBSERVE = Authority({"observe.*", "agent.delegate.*"}, [], ttl=None)

SPECIALISTS = {   # role -> (goal, backstory)
    "researcher":        ("Explore the repository and report concise findings with file paths.",
                          "You are a code researcher. Use list_files, search_files and read_file (few, targeted reads). Return concise findings with file paths. You never write files."),
    "security-reviewer": ("Find security-relevant code paths (input parsing, file/network access, auth, escaping, secrets).",
                          "You are a security reviewer. Use list_files, search_files and read_file to locate security-relevant code paths; report each with file path and one line of risk. You never write files."),
    "test-analyst":      ("Explain how the tests are organized and run.",
                          "You analyse the test suite: locate tests, runners, CI config with list_files/search_files/read_file; report how to run them, with file paths. You never write files."),
    "api-surveyor":      ("Map the public API / CLI surface and deprecations.",
                          "You map the public API or CLI surface: entry points, exported functions/commands, deprecated items; report with file paths. You never write files."),
}
AGENT_TOOLS = {"orchestrator": ["write_file", "delegate_work_to_coworker", "ask_question_to_coworker"], **{n: ["list_files", "read_file", "search_files"] for n in SPECIALISTS}}   # declared suites -> corpus rows
FANOUT_TASKS = [
    "Produce REPORT.md for this repository with four sections: architecture, security-relevant paths, tests, public API. "
    "Delegate EACH section to its specialist coworker (researcher, security-reviewer, test-analyst, api-surveyor) with the "
    "'Delegate work to coworker' tool, one after another; do all reading through them; then write REPORT.md yourself with write_file.",
    "Write ONBOARDING.md for a new contributor: how the code is organized (researcher), what to be careful about security-wise "
    "(security-reviewer), how to run the tests (test-analyst), and which public APIs or commands matter (api-surveyor). "
    "Delegate every reading task to those coworkers; write only the final file yourself with write_file.",
    "Assess this repository for a security review: delegate exploration to the researcher and the security-reviewer, ask the "
    "test-analyst whether security-relevant paths are covered by tests, and the api-surveyor which public APIs expose those paths. "
    "Write SECURITY_ASSESSMENT.md yourself with write_file; do all reading through the coworkers.",
]

_PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-5": (3.0, 15.0), "claude-sonnet-4": (3.0, 15.0), "claude-opus": (15.0, 75.0)}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    m = model.split("/", 1)[-1]
    pin, pout = next((v for k, v in _PRICES.items() if m.startswith(k)), (3.0, 15.0))
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout


class BudgetExceeded(RuntimeError):
    pass


class BudgetHook:
    """Global CrewAI LLM hooks that ABORT the crew when cumulative prompt tokens across ALL agents pass the
    budget. Usage is read from CrewAI's own per-LLM-instance tracking (`get_token_usage_summary`) as a delta
    per hook firing, so coworkers' calls (any LLM instance, any agent) are counted — the traffic that loops.
    Abort = `HookAborted` (the one exception CrewAI's dispatcher propagates; anything else is swallowed).
    Two lessons from live runs, both pinned by tests: (1) CrewAI SKIPS after_llm_call hooks for tool-call
    responses on the native function-calling path (agent_utils._setup_after_llm_call_hooks) — an after-hook
    fires once per agent execution — so the count happens at `before_llm_call`, which fires EVERY call (the
    abort lands one call late, like ADK's after_model_callback); (2) the executor RETRIES after an aborted
    after-hook, so the before-hook also raises once aborted — no model call happens after the abort."""

    def __init__(self, max_input_tokens: int):
        self.max = int(max_input_tokens); self.used = 0; self.output = 0; self.cached = 0; self.calls = 0
        self.by_agent: dict[str, int] = {}; self.aborted = False; self._seen: dict[int, tuple[int, int, int]] = {}

    def _account(self, ctx, *, count_call: bool) -> None:
        """Fold the LLM instance's cumulative usage delta into the totals; raise HookAborted once over budget."""
        llm = getattr(ctx, "llm", None)
        try:
            u = llm.get_token_usage_summary()
        except Exception:                             # noqa: BLE001 — an LLM without tracking cannot be budgeted; count nothing, never fail open silently
            return None
        key = id(llm); prev = self._seen.get(key, (0, 0, 0))
        cur = (int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0), int(getattr(u, "cached_prompt_tokens", 0) or 0))
        self._seen[key] = cur
        d_in = max(0, cur[0] - prev[0]); d_out = max(0, cur[1] - prev[1]); d_c = max(0, cur[2] - prev[2])
        self.used += d_in; self.output += d_out; self.cached += d_c
        if count_call: self.calls += 1
        role = getattr(getattr(ctx, "agent", None), "role", None) or "?"
        self.by_agent[role] = self.by_agent.get(role, 0) + d_in
        if self.used > self.max:
            self.aborted = True
            from crewai.hooks import HookAborted
            raise HookAborted(f"input-token budget exceeded: {self.used} > {self.max}", source=self)

    def _before(self, ctx):
        if self.aborted:                              # the executor retries after an abort: stop BEFORE the model is called
            from crewai.hooks import HookAborted
            raise HookAborted(f"input-token budget exceeded: {self.used} > {self.max} (no further model calls)", source=self)
        self._account(ctx, count_call=True)            # fires EVERY call: the previous call's usage is folded in here (one call late)
        return None

    def _after(self, ctx):
        self._account(ctx, count_call=False)           # only fires on textual responses (native path skips tool-call lists): catches the last call
        return None

    def __enter__(self):
        from crewai.hooks import register_after_llm_call_hook, register_before_llm_call_hook
        register_before_llm_call_hook(self._before); register_after_llm_call_hook(self._after); return self

    def __exit__(self, *exc):
        from crewai.hooks import unregister_after_llm_call_hook, unregister_before_llm_call_hook
        for fn, un in ((self._after, unregister_after_llm_call_hook), (self._before, unregister_before_llm_call_hook)):
            try: un(fn)
            except Exception: pass                    # noqa: BLE001


def make_bridge(salt: str):
    """(root Guard, observe-mode CrewAIGuardBridge): every tool call recorded with the redacted feature context."""
    from delegation_guard.adapters.crewai import CrewAIGuardBridge, ToolPolicy
    root = Guard.issue("orchestrator", OBSERVE, task="sample", max_depth=8, max_fanout=10_000)
    bridge = CrewAIGuardBridge(
        root_guard=root, root_role="orchestrator", tool_policies={}, delegation_authorities={},
        default_policy=lambda name: ToolPolicy(f"observe.{name}", lambda a: extract_features(a, salt=salt)),
        default_delegation_authority=lambda role: OBSERVE,
    )
    return root, bridge


# ---- repository tools (read-only over the checkout; writes go to the run's artifacts dir) -----------------------
_MAX_LIST = 80; _MAX_LINES = 120; _MAX_MATCHES = 40; _MAX_FILE_BYTES = 400_000
_SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", "__pycache__", ".venv", "vendor"}


def make_tools(repo: Path, artifacts: Path):
    from crewai.tools import tool
    repo = repo.resolve(); artifacts.mkdir(parents=True, exist_ok=True)

    def _inside(p: str) -> Path:
        q = (repo / p).resolve()
        if repo not in q.parents and q != repo:
            raise ValueError("path escapes the repository")
        return q

    def _skip(p: Path) -> bool:
        return any(part in _SKIP_DIRS for part in p.relative_to(repo).parts)

    @tool("list_files")
    def list_files(pattern: str = "**/*") -> str:
        """List repository files matching a glob pattern relative to the repo root, e.g. "src/**/*.java" or "*.md"."""
        out = []
        for p in sorted(repo.glob(pattern)):
            if p.is_file() and not _skip(p):
                out.append(str(p.relative_to(repo)))
                if len(out) >= _MAX_LIST: break
        return json.dumps({"files": out, "truncated": len(out) >= _MAX_LIST})

    @tool("read_file")
    def read_file(path: str, offset: int = 0, limit: int = 120) -> str:
        """Read up to `limit` lines of a repository file starting at line `offset` (0-based)."""
        q = _inside(path)
        if not q.is_file(): return json.dumps({"error": "not a file"})
        if q.stat().st_size > _MAX_FILE_BYTES: return json.dumps({"error": "file too large; use search_files"})
        lines = q.read_text(errors="replace").splitlines()
        lim = max(1, min(int(limit or _MAX_LINES), _MAX_LINES)); off = max(0, int(offset or 0))
        return json.dumps({"path": path, "offset": off, "lines": lines[off: off + lim], "total_lines": len(lines)})

    @tool("search_files")
    def search_files(pattern: str, glob: str = "**/*") -> str:
        """Search repository files for a regular expression; returns up to 40 matches as {path, line, text}."""
        rx = re.compile(pattern); hits = []
        for p in sorted(repo.glob(glob)):
            if not p.is_file() or _skip(p) or p.stat().st_size > _MAX_FILE_BYTES: continue
            try:
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append({"path": str(p.relative_to(repo)), "line": i, "text": line.strip()[:200]})
                        if len(hits) >= _MAX_MATCHES: return json.dumps({"matches": hits, "truncated": True})
            except (OSError, UnicodeDecodeError):
                continue
        return json.dumps({"matches": hits, "truncated": False})

    @tool("write_file")
    def write_file(path: str, content: str) -> str:
        """Write the final deliverable file (e.g. REPORT.md). Only the orchestrator writes; one file per task."""
        name = Path(path).name or "OUTPUT.md"
        (artifacts / name).write_text(content)
        return json.dumps({"written": name, "bytes": len(content.encode())})

    return list_files, read_file, search_files, write_file


def build_crew(*, llm, repo: Path, artifacts: Path, task_text: str, max_iter: int):
    from crewai import Agent, Crew, Process, Task
    list_files, read_file, search_files, write_file = make_tools(repo, artifacts)
    specialists = [Agent(role=r, goal=g, backstory=b, llm=llm, tools=[list_files, read_file, search_files], allow_delegation=False, verbose=False, max_iter=max_iter)
                   for r, (g, b) in SPECIALISTS.items()]
    orch = Agent(role="orchestrator", goal="Analyse the repository by delegating exploration to the specialist coworkers and write exactly the file the task asks for.",
                 backstory="A senior engineer who delegates all reading to coworkers, keeps own tool use minimal, and writes one deliverable with write_file.",
                 llm=llm, tools=[write_file], allow_delegation=True, verbose=False, max_iter=max_iter)
    task = Task(description=task_text, expected_output="The requested file written with write_file, then a one-line confirmation.", agent=orch)
    return Crew(agents=[orch] + specialists, tasks=[task], process=Process.sequential, telemetry=False)


async def run_task(task: str, *, repo: Path, model: str, salt: str, artifacts: Path, max_input_tokens: int, max_iter: int, timeout_s: float):
    from crewai import LLM
    from crewai.hooks import clear_all_global_hooks
    clear_all_global_hooks()
    root, bridge = make_bridge(salt); budget = BudgetHook(max_input_tokens)
    llm = LLM(model=model, temperature=0, max_tokens=2048)
    crew = build_crew(llm=llm, repo=repo, artifacts=artifacts, task_text=task, max_iter=max_iter)
    status = "ok"

    def _kick():
        with bridge, budget:
            crew.kickoff()
    try:
        await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, _kick), timeout=timeout_s)
    except asyncio.TimeoutError:
        status = f"error: Timeout: task exceeded {timeout_s:.0f}s wall clock"
    except Exception as exc:                                     # noqa: BLE001 — keep sampling; record the failure
        status = f"error: {'BudgetExceeded' if budget.aborted else type(exc).__name__}: {str(exc)[:120]}"
    finally:
        clear_all_global_hooks()
    usage = {"input_tokens": budget.used, "output_tokens": budget.output, "cached_input_tokens": budget.cached, "llm_calls": budget.calls,
             "by_agent": dict(budget.by_agent), "est_cost_usd": round(estimate_cost(model, budget.used, budget.output), 4)}
    return root, bridge, status, usage, budget.aborted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True); ap.add_argument("--out", default="data"); ap.add_argument("--project", default=None)
    ap.add_argument("--model", default="anthropic/claude-haiku-4-5-20251001")
    ap.add_argument("--tasks", default=None); ap.add_argument("--limit", type=int, default=None); ap.add_argument("--task-index", type=int, default=None)
    ap.add_argument("--max-input-tokens", type=int, default=200_000, help="HARD per-task budget across all agents (abort)")
    ap.add_argument("--max-iter", type=int, default=15, help="CrewAI Agent.max_iter per agent")
    ap.add_argument("--timeout-s", type=float, default=900.0)
    args = ap.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — `set -a; source ~/.attenu/keys.env; set +a`", file=sys.stderr); return 2
    repo = Path(args.repo).resolve(); project = args.project or repo.name
    tasks = [t.strip() for t in Path(args.tasks).read_text().splitlines() if t.strip()] if args.tasks else FANOUT_TASKS
    if args.limit: tasks = tasks[: args.limit]
    if args.task_index is not None: tasks = [tasks[args.task_index]]
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3); salt = secrets.token_hex(16)
    out = Path(args.out)
    for d in ("corpus", "mirror", f"runs/{run_id}"): (out / d).mkdir(parents=True, exist_ok=True)
    run_meta = {"project": project, "framework": "crewai", "model": args.model, "seed": 0, "salt": salt,
                "versions": {"attenu-derive": AD_VERSION, "delegation-guard": DG_VERSION, "python": platform.python_version()}}
    corpus_rows, mirror_rows, per_task = [], [], []
    for i, task in enumerate(tasks):
        t0 = time.time()
        root, bridge, status, usage, aborted = asyncio.run(run_task(task, repo=repo, model=args.model, salt=salt, artifacts=out / "runs" / run_id / f"task{i}-artifacts",
                                                                     max_input_tokens=args.max_input_tokens, max_iter=args.max_iter, timeout_s=args.timeout_s))
        entries = root.audit_log().entries
        (out / "runs" / run_id / f"task{i}-audit.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")
        run_i = dict(run_meta, task_index=i)
        rows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="hash"); mrows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="keep")
        for r, mr in zip(rows, mrows):
            r["tools_available"] = mr["tools_available"] = list(AGENT_TOOLS.get(r["agent"], []))            # declared suite (role-specific)
            if r["parent_node"] is not None:
                r["role_constraints"] = mr["role_constraints"] = {"no_write": True}                          # coworkers: "You never write files"
            if r["parent_node"] is None:
                r["subagent_tools"] = mr["subagent_tools"] = {n: list(AGENT_TOOLS[n]) for n in SPECIALISTS}   # the delegation subtree a parent must cover
            if r["parent_node"] is None:
                r["task_hash"] = hashlib.sha256(f"{salt}\x1f{task}".encode()).hexdigest()[:16]; r["task_features"] = _task_features(task); mr["task"] = task
        corpus_rows += rows; mirror_rows += mrows
        (out / "corpus" / f"{project}-crewai-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")
        (out / "mirror" / f"{project}-crewai-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
        n_calls = sum(len(r["child_calls"]) for r in rows); n_deleg = sum(1 for r in rows if r["parent_node"] is not None)
        per_task.append({"task_index": i, "status": status, "seconds": round(time.time() - t0, 1), "aborted": aborted, "delegations": n_deleg,
                         "tool_calls": n_calls, "usage": usage, "audit_events": len(entries), "denials": len(bridge.denials)})
        print(f"[task {i}] {status} | delegations={n_deleg} tool_calls={n_calls} denials={len(bridge.denials)} tokens={usage['input_tokens']}+{usage['output_tokens']} "
              f"llm_calls={usage['llm_calls']} est=${usage['est_cost_usd']} in {per_task[-1]['seconds']}s")
    manifest = {"run_id": run_id, **{k: v for k, v in run_meta.items() if k != "salt"}, "billing": "api",
                "guardrails": {"max_input_tokens": args.max_input_tokens, "max_iter": args.max_iter, "timeout_s": args.timeout_s},
                "tasks": len(tasks), "task_texts": tasks, "results": per_task,
                "totals": {"delegation_events": sum(1 for r in corpus_rows if r["parent_node"] is not None), "rows": len(corpus_rows),
                           "tool_calls": sum(len(r["child_calls"]) for r in corpus_rows),
                           "input_tokens": sum(p["usage"]["input_tokens"] for p in per_task), "output_tokens": sum(p["usage"]["output_tokens"] for p in per_task),
                           "est_cost_usd": round(sum(p["usage"]["est_cost_usd"] for p in per_task), 4)}}
    (out / "runs" / run_id / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
