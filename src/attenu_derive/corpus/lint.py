"""
Corpus / gold field lint (PM standing rule, 2026-08-18): a long-running sampler freezes the code it started with,
so every schema or semantic addition needs a check that FINDS the rows missing the new field — self-detecting,
not remembered. Three additions have now shipped this way (`completed`, the roster, `role_constraints`); this
lint would have caught all three.

    python -m attenu_derive.corpus.lint                 # lint the committed gold (what CI has)
    python -m attenu_derive.corpus.lint --data data     # lint the local corpus + mirror too

Returns a list of violations (empty = clean). Extend EXPECTED as the schema grows.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

# our own harness specialists (the frameworks where a sub-agent is DECLARED read-only)
_SPECIALISTS = {"researcher", "security-reviewer", "security_reviewer", "test-analyst", "test_analyst", "api-surveyor", "api_surveyor"}
_OUR_FRAMEWORKS = {"langchain/deepagents", "claude-agent-sdk", "google-adk", "crewai"}


def violations(rows: list[dict], *, source: str = "") -> list[dict]:
    out = []

    def bad(r, field, why):
        out.append({"source": source, "event_id": r.get("event_id"), "agent": r.get("agent"), "field": field, "why": why})

    for r in rows:
        if r.get("source") == "dataset" or r.get("label_provenance") == "dataset":
            continue                                             # public datasets have their own schema
        root = r.get("parent_node") is None
        # every real-corpus/gold row: node identity present
        for f in ("event_id", "project", "framework", "node"):
            if r.get(f) in (None, ""):
                bad(r, f, "missing")
        # per-node lifecycle (T21 leak 2): present as a bool
        if "completed" not in r:
            bad(r, "completed", "missing (Guard.complete lifecycle not recorded — stale sampler?)")
        # roots: a declared roster (may be empty), never absent
        if root and r.get("declared_subagents") is None:
            bad(r, "declared_subagents", "root missing declared roster")
        # our declared-read-only specialists: role_constraints.no_write
        if (not root) and r.get("agent") in _SPECIALISTS and r.get("framework") in _OUR_FRAMEWORKS:
            rc = r.get("role_constraints") or {}
            if not rc.get("no_write"):
                bad(r, "role_constraints.no_write", "declared read-only specialist without the no_write constraint")
    return out


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(Path(__file__).with_name("gold") / "gold-v1.2.jsonl"))
    ap.add_argument("--data", default=None, help="also lint data/corpus + data/mirror under this root")
    args = ap.parse_args(argv)
    v = []
    gp = Path(args.gold)
    if gp.exists():
        v += violations(_load(gp), source=gp.name)
    if args.data:
        for f in glob.glob(f"{args.data}/corpus/*.jsonl") + glob.glob(f"{args.data}/mirror/*.jsonl"):
            v += violations(_load(Path(f)), source=Path(f).name)
    from collections import Counter
    by = Counter((x["field"], x["why"]) for x in v)
    print(json.dumps({"violations": len(v), "by_field": {f"{k[0]}: {k[1]}": n for k, n in by.most_common()},
                      "sample": v[:10]}, indent=2))
    return 1 if v else 0


if __name__ == "__main__":
    raise SystemExit(main())
