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


def resolve(catalog: dict, tool: str) -> dict | None:
    t = catalog.get("tools", {}).get(tool)
    if t is not None:
        return t
    for pat, entry in (catalog.get("patterns") or {}).items():
        if fnmatch.fnmatchcase(tool, pat):
            return entry
    return None


def coverage(rows: list[dict], catalog: dict) -> dict:
    calls = Counter(); uncovered = Counter(); events = 0; events_ok = 0
    for r in rows:
        events += 1; ok = True
        for c in r.get("child_calls", []):
            calls[c["tool"]] += 1
            if resolve(catalog, c["tool"]) is None:
                uncovered[c["tool"]] += 1; ok = False
        events_ok += ok
    n = sum(calls.values())
    return {
        "calls": n,
        "calls_covered_share": round((n - sum(uncovered.values())) / n, 4) if n else None,
        "events": events,
        "events_fully_resolvable_share": round(events_ok / events, 4) if events else None,
        "uncovered_tools": dict(uncovered.most_common()),
        "tools_seen": len(calls),
    }


def main(argv=None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    rows = [json.loads(l) for p in paths for l in Path(p).read_text().splitlines() if l.strip()]
    print(json.dumps(coverage(rows, load_catalog()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
