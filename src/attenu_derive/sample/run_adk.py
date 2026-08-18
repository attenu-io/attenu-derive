"""
attenu-sample — observe-mode sampling of a Google ADK agent tree (orchestrator + specialist
sub-agents via `AgentTool`) over a real repository, with Gemini Flash (Google AI Studio free
tier by default; `GOOGLE_API_KEY`). Records every delegation and tool call through the
delegation-guard audit log (redacted at capture, ADR-05) and exports corpus rows.

    python -m attenu_derive.sample.run_adk --repo <path> --out data/ --model gemini-2.5-flash --limit 2

Third G2 framework (Rafael's quartet: deepagents, Claude Agent SDK, CrewAI, ADK). Fan-out by
construction: one task -> 4 specialist delegations. Guardrails (PM decisions 2026-08-18): hard
per-task INPUT-TOKEN budget counted across ALL agents (`BudgetPlugin.after_model_callback` fires
for delegated agents too — proven by tests/test_budget_guard_adk.py), `RunConfig.max_llm_calls`,
hard per-task wall-clock timeout (fan-out path: 900 s), incremental per-task export, notional
`est_cost_usd` in every manifest even on the free tier.

Observe mode uses the shim's ADK plugin hooks (`default_tool_authority`, `default_delegation`):
every tool call is authorized-and-recorded with the redacted feature context; the `AgentTool`
request text becomes the child's task on the spawn record (mirror only; hashed in the corpus).
Tool names are the harness's own (`list_files`, `read_file`, `search_text`, `write_file`) —
coverage on ADK rows is reported honestly, curated vs heuristic.
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
from typing import Any, Optional

from delegation_guard import Authority, Guard, __version__ as DG_VERSION

from attenu_derive import __version__ as AD_VERSION
from attenu_derive.corpus.export import _task_features, audit_to_corpus_rows
from attenu_derive.sample.features import extract_features

OBSERVE = Authority({"observe.*", "agent.delegate.*"}, [], ttl=None)

SPECIALISTS = {   # name -> (description, instruction); names are ADK identifiers (snake_case)
    "researcher":        ("Explores the repository: lists, searches and reads files, and reports concise findings with file paths.",
                          "You are a code researcher. Use list_files, search_text and read_file to explore efficiently (few, targeted reads). Return concise findings with file paths. Do NOT write files."),
    "security_reviewer": ("Finds security-relevant code paths (input parsing, file/network access, auth, escaping, secrets).",
                          "You are a security reviewer. Use list_files, search_text and read_file to locate security-relevant code paths; report each with file path and one line of risk. Do NOT write files."),
    "test_analyst":      ("Explains how tests are organized and run.",
                          "You analyse the test suite: locate tests, runners, CI config with list_files/search_text/read_file; report how to run them, with file paths. Do NOT write files."),
    "api_surveyor":      ("Maps the public API / CLI surface and deprecations.",
                          "You map the public API or CLI surface: entry points, exported functions/commands, deprecated items; report with file paths. Do NOT write files."),
}
FANOUT_TASKS = [
    "Produce REPORT.md for this repository with four sections: architecture, security-relevant paths, tests, public API. "
    "Delegate EACH section to its specialist sub-agent tool (researcher, security_reviewer, test_analyst, api_surveyor), "
    "one after another; do all reading through them; then write REPORT.md yourself with write_file and finish.",
    "Write ONBOARDING.md for a new contributor: how the code is organized (researcher), what to be careful about security-wise "
    "(security_reviewer), how to run the tests (test_analyst), and which public APIs or commands matter (api_surveyor). "
    "Delegate every reading task to those sub-agent tools; write only the final file yourself with write_file.",
    "Assess this repository for a security review: delegate exploration to the researcher and the security_reviewer, "
    "ask the test_analyst whether security-relevant paths are covered by tests, and the api_surveyor which public APIs "
    "expose those paths. Write SECURITY_ASSESSMENT.md yourself with write_file; do all reading through the sub-agents.",
]

# Public list prices (USD per 1M tokens: input, output) — for HONEST notional manifests even on the free tier.
# Known at write time (2026-08-18): the 2.5 family. Newer Flash models (3.x; `gemini-2.5-flash` is closed to new
# keys) are priced CONSERVATIVELY at the 2.5-Pro rate until confirmed — the manifest says so (`price_basis`).
_PRICES = {"gemini-2.5-flash-lite": (0.10, 0.40), "gemini-2.5-flash": (0.30, 2.50), "gemini-2.0-flash": (0.10, 0.40), "gemini-2.5-pro": (1.25, 10.0)}
_CONSERVATIVE = (1.25, 10.0)


def price_basis(model: str) -> str:
    return "list" if any(model.startswith(k) for k in _PRICES) else "conservative (unknown list price; 2.5-Pro rate)"


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = next((v for k, v in sorted(_PRICES.items(), key=lambda kv: -len(kv[0])) if model.startswith(k)), _CONSERVATIVE)
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout


class BudgetExceeded(RuntimeError):
    pass


def _lazy_adk():
    from google.adk.plugins.base_plugin import BasePlugin
    return BasePlugin


class BudgetPlugin(_lazy_adk()):
    """ADK plugin that ABORTS the run when cumulative prompt tokens across ALL agents pass the budget.
    Plugin callbacks are app-global, so a delegated agent's model calls (AgentTool / transfer / task
    sub-agents) are counted too — the traffic that loops. Also the usage capture for the manifest."""

    def __init__(self, max_input_tokens: int, name: str = "attenu_budget"):
        super().__init__(name=name)
        self.max = int(max_input_tokens); self.used = 0; self.output = 0; self.cached = 0; self.calls = 0
        self.by_agent: dict[str, int] = {}; self.aborted = False

    async def after_model_callback(self, *, callback_context, llm_response):
        um = getattr(llm_response, "usage_metadata", None)
        if um is None:
            return None
        pin = int(getattr(um, "prompt_token_count", 0) or 0); pout = int(getattr(um, "candidates_token_count", 0) or 0)
        self.used += pin; self.output += pout; self.cached += int(getattr(um, "cached_content_token_count", 0) or 0); self.calls += 1
        agent = getattr(callback_context, "agent_name", None) or "?"
        self.by_agent[agent] = self.by_agent.get(agent, 0) + pin
        if self.used > self.max:
            self.aborted = True
            raise BudgetExceeded(f"input-token budget exceeded: {self.used} > {self.max}")
        return None


def make_plugin(salt: str):
    """(root Guard, observe-mode DelegationGuardPlugin): every tool call recorded with the redacted feature context."""
    from delegation_guard.adapters.google_adk import DelegationGuardPlugin, ToolAuthority
    root = Guard.issue("orchestrator", OBSERVE, task="sample", max_depth=8, max_fanout=10_000)
    plugin = DelegationGuardPlugin(
        root, root_agent_name="orchestrator", delegations={}, tools={},
        default_tool_authority=lambda name: ToolAuthority(f"observe.{name}", lambda a: extract_features(a, salt=salt)),
        default_delegation=lambda name: OBSERVE,
        # no delegation_scope: the hand-off is recorded by the spawn (as deepagents does), not as a tool call named after the child
    )
    return root, plugin


# ---- repository tools (read-only over the checkout; writes go to the run's artifacts dir) -----------------------
_MAX_LIST = 200; _MAX_LINES = 200; _MAX_MATCHES = 100; _MAX_FILE_BYTES = 400_000
_SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", "__pycache__", ".venv", "vendor"}


def make_tools(repo: Path, artifacts: Path):
    repo = repo.resolve(); artifacts.mkdir(parents=True, exist_ok=True)

    def _inside(p: str) -> Path:
        q = (repo / p).resolve()
        if repo not in q.parents and q != repo:
            raise ValueError("path escapes the repository")
        return q

    def _skip(p: Path) -> bool:
        return any(part in _SKIP_DIRS for part in p.relative_to(repo).parts)

    def list_files(pattern: str = "**/*") -> dict:
        """List files in the repository matching a glob pattern (relative to the repo root), e.g. "src/**/*.rs" or "*.md"."""
        out = []
        for p in sorted(repo.glob(pattern)):
            if p.is_file() and not _skip(p):
                out.append(str(p.relative_to(repo)))
                if len(out) >= _MAX_LIST:
                    break
        return {"files": out, "truncated": len(out) >= _MAX_LIST}

    def read_file(path: str, offset: int = 0, limit: int = 200) -> dict:
        """Read up to `limit` lines of a repository file starting at line `offset` (0-based)."""
        q = _inside(path)
        if not q.is_file():
            return {"error": "not a file"}
        if q.stat().st_size > _MAX_FILE_BYTES:
            return {"error": "file too large; use search_text"}
        lines = q.read_text(errors="replace").splitlines()
        lim = max(1, min(int(limit or _MAX_LINES), _MAX_LINES)); off = max(0, int(offset or 0))
        return {"path": path, "offset": off, "lines": lines[off: off + lim], "total_lines": len(lines)}

    def search_text(pattern: str, glob: str = "**/*") -> dict:
        """Search the repository for a regular expression; returns up to 100 matches as {path, line, text}."""
        rx = re.compile(pattern); hits = []
        for p in sorted(repo.glob(glob)):
            if not p.is_file() or _skip(p) or p.stat().st_size > _MAX_FILE_BYTES:
                continue
            try:
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append({"path": str(p.relative_to(repo)), "line": i, "text": line.strip()[:200]})
                        if len(hits) >= _MAX_MATCHES:
                            return {"matches": hits, "truncated": True}
            except (OSError, UnicodeDecodeError):
                continue
        return {"matches": hits, "truncated": False}

    def write_file(path: str, content: str) -> dict:
        """Write the final deliverable file (e.g. REPORT.md). Only the orchestrator writes; one file per task."""
        name = Path(path).name or "OUTPUT.md"
        (artifacts / name).write_text(content)
        return {"written": name, "bytes": len(content.encode())}

    return list_files, read_file, search_text, write_file


def build_tree(*, model: str, repo: Path, artifacts: Path):
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.tools.agent_tool import AgentTool
    list_files, read_file, search_text, write_file = make_tools(repo, artifacts)
    specialists = [LlmAgent(name=n, model=model, description=d, instruction=i, tools=[list_files, read_file, search_text])
                   for n, (d, i) in SPECIALISTS.items()]
    return LlmAgent(
        name="orchestrator", model=model, description="Senior engineer analysing a repository; delegates reading to specialists.",
        instruction=("You are a senior engineer analysing a repository. Delegate exploration to the specialist sub-agent tools "
                     "(researcher, security_reviewer, test_analyst, api_surveyor); keep your own tool use minimal; write exactly "
                     "the file the task asks for with write_file and finish."),
        tools=[AgentTool(agent=s) for s in specialists] + [write_file],
    )


async def run_task(task: str, *, repo: Path, model: str, salt: str, artifacts: Path, max_input_tokens: int, max_llm_calls: int,
                   timeout_s: float, trace: Optional[Path] = None):
    from google.adk.agents.run_config import RunConfig
    from google.adk.apps.app import App
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.genai import types

    root, dg = make_plugin(salt); budget = BudgetPlugin(max_input_tokens)
    app = App(name="attenu-sample-adk", root_agent=build_tree(model=model, repo=repo, artifacts=artifacts), plugins=[dg, budget])
    sessions = InMemorySessionService(); runner = Runner(app=app, session_service=sessions)
    session = await sessions.create_session(app_name="attenu-sample-adk", user_id="attenu")
    status = "ok"

    async def _consume():
        async for ev in runner.run_async(user_id="attenu", session_id=session.id, run_config=RunConfig(max_llm_calls=max_llm_calls),
                                         new_message=types.Content(role="user", parts=[types.Part.from_text(text=task)])):
            if trace is not None:
                kinds = []
                for part in (getattr(getattr(ev, "content", None), "parts", None) or []):
                    if getattr(part, "function_call", None): kinds.append(f"call({part.function_call.name})")
                    elif getattr(part, "function_response", None): kinds.append(f"resp({part.function_response.name})")
                    elif getattr(part, "text", None): kinds.append("text")
                with trace.open("a") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} {getattr(ev, 'author', '?')} {' '.join(kinds)}\n")
    try:
        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        status = f"error: Timeout: task exceeded {timeout_s:.0f}s wall clock"
    except Exception as exc:                        # noqa: BLE001 — keep sampling; record the failure (incl. BudgetExceeded)
        cause = exc.__cause__ if isinstance(exc.__cause__, BudgetExceeded) else exc
        status = f"error: {type(cause).__name__}: {str(cause)[:120]}"
    usage = {"input_tokens": budget.used, "output_tokens": budget.output, "cached_input_tokens": budget.cached, "llm_calls": budget.calls,
             "by_agent": dict(budget.by_agent), "est_cost_usd": round(estimate_cost(model, budget.used, budget.output), 4)}
    return root, dg, status, usage, budget.aborted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="path to a checked-out repository to explore (read-only tools)")
    ap.add_argument("--out", default="data", help="output root (gitignored)")
    ap.add_argument("--project", default=None, help="corpus project name (default: repo dir name)")
    ap.add_argument("--model", default="gemini-3.6-flash", help="Gemini Flash model (the API's current recommendation; 2.5-flash is closed to new keys)")
    ap.add_argument("--tasks", default=None, help="file with one task per line (default: built-in fan-out 3)")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N tasks")
    ap.add_argument("--task-index", type=int, default=None)
    ap.add_argument("--max-input-tokens", type=int, default=300_000, help="HARD per-task budget across all agents (abort)")
    ap.add_argument("--max-llm-calls", type=int, default=40, help="ADK RunConfig.max_llm_calls per task")
    ap.add_argument("--timeout-s", type=float, default=900.0, help="HARD per-task wall-clock cap (fan-out path default)")
    ap.add_argument("--trace", action="store_true")
    args = ap.parse_args(argv)

    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        print("GOOGLE_API_KEY not set — `set -a; source ~/.attenu/keys.env; set +a`", file=sys.stderr)
        return 2
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

    repo = Path(args.repo).resolve(); project = args.project or repo.name
    tasks = [t.strip() for t in Path(args.tasks).read_text().splitlines() if t.strip()] if args.tasks else FANOUT_TASKS
    if args.limit:
        tasks = tasks[: args.limit]
    if args.task_index is not None:
        tasks = [tasks[args.task_index]]
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    salt = secrets.token_hex(16)
    out = Path(args.out)
    for d in ("corpus", "mirror", f"runs/{run_id}"):
        (out / d).mkdir(parents=True, exist_ok=True)
    run_meta = {"project": project, "framework": "google-adk", "model": args.model, "seed": 0, "salt": salt,
                "versions": {"attenu-derive": AD_VERSION, "delegation-guard": DG_VERSION, "python": platform.python_version()}}

    corpus_rows, mirror_rows, per_task = [], [], []
    for i, task in enumerate(tasks):
        t0 = time.time()
        root, dg, status, usage, aborted = asyncio.run(run_task(
            task, repo=repo, model=args.model, salt=salt, artifacts=out / "runs" / run_id / f"task{i}-artifacts",
            max_input_tokens=args.max_input_tokens, max_llm_calls=args.max_llm_calls, timeout_s=args.timeout_s,
            trace=(out / "runs" / run_id / f"task{i}-trace.log") if args.trace else None))
        entries = root.audit_log().entries
        (out / "runs" / run_id / f"task{i}-audit.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")
        run_i = dict(run_meta, task_index=i)
        rows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="hash")
        mrows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="keep")
        for r, mr in zip(rows, mrows):
            if r["parent_node"] is None:                # the root's task text is the prompt (the spawn records carry the children's)
                r["task_hash"] = hashlib.sha256(f"{salt}\x1f{task}".encode()).hexdigest()[:16]
                r["task_features"] = _task_features(task); mr["task"] = task
        corpus_rows += rows; mirror_rows += mrows
        (out / "corpus" / f"{project}-adk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")   # incremental
        (out / "mirror" / f"{project}-adk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
        n_calls = sum(len(r["child_calls"]) for r in rows); n_deleg = sum(1 for r in rows if r["parent_node"] is not None)
        per_task.append({"task_index": i, "status": status, "seconds": round(time.time() - t0, 1), "aborted": aborted,
                         "delegations": n_deleg, "tool_calls": n_calls, "usage": usage, "audit_events": len(entries)})
        print(f"[task {i}] {status} | delegations={n_deleg} tool_calls={n_calls} tokens={usage['input_tokens']}+{usage['output_tokens']} "
              f"llm_calls={usage['llm_calls']} est=${usage['est_cost_usd']} in {per_task[-1]['seconds']}s")

    manifest = {"run_id": run_id, **{k: v for k, v in run_meta.items() if k != "salt"},
                "billing": "Gemini API key (Google AI Studio; free tier unless billing is enabled on the key) — est_cost_usd is notional",
                "price_basis": price_basis(args.model),
                "guardrails": {"max_input_tokens": args.max_input_tokens, "max_llm_calls": args.max_llm_calls, "timeout_s": args.timeout_s},
                "tasks": len(tasks), "task_texts": tasks, "results": per_task,
                "totals": {"delegation_events": sum(1 for r in corpus_rows if r["parent_node"] is not None), "rows": len(corpus_rows),
                           "tool_calls": sum(len(r["child_calls"]) for r in corpus_rows),
                           "input_tokens": sum(p["usage"]["input_tokens"] for p in per_task),
                           "output_tokens": sum(p["usage"]["output_tokens"] for p in per_task),
                           "est_cost_usd": round(sum(p["usage"]["est_cost_usd"] for p in per_task), 4)}}
    (out / "runs" / run_id / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
