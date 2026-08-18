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


def build_agent(*, model, repo: Path, guarded: GuardedDelegation):
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    mw = guarded.middleware()
    subagents = [dict(RESEARCHER, model=model, middleware=[mw])]
    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=("You are a senior engineer analysing a repository. Delegate exploration to the "
                       "researcher sub-agent via the task tool; keep your own tool use minimal; write "
                       "exactly the file the task asks for and finish."),
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
    ap.add_argument("--tasks", default=None, help="file with one task per line (default: built-in 5)")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N tasks")
    ap.add_argument("--recursion-limit", type=int, default=80)
    ap.add_argument("--max-tokens", type=int, default=2048)
    args = ap.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — `set -a; source ~/.attenu/keys.env; set +a`", file=sys.stderr)
        return 2
    from langchain_anthropic import ChatAnthropic
    model = ChatAnthropic(model=args.model, max_tokens=args.max_tokens, temperature=0)

    repo = Path(args.repo).resolve()
    project = args.project or repo.name
    tasks = [t.strip() for t in Path(args.tasks).read_text().splitlines() if t.strip()] if args.tasks else DEFAULT_TASKS
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
        agent = build_agent(model=model, repo=repo, guarded=guarded)
        t0 = time.time(); status = "ok"; usage = {}
        try:
            result = agent.invoke({"messages": [("user", task)]}, config={"recursion_limit": args.recursion_limit})
            usage = usage_of(result)
        except Exception as exc:  # keep sampling; record the failure
            status = f"error: {type(exc).__name__}: {str(exc)[:200]}"
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
        n_calls = sum(len(r["child_calls"]) for r in rows)
        n_deleg = sum(1 for r in rows if r["parent_node"] is not None)
        per_task.append({"task_index": i, "status": status, "seconds": round(time.time() - t0, 1),
                         "delegations": n_deleg, "tool_calls": n_calls, "usage": usage,
                         "audit_events": len(entries)})
        print(f"[task {i}] {status} | delegations={n_deleg} tool_calls={n_calls} "
              f"tokens={usage.get('input_tokens',0)}+{usage.get('output_tokens',0)} in {per_task[-1]['seconds']}s")

    (out / "corpus" / f"{project}-deepagents-{run_id}.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")
    (out / "mirror" / f"{project}-deepagents-{run_id}.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
    manifest = {"run_id": run_id, **{k: v for k, v in run_meta.items() if k != "salt"},
                "tasks": len(tasks), "results": per_task,
                "totals": {"delegation_events": sum(1 for r in corpus_rows if r["parent_node"] is not None),
                           "rows": len(corpus_rows),
                           "tool_calls": sum(len(r["child_calls"]) for r in corpus_rows),
                           "input_tokens": sum(p["usage"].get("input_tokens", 0) for p in per_task),
                           "output_tokens": sum(p["usage"].get("output_tokens", 0) for p in per_task)}}
    (out / "runs" / run_id / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
