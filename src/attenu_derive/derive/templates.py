"""
L1 templates v1 — the two role envelopes that recur across every sampled project/framework
(gold-v1): the read-only EXPLORER sub-agent and the DELEGATING-WRITER orchestrator.
Deterministic, auditable, human-written. A template is matched on (role, task text, tools available);
its authority is expressed in the closed vocabulary; the grant is always meet(parent, template).

v1 (T12, 2026-08-18): a template no longer hard-codes its scope set. It COMPUTES it from
`tools_available` resolved through the catalog (exact/glob/heuristic tier), intersected with the
template's ALLOWED FAMILIES — never wider than what the node's tools need, never a tier-2 family at
L1. Why: v0 granted `{"fs.read"}` to every explorer; an ADK explorer holding `search_text`
(-> data.read) was benign-denied on every search (G1 train benign-deny 0.0204), and granting
data.read to everyone would have been over-provision. A node with no read tool gets neither read
scope; an orchestrator with no write tool gets no write scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

# --- signals ---------------------------------------------------------------------------------
_EXPLORE = re.compile(r"\b(explore|analy[sz]e|understand|find|identify|locate|inspect|read|grep|search|report (back|findings)|investigate|summari[sz]e how|list the)\b", re.I)
_WRITE_OUT = re.compile(r"\b(save (it|the (result|report|findings)) to|write (it|a|the|findings|.*?)\s*(to\s+)?[A-Z0-9_\-]+\.md|write [A-Z0-9_\-]+\.md|produce .* (report|overview|summary)|save .*\.md)\b", re.I)
_DELEGATE = re.compile(r"\b(delegate|use the (researcher|.*sub-?agent)|via the (task|Agent) tool|sub-?agent)\b", re.I)
_DELEGATE_ALL_READING = re.compile(r"(use the researcher (sub-?agent )?for all reading|delegate (the|all) reading|only write the final file yourself|delegate .*reading|do not read)", re.I)
_NO_WRITE = re.compile(r"\b(do not write|don't write|no writes?|read[- ]only)\b", re.I)
# Anything that implies a resource family beyond the local filesystem: the explorer template must NOT
# match (it would deny the needed web/mail/payment call) — fall through to L2 (catalog) instead.
_NON_LOCAL = re.compile(r"\b(fetch|download|website|web ?site|url|https?://|api call|e-?mail|mail|send|pay|payment|transfer|deploy|push|upload|post to|purchase|buy|reserve|book (a|an|the|me|us)|place (an|the|a) order|order (a|an|the|from|online))\b", re.I)   # money/booking VERB phrases only — not "in order to" / "book of"

EXPLORER_FAMILIES = frozenset({"fs.read", "data.read", "web.search", "web.fetch", "db.read", "crm.read"})   # READ-side families (tier<=1) an explorer/analyst may hold — a web-reading analyst is still read-only
WRITER_FAMILIES = frozenset({"fs.write", "data.write"})              # tier-1 write families a delegating-writer may hold
L1_MAX_TIER = 1                                                      # L1 never grants a tier-2 family (payments, mail, deletes, exec)


@lru_cache(maxsize=1)
def _default_catalog() -> dict:
    from attenu_derive.catalog.coverage import load_catalog
    return load_catalog()


def families_of(tools_available: list[str], catalog: dict | None = None) -> dict[str, dict]:
    """tool -> catalog entry (curated or heuristic) for every resolvable tool; unknown.* and unresolved are dropped."""
    from attenu_derive.catalog.coverage import resolve
    cat = catalog if catalog is not None else _default_catalog(); out = {}
    for t in tools_available or []:
        e = resolve(cat, t)
        if e is not None and not str(e.get("scope", "")).startswith("unknown."):
            out[t] = e
    return out


def _scopes_in(fams: dict[str, dict], allowed: frozenset) -> tuple[set[str], list[str]]:
    """The allowed families the node's tools resolve to (tier <= L1_MAX_TIER), and which tools got there heuristically."""
    scopes, heuristic = set(), []
    for t, e in fams.items():
        sc = str(e.get("scope", ""))
        if sc in allowed and int(e.get("tier", 2)) <= L1_MAX_TIER:
            scopes.add(sc)
            if e.get("heuristic"): heuristic.append(t)
    return scopes, heuristic


@dataclass
class TemplateMatch:
    name: str
    scopes: set[str]
    ceilings: list[dict]          # vocabulary-level dicts: {"type": "RowLimit", "max": 1000} | {"type": "CallLimit", "max": 5, "applies_to": "fs.write"} | {"type": "EgressRank", "level": "none"}
    ttl: int
    confidence: float
    evidence: dict = field(default_factory=dict)
    held_for_delegation: list[str] = field(default_factory=list)   # scopes the node holds to PASS DOWN, not to use itself (rubric v1.2)


