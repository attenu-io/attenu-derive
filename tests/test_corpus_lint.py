"""PM standing rule: schema additions must be self-detecting. This lint (over the COMMITTED gold) fails CI if a
row is missing completed / declared_subagents / a specialist's no_write — the three fields stale samplers dropped."""
import json
from pathlib import Path

from attenu_derive.corpus.lint import violations
from attenu_derive.eval.g1 import GOLD


def test_committed_gold_has_no_missing_fields():
    rows = [json.loads(l) for l in Path(GOLD).read_text().splitlines() if l.strip()]
    v = violations(rows, source="gold")
    assert v == [], f"{len(v)} rows missing schema fields (stale sampler?): {v[:8]}"


def test_lint_detects_a_planted_missing_field():
    good = {"event_id": "e", "project": "p", "framework": "langchain/deepagents", "node": "n1", "parent_node": "n0",
            "agent": "api-surveyor", "completed": True, "role_constraints": {"no_write": True}}
    assert violations([good]) == []
    assert any(x["field"] == "completed" for x in violations([{k: v for k, v in good.items() if k != "completed"}]))
    assert any(x["field"] == "role_constraints.no_write" for x in violations([{**good, "role_constraints": {}}]))
    assert any(x["field"] == "declared_subagents" for x in violations([{**good, "parent_node": None}]))
