"""Why would a tool's scope be absent from an authority? ONE answer for every runner and adapter — so the ledger,
the denial handed to the model, the Decisions queue and the coverage report all say the same word:

    held_pending_grant   curated tier-2 tool without an operator grant, or a scope the operator held back — "waiting on you"
    withheld_tier2       resolvable only to a tier-2 heuristic the deriver never grants (day-0 / heuristics on)
    unresolved           no catalog/pack entry at all — no authority is known for this tool
    None                 grantable: if the shim later denies it, that is its own `out_of_authority` (real over-reach)

Mirrors `catalog.coverage._classify` deliberately; the values are the shim's `Disposition` constants, never strings
invented here. The derivation mechanism itself stays where it is — this only names the reason a scope is missing.
"""
from __future__ import annotations

import re
from typing import Iterable

from attenu_guard import Disposition

from attenu_derive.catalog.coverage import resolve

__all__ = ["tool_dispositions", "unknown_tool_scope"]

# D1 (attenu-guard 0.8.0) tightened the scope grammar to lowercase dot-separated segments
# (^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*\.([a-z0-9_-]+|\*)$), enforced at `Authority` construction.
# A raw tool name is not guaranteed to match a single segment of that grammar (CamelCase tool classes
# are routine in LangChain/MCP), so it must be normalised before it can appear in `unknown.<segment>`.
_SCOPE_SEGMENT_INVALID = re.compile(r"[^a-z0-9_-]+")


def _scope_segment(name: str) -> str:
    """Normalise an arbitrary tool name into a single segment valid under the shim's scope grammar
    (`^[a-z][a-z0-9_-]*$`): lowercase; any run of characters outside `[a-z0-9_-]` collapses to one
    `_`; leading/trailing `_`/`-` are stripped. A degenerate result (empty, or not starting with a
    letter — e.g. an all-separator or digit-leading name) gets a `t`/`t_` prefix so the segment still
    starts with a letter, which the grammar requires."""
    s = _SCOPE_SEGMENT_INVALID.sub("_", (name or "").lower()).strip("_-")
    if not s:
        return "t"
    if not s[0].isalpha():
        return f"t_{s}"
    return s


def unknown_tool_scope(name: str) -> str:
    """The `unknown.<segment>` scope for a tool with no catalog/pack entry — the one place every caller
    (dispositions, gold labelling, G1/shadow scoring) goes through, so an unresolved tool's fallback
    scope is always grammar-valid (D1) AND identical across the derivation and evaluation pipelines —
    two independently-built `unknown.<tool>` strings for the same raw name must compare equal wherever
    scopes are set-compared (e.g. G1's gold-vs-granted `_covers`)."""
    return f"unknown.{_scope_segment(name)}"


def tool_dispositions(catalog: dict, domain: dict | None, tools: Iterable[str], operator_grants: set[str], *,
                      held: set[str] | frozenset[str] = frozenset(), heuristics: bool = False) -> dict[str, tuple[str, str | None]]:
    """{tool: (scope, disposition_or_None)}. `scope` is the resolved scope or `unknown.<segment>` (the tool name
    normalised into a grammar-valid segment, see `unknown_tool_scope`) when unresolved.
    `heuristics=False` = the enforce posture (curated only); `heuristics=True` = the day-0/shadow posture."""
    from attenu_derive.catalog.heuristics import HEURISTIC_MAX_GRANT_TIER
    out: dict[str, tuple[str, str | None]] = {}
    grants = set(operator_grants or ()); held = set(held or ())
    for t in tools:
        e = resolve(catalog, t, heuristics=heuristics, overlay=domain)
        if e is None or str(e.get("scope", "")).startswith("unknown."):
            out[t] = (unknown_tool_scope(t), Disposition.UNRESOLVED)
            continue
        sc = str(e["scope"])
        if e.get("heuristic") and int(e.get("tier", 2)) > HEURISTIC_MAX_GRANT_TIER:
            out[t] = (sc, Disposition.WITHHELD_TIER2)
            continue
        if (e.get("requires_grant") and sc not in grants) or sc in held:
            out[t] = (sc, Disposition.HELD_PENDING_GRANT)
            continue
        out[t] = (sc, None)
    return out