def match(role: str, task: str, tools_available: list[str], declared_subagents: list[str], catalog: dict | None = None,
          subagent_tools: dict[str, list[str]] | None = None, role_constraints: dict | None = None) -> TemplateMatch | None:
    """Return the best-matching L1 template, or None (fall through to L2)."""
    task = task or ""
    fams = families_of(tools_available, catalog)
    # the delegation SUBTREE's tools: what the declared sub-agents hold (role-specific suites: ADK/CrewAI orchestrators
    # hold no read tool themselves, their explorers do). A parent must hold what it delegates — from the subtree, never wider.
    sub_fams = families_of(sorted({t for ts in (subagent_tools or {}).values() for t in ts}), catalog)
    subtree_read_scopes, subtree_read_heur = _scopes_in({**sub_fams, **fams}, EXPLORER_FAMILIES)
    read_scopes, read_heur = _scopes_in(fams, EXPLORER_FAMILIES)
    write_scopes, write_heur = _scopes_in(fams, WRITER_FAMILIES)
    has_read = "fs.read" in read_scopes; has_write = bool(write_scopes)     # the verb-less explorer path is anchored to REPOSITORY reads (fs.read), not any data API
    has_del = any(str(e.get("scope", "")).startswith("agent.delegate") for e in fams.values())
    has_msg = any(str(e.get("scope", "")) == "agent.message" for e in fams.values())

    # EXPLORER: a sub-agent whose task is to look and report; may hold write tools but must not use them.
    # A reading sub-agent (has fs.read tools, no structural write grant) STAYS an explorer regardless of task-text
    # write/egress verbs — otherwise injected text ("...send an email...") knocks it out of this narrow template into a
    # wider L2 and grants fs.write from the Write tool it holds but must not use (T17). Task text may narrow, never widen.
    structural_write = bool((role_constraints or {}).get("allow_write"))
    if role == "child" and has_read and not structural_write:
        conf = 0.9 if _EXPLORE.search(task) else 0.7
        if read_heur: conf -= 0.2                                     # a read family reached only through a name heuristic
        return TemplateMatch("explorer", set(read_scopes),           # the read families ITS tools resolve to — {} when it holds no read tool
                             [{"type": "RowLimit", "max": 1000}, {"type": "EgressRank", "level": "none"}],
                             900, conf,
                             {"signals": ["explore-verb" if _EXPLORE.search(task) else "read-tools-only-task", "child-role"],
                              "read_tools": sorted(t for t, e in fams.items() if e.get("scope") in EXPLORER_FAMILIES), "heuristic_tools": read_heur})

    # DELEGATING-WRITER: a root that delegates exploration and writes one deliverable.
    if role == "root" and (_WRITE_OUT.search(task) or has_write) and (has_del or _DELEGATE.search(task) or declared_subagents):
        # Delegate authority is the DECLARED roster — task-text-INDEPENDENT, so injected text cannot add or move a delegate
        # scope (T17: task text must never widen; the delegation graph is bounded by meet, not by the prompt). Which members
        # the task happens to name is recorded as evidence only, never as the grant.
        low = re.sub(r"[-_]", " ", task.lower())
        named = [s for s in (declared_subagents or []) if re.search(r"\b" + re.escape(re.sub(r"[-_]", " ", s.lower())) + r"\b", low)]
        scopes = set(write_scopes) | {f"agent.delegate.{s}" for s in (declared_subagents or [])}   # the write family ITS tool resolves to — none without a write tool
        # Rubric v1.2 (T13, 2026-08-18): monotonic attenuation means child ⊆ parent — a parent cannot delegate what
        # it does not hold. The delegating-writer therefore HOLDS, for delegation, the read families its tools
        # resolve to (T12 semantics: never wider, tier<=1). Its OWN reads remain the over-exploration signal (R3),
        # not a scope question. Minimal authority for a parent = the minimal closure over its delegation subtree.
        held = sorted(subtree_read_scopes - scopes)          # own read tools ∪ declared sub-agents' read tools, tier<=1 only
        scopes |= set(held)
        if has_msg:                                     # messaging is structural to orchestration — but only if the tool exists
            scopes.add("agent.message")
        # Rubric v1: the delegating-writer role is delegate + write. Reads belong to the sub-agents it
        # delegates to; granting fs.read here was pure over-provision on every sampled project.
        reads_forbidden = bool(_DELEGATE_ALL_READING.search(task))
        ceilings = [{"type": "CallLimit", "max": 5, "applies_to": sc} for sc in sorted(write_scopes)] + [{"type": "EgressRank", "level": "none"}]
        return TemplateMatch("delegating-writer", scopes, ceilings, 3600, 0.85 - (0.2 if (write_heur or subtree_read_heur) else 0.0),
                             {"signals": ["root-role", "write-deliverable", "delegates"] + (["delegate-all-reading"] if reads_forbidden else []),
                              "write_tools": sorted(t for t, e in fams.items() if e.get("scope") in WRITER_FAMILIES), "heuristic_tools": sorted(set(write_heur + subtree_read_heur)),
                              "held_for_delegation": held, "subagents_named_in_task": named},
                             held_for_delegation=held)
    return None
