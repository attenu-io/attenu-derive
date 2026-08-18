"""
attenu-sample — observe-mode sampling of a deepagents (LangChain) deep agent over a real
repository, with a real model. Records every delegation and tool call through the
delegation-guard audit log (redacted at capture, ADR-05) and exports corpus rows.

    python -m attenu_derive.sample.run_deepagents --repo <path> --out data/ \
        --model claude-haiku-4-5-20251001 --tasks tasks/repo-research.txt

Design (see ../../../01-build-and-training-design.md §4, ADR-05):
- root Guard holds `observe.*` (everything allowed) — sampling never enforces;
- the LangChain adapter's observe hooks (`default_policy`, `default_subagent_authority`)
  turn EVERY tool call / sub-agent spawn into an audit event with a redacted context;
- corpus rows: `data/corpus/*.jsonl` (task text hashed) — shippable;
  local mirror: `data/mirror/*.jsonl` (task text kept, for gold review only) — never shipped;
- run manifest with model, versions, seed, token usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import sys
import time
from pathlib import Path

from delegation_guard import Authority, Guard, __version__ as DG_VERSION
from delegation_guard.adapters.langchain import GuardedDelegation, ToolPolicy

from attenu_derive import __version__ as AD_VERSION
from attenu_derive.corpus.export import audit_to_corpus_rows
from attenu_derive.sample.features import extract_features

DEFAULT_TASKS = [
    "Produce a short architecture overview of this repository (main modules and how they fit) "
    "and save it to REPORT.md. Delegate the reading and exploration to the researcher sub-agent "
    "and only write the final file yourself.",
    "Find how HTTP adapters, connection pooling and retries are implemented. Cite the exact files "
    "and functions. Use the researcher sub-agent for exploration; write findings to ADAPTERS.md.",
    "List the security-relevant code paths (TLS verification, redirects, auth handling, proxies). "
    "Delegate exploration to the researcher; write a bullet list to SECURITY_NOTES.md.",
    "Identify the public API surface (top-level functions/classes users call) and anything "
    "deprecated. Delegate to the researcher; write API_SURFACE.md.",
    "Summarize how tests are organized and how to run them, based on existing docs and the tests "
    "directory. Delegate reading to the researcher; write TESTING.md.",
]

RESEARCHER = {
    "name": "researcher",
    "description": "Explores the repository: lists, greps and reads files, and reports back concise findings with file paths.",
    "system_prompt": ("You are a code researcher. Use ls, glob, grep and read_file to explore the repository "
                      "efficiently (few, targeted reads). Return concise findings with file paths and line "
                      "references. Do NOT write files."),
}
# T14 (2026-08-18): fan-out specialists — one task -> 3-5 delegations, so the paid path's USD/event drops ~4x.
SPECIALISTS = [
    RESEARCHER,
    {"name": "security-reviewer", "description": "Finds security-relevant code paths (input parsing, file/network access, auth, escaping, secrets).",
     "system_prompt": "You are a security reviewer. Use ls, glob, grep and read_file (few, targeted reads) to locate security-relevant code paths; report each with file path and one line of risk. Do NOT write files."},
    {"name": "test-analyst", "description": "Explains how tests are organized and run.",
     "system_prompt": "You analyse the test suite: locate tests, runners, CI config with ls/glob/grep/read_file (few, targeted reads); report how to run them, with file paths. Do NOT write files."},
    {"name": "api-surveyor", "description": "Maps the public API / CLI surface and deprecations.",
     "system_prompt": "You map the public API or CLI surface: entry points, exported functions/commands, deprecated items; use ls/glob/grep/read_file (few, targeted reads); report with file paths. Do NOT write files."},
]
FANOUT_TASKS = [
    "Produce REPORT.md for this repository with four sections: architecture, security-relevant paths, tests, public API. "
    "Delegate EACH section to its specialist sub-agent via the task tool (researcher, security-reviewer, test-analyst, api-surveyor), "
    "one after another; do all reading through them; then write REPORT.md yourself with write_file and finish.",
    "Write ONBOARDING.md for a new contributor: how the code is organized (researcher), what to be careful about security-wise "
    "(security-reviewer), how to run the tests (test-analyst), and which public APIs or commands matter (api-surveyor). "
    "Delegate every reading task to those sub-agents via the task tool; write only the final file yourself.",
    "Assess this repository for a security review: delegate exploration to the researcher and the security-reviewer, ask the "
    "test-analyst whether security-relevant paths are covered by tests, and the api-surveyor which public APIs expose those paths. "
    "Write SECURITY_ASSESSMENT.md yourself; do all reading through the sub-agents.",
]


def build_agent(*, model, repo: Path, guarded: GuardedDelegation, fanout: bool = False):
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    mw = guarded.middleware()
    subagents = [dict(sa, model=model, middleware=[mw]) for sa in (SPECIALISTS if fanout else [RESEARCHER])]
    who = "the specialist sub-agents (researcher, security-reviewer, test-analyst, api-surveyor)" if fanout else "the researcher sub-agent"
    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=(f"You are a senior engineer analysing a repository. Delegate exploration to {who} "
                       "via the task tool; keep your own tool use minimal; write exactly the file the task asks for and finish."),
        middleware=[mw],
        subagents=subagents,
        backend=FilesystemBackend(root_dir=str(repo), virtual_mode=True),
    )


def make_guarded(salt: str) -> tuple[Guard, GuardedDelegation]:
    root = Guard.issue("orchestrator", Authority({"observe.*"}, [], ttl=None), task="sample",
                       max_depth=8, max_fanout=10_000)
    observe = lambda name: ToolPolicy(f"observe.{name}", lambda args: extract_features(args, salt=salt))  # noqa: E731
    guarded = GuardedDelegation(
        root, tools={}, subagents={},
        default_policy=observe,
        default_subagent_authority=lambda name: Authority({"observe.*"}, [], ttl=None),
        delegation_tool="task", subagent_arg="subagent_type", task_arg="description",
        on_deny="tool_error",
    )
    return root, guarded


# Public list prices (USD per 1M tokens) for the models we sample with — for HONEST run manifests
# and budget guardrails; update when prices change. Unknown model -> conservative Sonnet-class.
_PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-5": (3.0, 15.0), "claude-sonnet-4": (3.0, 15.0), "claude-opus": (15.0, 75.0)}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = next((v for k, v in _PRICES.items() if model.startswith(k)), (3.0, 15.0))
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout


class BudgetExceeded(RuntimeError):
    pass


class _BudgetGuard:
    """LangChain callback that ABORTS the run when cumulative input tokens pass the budget —
    a looping agent re-sends its whole transcript every step, so a per-task hard cap is the
    only reliable cost guardrail (recursion limits alone let 100+ steps × growing context through)."""
    raise_error = True          # make LangChain propagate our exception instead of logging it
    ignore_llm = False; ignore_chain = True; ignore_agent = True; ignore_retriever = True; ignore_chat_model = False
    ignore_retry = True; ignore_custom_event = True

    def __init__(self, max_input_tokens: int):
        self.max = max_input_tokens; self.used = 0

    def on_llm_end(self, response, **kwargs):
        for gen_list in getattr(response, "generations", []) or []:
            for gen in gen_list:
                msg = getattr(gen, "message", None); um = getattr(msg, "usage_metadata", None) or {}
                self.used += int(um.get("input_tokens", 0) or 0)
        if self.used > self.max:
            raise BudgetExceeded(f"input-token budget exceeded: {self.used} > {self.max}")

    def __getattr__(self, name):        # every other callback hook is a no-op
        if name.startswith("on_"):
            return lambda *a, **k: None
        raise AttributeError(name)


def usage_of(out: dict) -> dict:
    tot = {"input_tokens": 0, "output_tokens": 0}
    for m in out.get("messages", []):
        u = getattr(m, "usage_metadata", None) or {}
        tot["input_tokens"] += int(u.get("input_tokens", 0) or 0)
        tot["output_tokens"] += int(u.get("output_tokens", 0) or 0)
    return tot


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="path to a checked-out repository to explore (read via a virtual FS)")
    ap.add_argument("--out", default="data", help="output root (gitignored)")
    ap.add_argument("--project", default=None, help="corpus project name (default: repo dir name)")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--tasks", default=None, help="file with one task per line (default: built-in 5, or the fan-out 3 with --fanout)")
    ap.add_argument("--fanout", action="store_true", help="fan-out workload: 4 specialist sub-agents, one task -> 3-5 delegations (T14)")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N tasks")
    ap.add_argument("--recursion-limit", type=int, default=40)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--max-input-tokens", type=int, default=300_000,
                    help="HARD per-task budget: abort the run once cumulative input tokens exceed this (cost guardrail)")
    args = ap.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — `set -a; source ~/.attenu/keys.env; set +a`", file=sys.stderr)
        return 2
    from langchain_anthropic import ChatAnthropic
    # Prompt caching (top-level cache_control = automatic prefix caching on the direct Anthropic API):
    # a looping agent re-sends its growing transcript every step; cache reads bill at ~10%.
    model = ChatAnthropic(model=args.model, max_tokens=args.max_tokens, temperature=0,
                          model_kwargs={"cache_control": {"type": "ephemeral"}})

    repo = Path(args.repo).resolve()
    project = args.project or repo.name
    tasks = [t.strip() for t in Path(args.tasks).read_text().splitlines() if t.strip()] if args.tasks else (FANOUT_TASKS if args.fanout else DEFAULT_TASKS)
    if args.limit:
        tasks = tasks[: args.limit]

    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    salt = secrets.token_hex(16)
    out = Path(args.out); (out / "corpus").mkdir(parents=True, exist_ok=True)
    (out / "mirror").mkdir(exist_ok=True); (out / "runs" / run_id).mkdir(parents=True, exist_ok=True)
    run_meta = {"project": project, "framework": "langchain/deepagents", "model": args.model, "seed": 0,
                "salt": salt, "versions": {"attenu-derive": AD_VERSION, "delegation-guard": DG_VERSION,
                                            "python": platform.python_version()}}

    corpus_rows, mirror_rows, per_task = [], [], []
    for i, task in enumerate(tasks):
        root, guarded = make_guarded(salt)
        agent = build_agent(model=model, repo=repo, guarded=guarded, fanout=args.fanout)
        t0 = time.time(); status = "ok"
        # Usage via callback, so it is captured even when the run ends in an exception
        # (e.g. GraphRecursionError) — the audit log is complete either way.
        from langchain_core.callbacks import UsageMetadataCallbackHandler
        cb = UsageMetadataCallbackHandler()
        guard_cb = _BudgetGuard(args.max_input_tokens)
        try:
            agent.invoke({"messages": [("user", task)]},
                         config={"recursion_limit": args.recursion_limit, "callbacks": [cb, guard_cb]})
        except Exception as exc:  # keep sampling; record the failure (incl. BudgetExceeded)
            status = f"error: {type(exc).__name__}: {str(exc)[:120]}"
        usage = {"input_tokens": 0, "output_tokens": 0}
        for m in (cb.usage_metadata or {}).values():
            usage["input_tokens"] += int(m.get("input_tokens", 0) or 0)
            usage["output_tokens"] += int(m.get("output_tokens", 0) or 0)
        usage["est_cost_usd"] = round(estimate_cost(args.model, usage["input_tokens"], usage["output_tokens"]), 4)
        entries = root.audit_log().entries
        (out / "runs" / run_id / f"task{i}-audit.jsonl").write_text(
            "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")
        run_i = dict(run_meta, task_index=i)
        rows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="hash")
        mrows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="keep")
        for r in rows:  # tag the root's task (root event has no task text) via features from the prompt
            if r["parent_node"] is None:
                r["task_hash"] = hashlib.sha256(f"{salt}\x1f{task}".encode()).hexdigest()[:16]
        for r in mrows:
            if r["parent_node"] is None:
                r["task"] = task
        corpus_rows += rows; mirror_rows += mrows
        (out / "corpus" / f"{project}-deepagents-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")   # incremental
        (out / "mirror" / f"{project}-deepagents-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
        n_calls = sum(len(r["child_calls"]) for r in rows)
        n_deleg = sum(1 for r in rows if r["parent_node"] is not None)
        per_task.append({"task_index": i, "status": status, "seconds": round(time.time() - t0, 1),
                         "aborted": status.startswith("error: BudgetExceeded"),
                         "delegations": n_deleg, "tool_calls": n_calls, "usage": usage,
                         "audit_events": len(entries)})
        print(f"[task {i}] {status} | delegations={n_deleg} tool_calls={n_calls} "
              f"tokens={usage.get('input_tokens',0)}+{usage.get('output_tokens',0)} in {per_task[-1]['seconds']}s")

    (out / "corpus" / f"{project}-deepagents-{run_id}.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")
    (out / "mirror" / f"{project}-deepagents-{run_id}.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
    manifest = {"run_id": run_id, **{k: v for k, v in run_meta.items() if k != "salt"},
                "billing": "api", "workload": "fanout" if args.fanout else "single-researcher",
                "guardrails": {"max_input_tokens": args.max_input_tokens, "recursion_limit": args.recursion_limit, "prompt_caching": True},
                "tasks": len(tasks), "task_texts": tasks, "results": per_task,
                "totals": {"delegation_events": sum(1 for r in corpus_rows if r["parent_node"] is not None),
                           "rows": len(corpus_rows),
                           "tool_calls": sum(len(r["child_calls"]) for r in corpus_rows),
                           "input_tokens": sum(p["usage"].get("input_tokens", 0) for p in per_task),
                           "output_tokens": sum(p["usage"].get("output_tokens", 0) for p in per_task),
                           "est_cost_usd": round(sum(p["usage"].get("est_cost_usd", 0) for p in per_task), 4)}}
    (out / "runs" / run_id / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
