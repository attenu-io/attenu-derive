"""
Catalog coverage over sampled corpora — the catalog's success metric (design §5a).

    python -m attenu_derive.catalog.coverage data/corpus/*.jsonl

Headline (T7, 2026-08-18) = GRANTABLE share of recorded tool CALLS: curated (exact/glob) entries +
heuristic families of tier <= HEURISTIC_MAX_GRANT_TIER — what the deriver can actually grant.
Reported apart: WITHHELD (tier-2 heuristics: resolvable, never granted — the curation backlog),
UNRESOLVED (no entry, no family; `unknown.*` counts here). `calls_covered_share` (= resolvable,
grantable + withheld) is kept for continuity but is NOT the headline. Same split per delegation EVENT.
"""
from __future__ import annotations

import fnmatch
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

__all__ = ["load_catalog", "load_domain", "resolve", "coverage"]

_CATALOG = Path(__file__).with_name("v1.yaml")
_DOMAINS = Path(__file__).with_name("domains")


def load_catalog(path: Path = _CATALOG) -> dict:
    return yaml.safe_load(path.read_text())


def load_domain(name: str) -> dict:
    """A curated domain pack (catalog/domains/<name>.yaml) — the overlay onboarding produces for a customer's own tools."""
    return yaml.safe_load((_DOMAINS / f"{name}.yaml").read_text())


def resolve(catalog: dict, tool: str, description: str = "", *, heuristics: bool = True, overlay: dict | None = None) -> dict | None:
    """domain overlay (curated) -> exact entry -> glob pattern -> (optional) heuristic family -> None. The overlay wins:
    a customer's curated pack resolves their own tool names before the base catalog or any heuristic."""
    if overlay is not None:
        o = (overlay.get("tools") or {}).get(tool)
        if o is not None:
            return o
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


def _classify(catalog: dict, tool: str, description: str = "", overlay: dict | None = None) -> str:
    """'curated' | 'requires_grant' (curated tier-2 held pending operator grant) | 'heuristic' | 'withheld' | 'unresolved'."""
    from attenu_derive.catalog.heuristics import HEURISTIC_MAX_GRANT_TIER
    e = resolve(catalog, tool, description, overlay=overlay)
    if e is None or str(e.get("scope", "")).startswith("unknown."):
        return "unresolved"
    if e.get("heuristic"):
        return "heuristic" if int(e.get("tier", 2)) <= HEURISTIC_MAX_GRANT_TIER else "withheld"
    return "requires_grant" if e.get("requires_grant") else "curated"


def coverage(rows: list[dict], catalog: dict, overlay: dict | None = None) -> dict:
    """Coverage split by grantability: curated + heuristic(tier<=1) = GRANTABLE; requires_grant (curated tier-2, held);
    withheld (tier-2 heuristic); unresolved. `overlay` = a curated domain pack (per-app curation)."""
    calls = Counter(); by_class = {k: Counter() for k in ("curated", "requires_grant", "heuristic", "withheld", "unresolved")}
    events = 0; ev_grantable = 0; ev_resolvable = 0; ev_curated = 0
    for r in rows:
        events += 1; grantable = True; resolvable = True; curated = True
        for c in r.get("child_calls", []):
            calls[c["tool"]] += 1
            k = _classify(catalog, c["tool"], c.get("description", ""), overlay=overlay)
            by_class[k][c["tool"]] += 1
            if k not in ("curated", "requires_grant"): curated = False
            if k in ("withheld", "unresolved", "requires_grant"): grantable = False
            if k == "unresolved": resolvable = False
        ev_grantable += grantable; ev_resolvable += resolvable; ev_curated += curated
    n = sum(calls.values()); cnt = {k: sum(v.values()) for k, v in by_class.items()}
    share = lambda x: round(x / n, 4) if n else None                              # noqa: E731
    eshare = lambda x: round(x / events, 4) if events else None                   # noqa: E731
    return {
        "calls": n,
        "calls_grantable_share": share(cnt["curated"] + cnt["heuristic"]),        # HEADLINE: what the deriver can grant now
        "calls_curated_share": share(cnt["curated"] + cnt["requires_grant"]),      # curated (incl. held-pending-grant): the confident, reviewed surface
        "calls_requires_grant_share": share(cnt["requires_grant"]),                # curated tier-2 held pending an operator grant
        "calls_heuristic_share": share(cnt["heuristic"] + cnt["withheld"]),        # every heuristic classification (tier<=1 + withheld)
        "calls_heuristic_grantable_share": share(cnt["heuristic"]),
        "calls_withheld_share": share(cnt["withheld"]),                            # tier-2 heuristics: resolvable, never granted
        "calls_unresolved_share": share(cnt["unresolved"]),
        "calls_covered_share": share(n - cnt["unresolved"]),                       # resolvable — kept, not the headline
        "events": events,
        "events_grantable_share": eshare(ev_grantable),
        "events_fully_resolvable_share": eshare(ev_resolvable),
        "events_curated_share": eshare(ev_curated),
        "uncovered_tools": dict(by_class["unresolved"].most_common(40)),
        "withheld_tools_top": dict(by_class["withheld"].most_common(25)),
        "requires_grant_tools": dict(by_class["requires_grant"].most_common(25)),
        "heuristic_tools_top": dict(by_class["heuristic"].most_common(20)),
        "tools_seen": len(calls),
    }


def main(argv=None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    rows = [json.loads(l) for p in paths for l in Path(p).read_text().splitlines() if l.strip()]
    print(json.dumps(coverage(rows, load_catalog()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
