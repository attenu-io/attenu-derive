"""
Error analysis over sampled runs (P1 deliverable, design §5a): read the manifests + corpus and
produce a failure-taxonomy report a human can act on — BEFORE any L3 work.

    python -m attenu_derive.eval.error_analysis --data data --out data/reports

Taxonomy v0 (open-coded from the first runs; extend as new modes appear):
  R1 run-cap        the agent hit the recursion/turn cap (over-exploration; loops)
  R2 over-read      an agent made > N read calls for a task that needed few (wasted authority/cost)
  R3 no-delegation  the orchestrator did the work itself although the task asked to delegate
  R4 unknown-tool   a recorded tool that the catalog cannot resolve (coverage gap)
  R5 denial         a call denied in observe mode (should be zero — indicates a harness bug)
  R6 empty-run      zero tool calls (model refused / errored before acting)
  R7 write-loop     one agent wrote > N times in a run (looping on its output; cost + authority slack)
  R8 chain-block    SHADOW MODE (T13): a recorded call the DERIVED authority would deny down the real chain;
                    cause `parent-chain` = the parent's derived authority lacks the family the child needs
                    (the parent must HOLD what it delegates), cause `proposal` = the node's own template/catalog
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from attenu_derive.catalog.coverage import load_catalog, resolve

OVER_READ_N = 40
WRITE_LOOP_N = 5


def analyse(data: Path) -> dict:
    cat = load_catalog()
    manifests = [json.loads(p.read_text()) for p in sorted((data / "runs").glob("*/manifest.json"))]
    rows = [json.loads(l) for p in sorted((data / "corpus").glob("*.jsonl")) for l in p.read_text().splitlines() if l.strip()]
    findings: list[dict] = []
    for m in manifests:
        for r in m.get("results", []):
            if "Recursion" in str(r.get("status")) or "max_turns" in str(r.get("status")):
                findings.append({"code": "R1", "run": m["run_id"], "task": r["task_index"], "framework": m["framework"],
                                 "detail": f"{r.get('tool_calls')} calls before the cap"})
            if r.get("tool_calls", 0) == 0:
                findings.append({"code": "R6", "run": m["run_id"], "task": r["task_index"], "framework": m["framework"], "detail": r.get("status")})
            if r.get("denials"):
                findings.append({"code": "R5", "run": m["run_id"], "task": r["task_index"], "framework": m["framework"], "detail": f"{r['denials']} denials"})
    for row in rows:
        reads = sum(1 for c in row["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.read")
        if row["agent"] != "orchestrator" and reads > OVER_READ_N:
            findings.append({"code": "R2", "run": row["run"].get("task_index"), "task": row["event_id"], "framework": row["framework"],
                             "detail": f"{reads} read calls"})
        if row["parent_node"] is None and not row.get("delegated_to") and any(
                (resolve(cat, c["tool"]) or {}).get("scope") == "fs.read" for c in row["child_calls"]):
            findings.append({"code": "R3", "run": row["run"].get("task_index"), "task": row["event_id"], "framework": row["framework"],
                             "detail": "orchestrator read files itself, no delegation"})
        writes = sum(1 for c in row["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.write")
        if writes > WRITE_LOOP_N:
            findings.append({"code": "R7", "run": row["run"].get("task_index"), "task": row["event_id"], "framework": row["framework"],
                             "detail": f"{writes} write calls"})
        for c in row["child_calls"]:
            if resolve(cat, c["tool"]) is None:
                findings.append({"code": "R4", "run": row["run"].get("task_index"), "task": row["event_id"], "framework": row["framework"], "detail": c["tool"]})
    # R8: shadow mode over the mirror rows (task text needed for L1) — one finding per (project, framework, scope, cause)
    try:
        from attenu_derive.eval.shadow import shadow_files
        mirror = sorted((data / "mirror").glob("*.jsonl"))
        if mirror:
            sh = shadow_files(mirror); agg = Counter()
            for run in sh["runs"].values():
                for b in run["blocks"]:
                    agg[(run["project"], run["framework"], b["scope"], b["cause"])] += 1
                # blocks are capped at 50 per run in the report; recount from totals for the detail line
            for (proj, fw, sc, cause), n in sorted(agg.items(), key=lambda kv: -kv[1]):
                findings.append({"code": "R8", "run": "shadow", "task": f"{proj} · {sc}", "framework": fw, "detail": f"{cause}: {n}+ would-be blocks"})
            findings.append({"code": "R8", "run": "shadow", "task": "ALL", "framework": "all",
                             "detail": f"{sh['total']['would_block']} of {sh['total']['calls']} recorded calls would be blocked (rate {sh['total']['would_block_rate']}); "
                                       f"cause parent-chain {sh['total'].get('cause:parent-chain', 0)}, proposal {sh['total'].get('cause:proposal', 0)}"})
    except Exception as exc:                          # noqa: BLE001 — the notebook must still render without shadow
        findings.append({"code": "R8", "run": "shadow", "task": "-", "framework": "-", "detail": f"shadow unavailable: {exc!r}"})
    by_code = Counter(f["code"] for f in findings)
    tools = Counter(c["tool"] for r in rows for c in r["child_calls"])
    per_agent = Counter(r["agent"] for r in rows)
    return {"runs": len(manifests), "tasks": sum(m.get("tasks", 0) for m in manifests), "rows": len(rows),
            "delegation_events": sum(1 for r in rows if r["parent_node"]),
            "tool_calls": sum(tools.values()), "tools": dict(tools.most_common()), "agents": dict(per_agent),
            "findings_by_code": dict(by_code), "findings": findings,
            "cost_usd_api": round(sum((m.get("totals", {}).get("cost_usd") or 0) for m in manifests), 3),
            "tokens": {"input": sum(m.get("totals", {}).get("input_tokens", 0) for m in manifests),
                       "output": sum(m.get("totals", {}).get("output_tokens", 0) for m in manifests)}}


def render(a: dict) -> str:
    lines = [f"# Error analysis — {time.strftime('%Y-%m-%d')}", "",
             f"runs={a['runs']} tasks={a['tasks']} rows={a['rows']} delegation_events={a['delegation_events']} tool_calls={a['tool_calls']}",
             f"tokens={a['tokens']} api-equivalent cost={a['cost_usd_api']} USD (Claude-SDK runs are subscription-billed)", "",
             "## Findings by code", ""]
    for code, n in sorted(a["findings_by_code"].items()):
        lines.append(f"- **{code}**: {n}")
    lines += ["", "## Tools seen", "", ", ".join(f"{t}×{n}" for t, n in a["tools"].items()), "", "## Findings", ""]
    for f in a["findings"]:
        lines.append(f"- {f['code']} · {f['framework']} · run {f['run']} · {f['task']} — {f['detail']}")
    lines += ["", "## Reading (v0)", "",
              "- R1/R2 dominate with Haiku-class models: over-exploration is the main *cost* failure, not a safety one —",
              "  the observed envelope still bounds authority correctly (read-only), but ceilings derived from these",
              "  runs would be looser than needed. Mitigation for sampling: tighter sub-agent prompts, recursion caps,",
              "  a small frontier-model slice for realism; for labels: the rubric's `over_exploration` flag.",
              "- R3 (no delegation) is a task-prompt problem in the harness, not a model failure — tighten the prompt.",
              "- R4 must stay at zero as new projects/tools arrive; it is the catalog's coverage metric.",
              "- R5 must be zero in observe mode by construction; non-zero = harness bug.",
              "- R7 (write loops) is the costliest single mode seen so far (one orchestrator rewrote its report ~100×);",
              "  a CallLimit ceiling on fs.write is a natural derived ceiling for orchestrator roles.",
              "- R8 (shadow, T13): the FIRST chain-level reading. G1 scores every row against a synthetic wide parent and",
              "  reported 0.0 benign-deny; replayed down the real chain the same proposals would block ~4 of 5 recorded",
              "  calls, almost all fs.read with cause parent-chain: rubric ruling 2 (the delegating-writer holds no read)",
              "  is UNENFORCEABLE under monotonic attenuation — a parent cannot delegate what it does not hold. Triage:",
              "  the delegating-writer must hold the read families its declared sub-agents' tools resolve to (held FOR",
              "  delegation), and the unused-scope metric + rubric must count a descendant's use as the ancestor's use",
              "  (transitive). Decision owner: PM (rubric v1.2 + templates + metric). Until then G2's '0 benign blocks'",
              "  is not evidence-able."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data"); ap.add_argument("--out", default="data/reports")
    args = ap.parse_args(argv)
    a = analyse(Path(args.data)); out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    md = render(a); (out / f"error-analysis-{time.strftime('%Y%m%d')}.md").write_text(md); print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
