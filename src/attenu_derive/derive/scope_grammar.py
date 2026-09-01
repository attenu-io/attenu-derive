"""D1 (attenu-guard 0.8.0) tightened the shim's scope grammar to lowercase dot-separated segments
(`^[a-z][a-z0-9_-]*(\\.[a-z][a-z0-9_-]*)*\\.([a-z0-9_-]+|\\*)$`), enforced at `Authority` construction.
Two shapes in the derivation/evaluation pipeline build a scope segment from a raw, uncontrolled name
that is not guaranteed to already match it: an unresolved TOOL name (`unknown.<tool>`) and a
delegation TARGET agent/sub-agent name (`agent.delegate.<agent>`) — both routinely CamelCase in real
frameworks (LangChain tool classes, MCP tool names; ADK/CrewAI agent class names). `agent.delegate.*`
is the more reachable of the two: `unknown.*` is dropped before a granted `Authority` is ever built
(disposition.py's own filtering, propose.py's L2, templates.py's `families_of`), but a delegation
target's name is not — `derive/templates.py` and `derive/propose.py` feed it straight into the
GRANTED authority via `spec_to_authority`, and `sample/run_adk_enforce.py` builds an `Authority` from
declared agent names directly. A CamelCase sub-agent/target name reaching either crashes the deriver
itself, not just the eval.

This module is the one place every caller of either shape goes through, so they produce byte-identical
scopes for the same raw name (no drift between the derivation and evaluation pipelines' otherwise
independently-built representations — see `resolved_scope`) and none of them can construct a
grammar-invalid `Authority`.

The normalisation is LOSSY and its result is used as an identity, not a display string: distinct raw
names can collide onto the same segment (`send_email` / `send.email` / `SEND_EMAIL` all become
`send_email`; a unicode letter outside `[a-z]` is dropped, not transliterated). This cannot produce a
wrong GRANT: an unresolved tool's `unknown.*` scope is never granted (see the module docstring above),
and a delegation target's grant is looked up and issued by its own raw name before this normalisation
ever runs — the normalised scope only gates whether the *call* to delegate is itself authorised, same
as any other scope. But a collision CAN merge two distinct unresolved tools, or two distinct
delegation targets with colliding normalised names, into one row in the set-based comparisons
`gold_v0.label_row` / `eval.g1.score` do (a gold `label.scopes` set, or a G1 `used_scopes` set) — a
false covering that could hide a true positive or a true benign-deny in a report. Collisions are rare
(names differing only in case, or only in the characters this strips) and, when they happen, only
weaken evidence quality for that row — they do not create or hide a wrong grant.
"""
from __future__ import annotations

import re

__all__ = ["scope_segment", "unknown_tool_scope", "delegate_scope", "resolved_scope"]

_INVALID = re.compile(r"[^a-z0-9_-]+")


def scope_segment(name: str) -> str:
    """Normalise an arbitrary name into a single segment valid under the shim's scope grammar
    (`^[a-z][a-z0-9_-]*$`): lowercase; any run of characters outside `[a-z0-9_-]` collapses to one
    `_`; leading/trailing `_`/`-` are stripped. A degenerate result (empty, or not starting with a
    letter — e.g. an all-separator or digit-leading name) gets a `t`/`t_` prefix so the segment still
    starts with a letter, which the grammar requires."""
    s = _INVALID.sub("_", (name or "").lower()).strip("_-")
    if not s:
        return "t"
    if not s[0].isalpha():
        return f"t_{s}"
    return s


def unknown_tool_scope(name: str) -> str:
    """The `unknown.<segment>` scope for a tool with no catalog/pack entry."""
    return f"unknown.{scope_segment(name)}"


def delegate_scope(name: str) -> str:
    """The `agent.delegate.<segment>` scope for delegating to sub-agent/target `name`."""
    return f"agent.delegate.{scope_segment(name)}"


def resolved_scope(entry: dict | None, tool: str) -> str:
    """The scope for a resolved catalog/pack `entry`, or the normalised `unknown.<tool>` fallback —
    when `entry` is `None` (no catalog/pack entry at all) OR `entry`'s own declared scope is ITSELF
    only a catalog-level `unknown.*` placeholder (e.g. the `mcp__*__*` pattern's blanket
    `unknown.mcp`), which this replaces with a scope specific to THIS tool's own name — finer-grained,
    and what `disposition.tool_dispositions`'s own `unresolved` classification is keyed on. Every
    derivation/evaluation call site that needs "the scope, or unknown.<tool> if this doesn't really
    resolve" goes through this one function so they agree deliberately, not by the coincidence of two
    independently-written fallbacks happening to produce the same string for today's catalog."""
    if entry is None or str(entry.get("scope", "")).startswith("unknown."):
        return unknown_tool_scope(tool)
    return str(entry["scope"])
