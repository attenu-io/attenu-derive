"""
Catalog coverage over sampled corpora — the catalog's success metric (design §5a).

    python -m attenu_derive.catalog.coverage data/corpus/*.jsonl

Reports: share of recorded tool CALLS whose tool resolves in the catalog; share of delegation
EVENTS fully resolvable at L2 (every call covered); the uncovered tools by count.
"""
from __future__ import annotations

import fnmatch
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

__all__ = ["load_catalog", "resolve", "coverage"]

_CATALOG = Path(__file__).with_name("v0.yaml")


def load_catalog(path: Path = _CATALOG) -> dict:
    return yaml.safe_load(path.read_text())


def resolve(catalog: dict, tool: str, description: str = "", *, heuristics: bool = True) -> dict | None:
    """exact entry -> glob pattern -> (optional) heuristic family classification (flagged) -> None."""
    t = catalog.get("tools", {}).get(tool)
    if t is not None:
        return t
    for pat, entry in (catalog.get("patterns") or {}).items():
        if fnmatch.fnmatchcase(tool, pat):
            return entry
    if heuristics:
        from attenu_derive.catalog.heuristics import heuristic_resolve
        return heuristic_resolve(tool, description)
    return None


def coverage(rows: list[dict], catalog: dict) -> dict:
    """Coverage split by tier: EXACT/glob (curated) vs HEURISTIC (flagged leads) vs uncovered."""
    calls = Counter(); uncovered = Counter(); heuristic = Counter(); events = 0; events_ok = 0; events_curated = 0
    for r in rows:
        events += 1; ok = True; curated = True
        for c in r.get("child_calls", []):
            calls[c["tool"]] += 1
            e = resolve(catalog, c["tool"], c.get("description", ""))
            if e is None:
                uncovered[c["tool"]] += 1; ok = False; curated = False
            elif e.get("heuristic"):
                heuristic[c["tool"]] += 1; curated = False
        events_ok += ok; events_curated += curated
    n = sum(calls.values()); nh = sum(heuristic.values()); nu = sum(uncovered.values())
    return {
        "calls": n,
        "calls_covered_share": round((n - nu) / n, 4) if n else None,          # exact + heuristic
        "calls_curated_share": round((n - nu - nh) / n, 4) if n else None,     # exact/glob only
        "calls_heuristic_share": round(nh / n, 4) if n else None,
        "events": events,
        "events_fully_resolvable_share": round(events_ok / events, 4) if events else None,
        "events_curated_share": round(events_curated / events, 4) if events else None,
        "uncovered_tools": dict(uncovered.most_common(40)),
        "heuristic_tools_top": dict(heuristic.most_common(20)),
        "tools_seen": len(calls),
    }


def main(argv=None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    rows = [json.loads(l) for p in paths for l in Path(p).read_text().splitlines() if l.strip()]
    print(json.dumps(coverage(rows, load_catalog()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
