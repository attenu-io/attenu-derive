"""
attenu-sample — observe-mode sampling of a Claude Agent SDK session (subagents via the
built-in `Agent` tool) over a real repository. Billed to the logged-in Claude Code
subscription — no API key. Records every subagent spawn and tool call through the
delegation-guard audit log (redacted at capture, ADR-05) and exports corpus rows.

    python -m attenu_derive.sample.run_claude_sdk --repo <path> --out data/ --model haiku

Observe mode needs no adapter change: the Claude SDK adapter accepts fnmatch policies, so a
single "*" ToolPolicy with the redacting context covers every tool; grants exist for the
sub-agents this harness declares (plus the built-in general-purpose type). The delegated
task text (the `Agent` tool's prompt) is captured by an extra hook for the local mirror.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import secrets
import sys
import time
from pathlib import Path

from delegation_guard import Authority, Guard, __version__ as DG_VERSION
from delegation_guard.adapters.claude_sdk import AgentGrant, DelegationGuardRegistry, ToolPolicy

from attenu_derive import __version__ as AD_VERSION
from attenu_derive.corpus.export import audit_to_corpus_rows
from attenu_derive.sample.features import extract_features

OBSERVE = Authority({"observe.*", "agent.delegate.*"}, [], ttl=None)

DEFAULT_TASKS = [
    "Produce a short architecture overview of this repository (main modules and how they fit) and "
    "save it to REPORT.md. Use the researcher subagent for all reading/exploration; write only the final file yourself.",
    "Find the main entry points and the public API surface users call; note anything deprecated. "
    "Use the researcher subagent to explore; write API_SURFACE.md.",
    "List the security-relevant code paths (input parsing, file/network access, auth, escaping). "
    "Use the researcher subagent to explore; write a bullet list to SECURITY_NOTES.md.",
    "Summarize how tests are organized and how to run them, from existing docs and the tests directory. "
    "Use the researcher subagent to read; write TESTING.md.",
]


SPECIALISTS = {   # T5 fan-out: one task -> several delegations (events per run x4 at the same subscription cost)
    "researcher":        ("Explores the repository: lists, greps and reads files, and reports concise findings with file paths.",
                          "You are a code researcher. Use Glob, Grep and Read to explore efficiently (few, targeted reads). Return concise findings with file paths. Do NOT write files."),
    "security-reviewer": ("Finds security-relevant code paths (input parsing, file/network access, auth, escaping, secrets).",
                          "You are a security reviewer. Use Glob, Grep and Read to locate security-relevant code paths; report each with file path and one line of risk. Do NOT write files."),
    "test-analyst":      ("Explains how tests are organized and run.",
                          "You analyse the test suite: locate tests, runners, CI config; report how to run them, with file paths. Do NOT write files."),
    "api-surveyor":      ("Maps the public API surface and deprecations.",
                          "You map the public API: entry points, exported functions/classes, deprecated items; report with file paths. Do NOT write files."),
}
FANOUT_TASKS = [
    "Produce REPORT.md for this repository with four sections: architecture, security-relevant paths, tests, public API. "
    "Delegate EACH section to its specialist subagent (researcher, security-reviewer, test-analyst, api-surveyor) via the Agent tool, "
    "one after another (foreground, not background); do all reading through them; then write REPORT.md yourself and finish.",
    "Write ONBOARDING.md for a new contributor: how the code is organized (researcher), what to be careful about security-wise "
    "(security-reviewer), how to run the tests (test-analyst), and which public APIs matter (api-surveyor). Delegate every "
    "reading task to those subagents; write only the final file yourself.",
    "Assess this repository for a security review: delegate exploration to the researcher and the security-reviewer, "
    "ask the test-analyst whether security-relevant paths are covered by tests, and the api-surveyor which public APIs "
    "expose those paths. Write SECURITY_ASSESSMENT.md yourself; do all reading through the subagents.",
]


FANOUT_TIMEOUT_S = 900.0     # PM decision 2026-08-18 (W2 T8): fan-out tasks (4 specialists) need more wall clock; fewer truncated rows
PLAIN_TIMEOUT_S = 600.0


def default_timeout_s(*, fanout: bool, explicit: float | None = None) -> float:
    """Per-task wall-clock cap: an explicit --timeout-s wins; else 900 s on the fan-out path, 600 s otherwise."""
    if explicit is not None:
        return float(explicit)
    return FANOUT_TIMEOUT_S if fanout else PLAIN_TIMEOUT_S


def foreground_delegation(input_data: dict) -> dict:
    """PreToolUse rewrite: a delegation launched with `run_in_background: true` is forced to the foreground.
    `AgentDefinition(background=False)` is only a default the model can override per call; background
    subagents make the parent's turn end early and the run's wall clock unbounded (T8 root cause)."""
    if input_data.get("tool_name") in ("Agent", "Task"):
        ti = input_data.get("tool_input") or {}
        if ti.get("run_in_background"):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                           "permissionDecisionReason": "attenu-sample: subagents run in the foreground",
                                           "updatedInput": {**ti, "run_in_background": False}}}
    return {}


def make_registry(salt: str):
    root = Guard.issue("orchestrator", OBSERVE, task="sample", max_depth=8, max_fanout=10_000)
    reg = DelegationGuardRegistry(
        root,
        agent_grants={**{name: AgentGrant(OBSERVE, task=desc) for name, (desc, _p) in SPECIALISTS.items()},
                      "general-purpose": AgentGrant(OBSERVE, task="general-purpose subagent")},
        tool_policies={"*": ToolPolicy("observe.tool", context_fn=lambda ti: extract_features(ti, salt=salt))},
    )
    return root, reg


async def run_task(task: str, *, repo: Path, model: str, salt: str, max_turns: int, budget: float, timeout_s: float = 600.0, trace: Path | None = None):
    from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher, ResultMessage, query

    root, reg = make_registry(salt)
    delegations: list[dict] = []          # (agent_type, prompt) in call order — for the local mirror

    async def capture_delegation(input_data, tool_use_id, context):
        if input_data.get("tool_name") in ("Agent", "Task"):
            ti = input_data.get("tool_input") or {}
            delegations.append({"agent_type": ti.get("subagent_type"), "task": ti.get("prompt") or ti.get("description") or "",
                                "background_requested": bool(ti.get("run_in_background"))})
        return foreground_delegation(input_data)

    hooks = reg.hooks()
    hooks["PreToolUse"] = [HookMatcher(hooks=[capture_delegation])] + hooks["PreToolUse"]

    options = ClaudeAgentOptions(
        cwd=str(repo),
        model=model,
        setting_sources=[],                              # ignore ~/.claude and the repo's .claude
        allowed_tools=["Read", "Grep", "Glob", "Write", "Agent"],
        disallowed_tools=["Bash", "WebFetch", "WebSearch", "Edit", "NotebookEdit"],
        permission_mode="acceptEdits",                   # scratch checkout; Write is the task's output
        # background=False: background subagents keep the CLI alive after the parent's ResultMessage, which a
        # headless query() experiences as an idle hang (T5 stall root cause). Foreground only.
        agents={name: AgentDefinition(description=desc, prompt=prompt, tools=["Read", "Grep", "Glob"], model=model, background=False)
                for name, (desc, prompt) in SPECIALISTS.items()},
        hooks=hooks,
        max_turns=max_turns,
        max_budget_usd=budget,
        system_prompt=("You are a senior engineer analysing a repository. Delegate exploration to the "
                       "'researcher' subagent via the Agent tool; keep your own tool use minimal; write exactly "
                       "the file the task asks for and finish."),
    )

    async def _prompt():
        yield {"type": "user", "message": {"role": "user", "content": task},
               "parent_tool_use_id": None, "session_id": "attenu-sample"}

    status, usage, cost = "ok", {}, None

    async def _consume():
        nonlocal status, usage, cost
        async for message in query(prompt=_prompt(), options=options):
            if trace is not None:                                   # diagnostic stream: message type + tool names + timestamps
                with trace.open("a") as fh:
                    kinds = []
                    for b in getattr(message, "content", []) or []:
                        n = getattr(b, "name", None); kinds.append(f"{type(b).__name__}{'(' + n + ')' if n else ''}")
                    fh.write(f"{time.strftime('%H:%M:%S')} {type(message).__name__} {getattr(message, 'subtype', '')} {' '.join(kinds)}\n")
            if isinstance(message, ResultMessage):
                status = f"result:{message.subtype}"
                usage = dict(getattr(message, "usage", None) or {})
                cost = getattr(message, "total_cost_usd", None)
    try:
        # HARD wall-clock cap per task: a stalled CLI (e.g. subscription window exhausted -> silent
        # backoff) must not hang the batch. The audit log is complete up to the stall either way.
        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        status = f"error: Timeout: task exceeded {timeout_s:.0f}s wall clock (stalled CLI?)"
    except Exception as exc:
        status = f"error: {type(exc).__name__}: {str(exc)[:120]}"
    return root, reg, delegations, status, usage, cost


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default="data")
    ap.add_argument("--project", default=None)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--fanout", action="store_true", help="use the fan-out task set (4 specialist subagents per task)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--budget-usd", type=float, default=5.0, help="SDK notional cap per task (subscription-billed; fan-out tasks need ~3-5)")
    ap.add_argument("--timeout-s", type=float, default=None, help="HARD per-task wall-clock cap (guardrail against stalled CLI); default 900 s with --fanout, else 600 s")
    ap.add_argument("--task-index", type=int, default=None, help="run only this task index from the task set")
    ap.add_argument("--trace", action="store_true", help="write a message-stream trace per task to the run dir (diagnostics)")
    args = ap.parse_args(argv)
    args.timeout_s = default_timeout_s(fanout=args.fanout, explicit=args.timeout_s)

    repo = Path(args.repo).resolve(); project = args.project or repo.name
    tasks = [t.strip() for t in Path(args.tasks).read_text().splitlines() if t.strip()] if args.tasks else (FANOUT_TASKS if args.fanout else DEFAULT_TASKS)
    if args.limit:
        tasks = tasks[: args.limit]
    if args.task_index is not None:
        tasks = [tasks[args.task_index]]
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    salt = secrets.token_hex(16)
    out = Path(args.out)
    for d in ("corpus", "mirror", f"runs/{run_id}"):
        (out / d).mkdir(parents=True, exist_ok=True)
    run_meta = {"project": project, "framework": "claude-agent-sdk", "model": args.model, "seed": 0, "salt": salt,
                "versions": {"attenu-derive": AD_VERSION, "delegation-guard": DG_VERSION, "python": platform.python_version()}}

    corpus_rows, mirror_rows, per_task = [], [], []
    for i, task in enumerate(tasks):
        t0 = time.time()
        root, reg, delegations, status, usage, cost = asyncio.run(
            run_task(task, repo=repo, model=args.model, salt=salt, max_turns=args.max_turns, budget=args.budget_usd, timeout_s=args.timeout_s,
                     trace=(out / "runs" / run_id / f"task{i}-trace.log") if args.trace else None))
        entries = root.audit_log().entries
        (out / "runs" / run_id / f"task{i}-audit.jsonl").write_text(
            "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")
        run_i = dict(run_meta, task_index=i)
        rows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="hash")
        mrows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="keep")
        # attach the delegated prompts (FIFO per agent type, same heuristic as the adapter)
        pending = {}
        for d in delegations:
            pending.setdefault(d["agent_type"], []).append(d["task"])
        for r, mr in zip(rows, mrows):
            if r["parent_node"] is None:
                r["task_hash"] = hashlib.sha256(f"{salt}\x1f{task}".encode()).hexdigest()[:16]; mr["task"] = task
            else:
                q = pending.get(r["agent"]) or []
                text = q.pop(0) if q else ""
                r["task_hash"] = hashlib.sha256(f"{salt}\x1f{text}".encode()).hexdigest()[:16]
                from attenu_derive.corpus.export import _task_features
                r["task_features"] = _task_features(text); mr["task"] = text
        corpus_rows += rows; mirror_rows += mrows
        # incremental export: a killed/stalled batch still leaves rows on disk
        (out / "corpus" / f"{project}-claude_sdk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")
        (out / "mirror" / f"{project}-claude_sdk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
        n_calls = sum(len(r["child_calls"]) for r in rows); n_deleg = sum(1 for r in rows if r["parent_node"])
        per_task.append({"task_index": i, "status": status, "seconds": round(time.time() - t0, 1),
                         "delegations": n_deleg, "tool_calls": n_calls, "usage": usage, "cost_usd": cost,
                         "audit_events": len(entries), "denials": len(reg.denials),
                         "background_requested": sum(1 for d in delegations if d.get("background_requested"))})   # how often the model asked for background subagents (rewritten to foreground)
        print(f"[task {i}] {status} | delegations={n_deleg} tool_calls={n_calls} denials={len(reg.denials)} "
              f"cost={cost} in {per_task[-1]['seconds']}s")

    (out / "corpus" / f"{project}-claude_sdk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")
    (out / "mirror" / f"{project}-claude_sdk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
    manifest = {"run_id": run_id, **{k: v for k, v in run_meta.items() if k != "salt"},
                "billing": "subscription (Claude Code); cost_usd is the SDK's notional API-equivalent incl. subagents",
                "guardrails": {"max_turns": args.max_turns, "max_budget_usd": args.budget_usd, "timeout_s": args.timeout_s},
                "tasks": len(tasks), "task_texts": tasks, "results": per_task,
                "totals": {"delegation_events": sum(1 for r in corpus_rows if r["parent_node"]), "rows": len(corpus_rows),
                           "tool_calls": sum(len(r["child_calls"]) for r in corpus_rows),
                           "cost_usd": sum((p["cost_usd"] or 0) for p in per_task)}}
    (out / "runs" / run_id / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
