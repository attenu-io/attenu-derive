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
