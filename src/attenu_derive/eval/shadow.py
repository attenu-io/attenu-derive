"""
Shadow mode (P2, T13) — apply DERIVED authority to a sampled project's own workload in would-deny mode:
replay the recorded delegation chain through the deriver, check every recorded tool call against the
authority the node WOULD have been granted, count what would have been blocked, block nothing.

    python -m attenu_derive.eval.shadow --run <run_id> [--run ...]      # data/mirror/*-<run_id>.jsonl (task text needed for L1)
    python -m attenu_derive.eval.shadow --all                          # every mirror file, grouped by project

Chain semantics are REAL here, unlike G1: the root is derived against the observe parent (an operator
issuing "whatever the workload needs"), and every child is derived against its PARENT'S DERIVED
authority — `granted = meet(parent_derived, child_proposal)` — exactly what enforce mode does. So a
would-be block has one of two causes, and the report says which:
  - `proposal`     the node's own template/catalog proposal does not permit the call (G1's benign-deny), or
  - `parent-chain` the node's proposal permits it but the parent's derived authority lacks the family —
                   monotonic attenuation makes the child ⊆ parent; the parent must HOLD what it delegates.
G2's criterion is "0 benign blocks across the projects' own example workloads": every recorded call in an
observe run is the workload (nothing was denied at capture); a would-deny IS a would-be benign block.
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from attenu_derive.catalog.coverage import load_catalog, resolve
from attenu_derive.derive.propose import Deriver, event_from_row, spec_to_authority
from attenu_derive.eval.g1 import OBSERVE_PARENT, _ctx_for

__all__ = ["shadow", "shadow_files"]


def _scope_of(cat: dict, tool: str, row: dict) -> str:
    e = resolve(cat, tool) or {}
    sc = str(e.get("scope") or f"unknown.{tool}")
    if sc == "agent.delegate":
        sc = f"agent.delegate.{(row.get('delegated_to') or ['researcher'])[0]}"
    return sc


def shadow(rows: list[dict], deriver: Deriver | None = None, cat: dict | None = None, negatives_by_node: dict[str, set] | None = None) -> dict:
    """rows = the mirror rows of ONE run (root first, spawns in seq order; task text present).
    negatives_by_node = {node: {tool,...}} from the rubric (gold): calls the label marks as over-reach. A block on one of
    those is `blocked_overreach` (a block we WANT), not a would-be benign block; without the join every block counts as benign."""
    deriver = deriver or Deriver(); cat = cat or load_catalog(); negatives_by_node = negatives_by_node or {}
    derived: dict[str, object] = {}; proposals: dict[str, object] = {}; recs: dict[str, object] = {}
    blocks: list[dict] = []; overreach: list[dict] = []; calls = 0; by_layer = Counter()
    for r in rows:                                   # file order = spawn order: parents precede children
        node = r["node"]; parent = r.get("parent_node")
        ev = event_from_row(r, task_text=r.get("task") or "")
        ev = replace(ev, parent_authority=derived[parent] if parent in derived else OBSERVE_PARENT)   # REAL chain
        granted, rec = deriver.propose(ev)
        derived[node] = granted; recs[node] = rec; by_layer[rec.layer] += 1
        proposal = spec_to_authority(rec.spec)       # the node's own proposal, before the meet
        proposals[node] = proposal
        qmax = (r.get("observed_envelope") or {}).get("quantities_max") or {}
        for c in r.get("child_calls", []):
            if c.get("outcome") == "deny":
                continue                             # a real denial at capture is not part of the benign workload
            tool = c["tool"]; e = resolve(cat, tool) or {}
            sc = _scope_of(cat, tool, r)
            if sc == "state.write":
                continue                             # internal scratch state, never authority
            calls += 1
            ctx = _ctx_for(tool, e, qmax)
            ok = granted.permits(sc, ctx)
            if ok:
                continue
            own = proposal.permits(sc, ctx)
            entry = {"node": node, "agent": r.get("agent"), "parent": parent, "tool": tool, "scope": sc,
                     "reasons": [x.code for x in ok.reasons], "cause": "parent-chain" if own else "proposal",
                     "layer": rec.layer, "template": rec.template,
                     "parent_scopes": sorted(derived[parent].scopes) if parent in derived else None}
            (overreach if tool in negatives_by_node.get(node, set()) else blocks).append(entry)
    return {"nodes": len(rows), "calls": calls, "would_block": len(blocks), "blocked_overreach": len(overreach),
            "would_block_rate": round(len(blocks) / calls, 4) if calls else None,
            "by_scope": dict(Counter(b["scope"] for b in blocks)), "by_cause": dict(Counter(b["cause"] for b in blocks)),
            "by_layer": dict(by_layer), "by_agent": dict(Counter(b["agent"] for b in blocks)),
            "derived": {n: {"scopes": sorted(a.scopes), "layer": recs[n].layer, "template": recs[n].template} for n, a in derived.items()},
            "blocks": blocks, "overreach": overreach}


def _rows_of(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _gold_negatives() -> dict[tuple, set]:
    """(run_key, node) -> negatives from the committed gold (rubric): the over-reach the label does not admit."""
    from attenu_derive.eval.g1 import GOLD
    out: dict[tuple, set] = {}
    if GOLD.exists():
        for l in GOLD.read_text().splitlines():
            if l.strip():
                g = json.loads(l)
                if g.get("negatives"): out[(g.get("run_key"), g.get("node"))] = set(g["negatives"])
    return out


def _run_key_of(path: Path, row: dict) -> str:
    stem = path.stem.split("-"); run_id = stem[-2] + "-" + stem[-1]              # <project>-<tag>-<YYYYMMDDTHHMMSS>-<hex>
    return f"{run_id}:{(row.get('run') or {}).get('task_index')}"


def shadow_files(paths: list[Path]) -> dict:
    """One shadow per mirror FILE (= one run), then aggregated per project and overall. Joins the gold's negatives so a
    block on rubric-marked over-reach counts as blocked_overreach, not as a would-be benign block."""
    d = Deriver(); cat = load_catalog(); per_run = {}; per_project = defaultdict(lambda: Counter()); negs = _gold_negatives()
    for p in paths:
        rows = _rows_of(p)
        if not rows:
            continue
        neg_by_node = {r["node"]: negs[(_run_key_of(p, r), r["node"])] for r in rows if (_run_key_of(p, r), r["node"]) in negs}
        rep = shadow(rows, d, cat, negatives_by_node=neg_by_node); project = rows[0].get("project"); fw = rows[0].get("framework")
        per_run[p.stem] = {"project": project, "framework": fw, **{k: rep[k] for k in ("nodes", "calls", "would_block", "blocked_overreach", "would_block_rate", "by_scope", "by_cause", "by_layer", "by_agent")},
                           "blocks": rep["blocks"][:50], "overreach": rep["overreach"][:20]}
        c = per_project[project]; c["nodes"] += rep["nodes"]; c["calls"] += rep["calls"]; c["would_block"] += rep["would_block"]; c["blocked_overreach"] += rep["blocked_overreach"]
        for k, v in rep["by_cause"].items(): c[f"cause:{k}"] += v
        for k, v in rep["by_scope"].items(): c[f"scope:{k}"] += v
    total = Counter()
    for c in per_project.values():
        total.update(c)
    return {"date": time.strftime("%Y-%m-%d"), "runs": per_run,
            "per_project": {k: dict(v, would_block_rate=round(v["would_block"] / v["calls"], 4) if v["calls"] else None) for k, v in per_project.items()},
            "total": dict(total, would_block_rate=round(total["would_block"] / total["calls"], 4) if total["calls"] else None)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", default=[], help="run id (suffix of a data/mirror file); repeatable")
    ap.add_argument("--all", action="store_true", help="every mirror file (real runs only, not datasets)")
    ap.add_argument("--mirror-dir", default="data/mirror"); ap.add_argument("--out", default="data/reports")
    args = ap.parse_args(argv)
    paths = sorted(Path(p) for p in glob.glob(f"{args.mirror_dir}/*.jsonl")) if args.all else \
        sorted(Path(p) for rid in args.run for p in glob.glob(f"{args.mirror_dir}/*-{rid}.jsonl"))
    if not paths:
        print("no mirror files matched"); return 2
    rep = shadow_files(paths)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    (out / f"shadow-{stamp}.json").write_text(json.dumps(rep, indent=2))
    md = [f"# Shadow report — {rep['date']} (would-deny mode; derived authority down the REAL chain; nothing blocked)", "",
          "| project | nodes | calls | would-be benign blocks | rate | blocked over-reach | by cause | by scope |", "|---|---|---|---|---|---|---|---|"]
    for proj, c in sorted(rep["per_project"].items()):
        cause = ", ".join(f"{k[6:]}={v}" for k, v in c.items() if k.startswith("cause:")) or "—"
        scope = ", ".join(f"{k[6:]}={v}" for k, v in c.items() if k.startswith("scope:")) or "—"
        md.append(f"| {proj} | {c['nodes']} | {c['calls']} | {c['would_block']} | {c['would_block_rate']} | {c.get('blocked_overreach', 0)} | {cause} | {scope} |")
    t = rep["total"]; md.append(f"| **all** | {t['nodes']} | {t['calls']} | {t['would_block']} | {t['would_block_rate']} | {t.get('blocked_overreach', 0)} | | |")
    (out / f"shadow-{stamp}.md").write_text("\n".join(md) + "\n")
    print(json.dumps({"per_project": rep["per_project"], "total": rep["total"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
