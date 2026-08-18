"""
L1 templates v0 — the two role envelopes that recur across every sampled project/framework
(gold-v1, 26 items): the read-only EXPLORER sub-agent and the DELEGATING-WRITER orchestrator.
Deterministic, auditable, human-written. A template is matched on (role, task text, tools available);
its authority is expressed in the closed vocabulary; the grant is always meet(parent, template).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# --- signals ---------------------------------------------------------------------------------
_EXPLORE = re.compile(r"\b(explore|analy[sz]e|understand|find|identify|locate|inspect|read|grep|search|report (back|findings)|investigate|summari[sz]e how|list the)\b", re.I)
_WRITE_OUT = re.compile(r"\b(save (it|the (result|report|findings)) to|write (it|a|the|findings|.*?)\s*(to\s+)?[A-Z0-9_\-]+\.md|write [A-Z0-9_\-]+\.md|produce .* (report|overview|summary)|save .*\.md)\b", re.I)
_DELEGATE = re.compile(r"\b(delegate|use the (researcher|.*sub-?agent)|via the (task|Agent) tool|sub-?agent)\b", re.I)
_DELEGATE_ALL_READING = re.compile(r"(use the researcher (sub-?agent )?for all reading|delegate (the|all) reading|only write the final file yourself|delegate .*reading|do not read)", re.I)
_NO_WRITE = re.compile(r"\b(do not write|don't write|no writes?|read[- ]only)\b", re.I)
# Anything that implies a resource family beyond the local filesystem: the explorer template must NOT
# match (it would deny the needed web/mail/payment call) — fall through to L2 (catalog) instead.
_NON_LOCAL = re.compile(r"\b(fetch|download|website|web ?site|url|https?://|api call|e-?mail|mail|send|pay|payment|transfer|deploy|push|upload|post to)\b", re.I)

READ_TOOLS = {"ls", "glob", "grep", "read_file", "Read", "Glob", "Grep", "LS"}
WRITE_TOOLS = {"write_file", "edit_file", "Write", "Edit", "NotebookEdit"}
DELEGATE_TOOLS = {"task", "Agent", "Task"}
MESSAGE_TOOLS = {"SendMessage"}


@dataclass
class TemplateMatch:
    name: str
    scopes: set[str]
    ceilings: list[dict]          # vocabulary-level dicts: {"type": "RowLimit", "max": 1000} | {"type": "CallLimit", "max": 5, "applies_to": "fs.write"} | {"type": "EgressRank", "level": "none"}
    ttl: int
    confidence: float
    evidence: dict = field(default_factory=dict)


def match(role: str, task: str, tools_available: list[str], declared_subagents: list[str]) -> TemplateMatch | None:
    """Return the best-matching L1 template, or None (fall through to L2)."""
    tools = set(tools_available or [])
    task = task or ""
    has_read = bool(tools & READ_TOOLS); has_write = bool(tools & WRITE_TOOLS); has_del = bool(tools & DELEGATE_TOOLS)

    # EXPLORER: a sub-agent whose task is to look and report; may hold write tools but must not use them.
    if role == "child" and not _NON_LOCAL.search(task) and (_EXPLORE.search(task) or (has_read and not _WRITE_OUT.search(task))) and not _WRITE_OUT.search(task):
        return TemplateMatch("explorer", {"fs.read"},
                             [{"type": "RowLimit", "max": 1000}, {"type": "EgressRank", "level": "none"}],
                             900, 0.9 if _EXPLORE.search(task) else 0.7,
                             {"signals": ["explore-verb" if _EXPLORE.search(task) else "read-tools-only-task", "child-role"]})

    # DELEGATING-WRITER: a root that delegates exploration and writes one deliverable.
    if role == "root" and (_WRITE_OUT.search(task) or has_write) and (has_del or _DELEGATE.search(task) or declared_subagents):
        scopes = {"fs.write"} | {f"agent.delegate.{s}" for s in (declared_subagents or [])}
        if tools & MESSAGE_TOOLS:                       # messaging is structural to orchestration — but only if the tool exists
            scopes.add("agent.message")
        # Rubric v1: the delegating-writer role is delegate + write. Reads belong to the sub-agents it
        # delegates to; granting fs.read here was pure over-provision on every sampled project.
        reads_forbidden = bool(_DELEGATE_ALL_READING.search(task))
        ceilings = [{"type": "CallLimit", "max": 5, "applies_to": "fs.write"}, {"type": "EgressRank", "level": "none"}]
        return TemplateMatch("delegating-writer", scopes, ceilings, 3600, 0.85,
                             {"signals": ["root-role", "write-deliverable", "delegates"] + (["delegate-all-reading"] if reads_forbidden else [])})
    return None
