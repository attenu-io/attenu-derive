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

from typing import Iterable

from attenu_guard import Disposition

from attenu_derive.catalog.coverage import resolve
from attenu_derive.derive.scope_grammar import resolved_scope, unknown_tool_scope

__all__ = ["tool_dispositions", "unknown_tool_scope"]


def tool_dispositions(catalog: dict, domain: dict | None, tools: Iterable[str], operator_grants: set[str], *,
                      held: set[str] | frozenset[str] = frozenset(), heuristics: bool = False) -> dict[str, tuple[str, str | None]]:
    """{tool: (scope, disposition_or_None)}. `scope` is the resolved scope or `unknown.<segment>` (the tool name
    normalised into a grammar-valid segment, see `scope_grammar.unknown_tool_scope`) when unresolved.
    `heuristics=False` = the enforce posture (curated only); `heuristics=True` = the day-0/shadow posture."""
    from attenu_derive.catalog.heuristics import HEURISTIC_MAX_GRANT_TIER
    out: dict[str, tuple[str, str | None]] = {}
    grants = set(operator_grants or ()); held = set(held or ())
    for t in tools:
        e = resolve(catalog, t, heuristics=heuristics, overlay=domain)
        if e is None or str(e.get("scope", "")).startswith("unknown."):
            out[t] = (resolved_scope(e, t), Disposition.UNRESOLVED)
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
