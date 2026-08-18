"""Generate gold-v0 labels from the local mirror under rubric v0 (observed envelope + catalog
mapping; over-exploration flagged, not denied). Re-run after every sampling batch:
    python -m attenu_derive.corpus.gold_v0            # writes corpus/gold/gold-v0.jsonl, prints a table
"""
from __future__ import annotations
import glob, json, sys
from pathlib import Path
from attenu_derive.catalog.coverage import load_catalog, resolve

QUP = {"0": 0, "1": 1, "2-10": 10, "11-100": 100, "101-1k": 1000, "1k-10k": 10_000, "10k-100k": 100_000, "100k-1M": 1_000_000, "1M+": None}
GOLD = Path(__file__).with_name("gold") / "gold-v0.jsonl"


def label_row(r: dict, cat: dict) -> dict:
    env = r["observed_envelope"]; scopes = set()
    for t in env["tools"]:
        sc = (resolve(cat, t) or {}).get("scope", f"unknown.{t}")
        if sc != "agent.delegate":
            scopes.add(sc)
    for child in r.get("delegated_to", []):
        scopes.add(f"agent.delegate.{child}")
    rows_max = None
    for t in env["tools"]:
        for arg, dim in ((resolve(cat, t) or {}).get("consumes") or {}).items():
            if dim == "max_rows" and arg in env["quantities_max"]:
                up = QUP[env["quantities_max"][arg]]; rows_max = max(rows_max or 0, up or 10**9)
    ceilings = ([{"type": "RowLimit", "max": rows_max}] if rows_max else []) + \
               ([{"type": "EgressRank", "level": "none"}] if "fs.read" in scopes and not scopes & {"web.fetch", "mail.send", "crm.export"} else [])
    reads = sum(1 for c in r["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.read")
    writes = sum(1 for c in r["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.write")
    over = []
    root = r["parent_node"] is None
    if not root and reads > 40: over.append(f"repeated broad reads ({reads})")
    if root and writes > 5: over.append(f"write loop ({writes} writes)")
    if root and reads: over.append(f"read directly instead of delegating ({reads})")
    return {"event_id": r["event_id"], "project": r["project"], "framework": r["framework"], "agent": r["agent"], "role": "orchestrator" if root else "subagent",
            "task": r.get("task", ""), "observed_envelope": env, "delegated_to": r.get("delegated_to", []),
            "label": {"scopes": sorted(scopes), "ceilings": ceilings, "ttl_bucket_s": 900},
            "negatives": r.get("negatives", []), "over_exploration": over,
            "confidence": "high" if not root else "med",
            "rationale": ("subagent: read-only exploration; RowLimit from observed read limits; egress none" if not root
                          else "orchestrator: the requested write + the delegation it was asked to make"),
            "reviewer": "session-2026-08-18", "rubric_version": 0}


def main(argv=None) -> int:
    cat = load_catalog(); gold = []
    for f in sorted(glob.glob("data/mirror/*.jsonl")):
        for line in Path(f).read_text().splitlines():
            if line.strip():
                gold.append(label_row(json.loads(line), cat))
    GOLD.write_text("\n".join(json.dumps(g, sort_keys=True) for g in gold) + "\n")
    print("| # | project / fw | agent | task (truncated) | observed tools · quantities | label scopes | ceilings | over-exploration |")
    print("|---|---|---|---|---|---|---|---|")
    for i, g in enumerate(gold, 1):
        fw = g["framework"].replace("langchain/", "").replace("claude-agent-sdk", "claude-sdk")
        task = (g.get("task") or "").replace("|", "/").replace("\n", " "); task = task[:55] + ("…" if len(task) > 55 else "")
        env = g["observed_envelope"]; q = env["quantities_max"]
        ceils = "; ".join(f"{c['type']}({c.get('max', c.get('level'))})" for c in g["label"]["ceilings"]) or "—"
        print(f"| {i} | {g['project']} / {fw} | {g['agent']} | {task} | {', '.join(env['tools'])}{(' · q=' + json.dumps(q)) if q else ''} | {', '.join(g['label']['scopes'])} | {ceils} | {', '.join(g['over_exploration']) or '—'} |")
    print(f"\ngold items: {len(gold)} -> {GOLD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
