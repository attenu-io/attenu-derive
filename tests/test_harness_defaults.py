"""T8 (PM W2 slice, 2026-08-18): per-task wall-clock timeout is 900 s on the FAN-OUT path only
(fewer truncated hold-out rows), 600 s elsewhere; the eval card reports the clean-row count."""
from __future__ import annotations

from attenu_derive.sample.run_claude_sdk import default_timeout_s
from attenu_derive.eval.g1 import score


def test_fanout_timeout_is_900_and_plain_is_600():
    assert default_timeout_s(fanout=True) == 900.0
    assert default_timeout_s(fanout=False) == 600.0
    assert default_timeout_s(fanout=True, explicit=120.0) == 120.0    # an explicit --timeout-s always wins


def test_eval_card_reports_clean_rows():
    from attenu_derive.derive.propose import Deriver
    from attenu_derive.catalog.coverage import load_catalog
    base = {"project": "p", "framework": "f", "agent": "researcher", "role": "subagent", "task": "read the repo",
            "task_features": {}, "observed_envelope": {"tools": ["Read"], "quantities_max": {}}, "delegated_to": [],
            "label": {"scopes": ["fs.read"], "ceilings": [], "ttl_bucket_s": 900}, "negatives": []}
    rows = [dict(base, event_id="a"), dict(base, event_id="b", truncated=True), dict(base, event_id="c", degenerate=True)]
    s = score(rows, Deriver(), load_catalog())
    assert s["rows"] == 3 and s["rows_truncated"] == 1 and s["rows_degenerate"] == 1
    assert s["rows_clean"] == 1


def test_claude_sdk_delegations_are_forced_to_the_foreground():
    """T8 root cause: `AgentDefinition(background=False)` is only a default — the model still launched
    subagents with `run_in_background: true` (trace: Agent -> task_started x4, parent ResultMessage after
    10 s, subagents finish minutes later, parent resumes; slow ones truncate). The PreToolUse hook rewrites
    the input so every delegation runs in the foreground; other tools and foreground calls are untouched."""
    from attenu_derive.sample.run_claude_sdk import foreground_delegation
    out = foreground_delegation({"tool_name": "Agent", "tool_input": {"subagent_type": "researcher", "prompt": "x", "run_in_background": True}})
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "allow"
    assert hso["updatedInput"] == {"subagent_type": "researcher", "prompt": "x", "run_in_background": False}
    assert foreground_delegation({"tool_name": "Agent", "tool_input": {"subagent_type": "researcher", "prompt": "x"}}) == {}
    assert foreground_delegation({"tool_name": "Read", "tool_input": {"file_path": "a", "run_in_background": True}}) == {}
