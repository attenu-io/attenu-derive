"""RED→GREEN: export the shim's audit log (root/spawn/allow/deny/kill events) into
corpus rows — one row per delegation event, with the child's recorded calls and
a mechanical 'observed envelope' (provisional label). Derived features only."""
import json
from attenu_derive.corpus.export import audit_to_corpus_rows, observed_envelope

RUN = {"project": "demo", "framework": "langchain", "model": "scripted", "seed": 0, "salt": "s"}

AUDIT = [
    {"seq": 0, "event": "root",  "chain_id": "c", "node": "c:n0", "agent": "orchestrator"},
    {"seq": 1, "event": "allow", "chain_id": "c", "node": "c:n0", "scope": "observe.ls", "tool": "ls",
     "context": {"arg_shape": {}, "quantities": {}, "arg_hashes": {}, "str_len_buckets": {}}},
    {"seq": 2, "event": "spawn", "chain_id": "c", "parent": "c:n0", "node": "c:n1", "agent": "researcher",
     "task": "summarize the repo architecture", "requested": {"scopes": ["observe.*"]}, "granted": {"scopes": ["observe.*"]}},
    {"seq": 3, "event": "allow", "chain_id": "c", "node": "c:n1", "scope": "observe.read_file", "tool": "read_file",
     "context": {"arg_shape": {"path": "str"}, "quantities": {}, "arg_hashes": {"path": "abc"}, "str_len_buckets": {"path": "11-100"}}},
    {"seq": 4, "event": "allow", "chain_id": "c", "node": "c:n1", "scope": "observe.grep", "tool": "grep",
     "context": {"arg_shape": {"pattern": "str"}, "quantities": {"max_results": "11-100"}, "arg_hashes": {"pattern": "def"}, "str_len_buckets": {"pattern": "2-10"}}},
    {"seq": 5, "event": "deny",  "chain_id": "c", "node": "c:n1", "scope": "observe.write_file", "tool": "write_file",
     "reason": "scope_not_granted", "context": {"arg_shape": {"path": "str"}, "quantities": {}, "arg_hashes": {}, "str_len_buckets": {}}},
    {"seq": 6, "event": "kill",  "chain_id": "c", "target": "c:n1", "revoked": ["c:n1"]},
]


def test_one_row_per_delegation_event_with_child_calls():
    rows = audit_to_corpus_rows(AUDIT, run=RUN, task_text_mode="hash")
    assert [r["agent"] for r in rows] == ["orchestrator", "researcher"]
    child = rows[1]
    assert child["parent_node"] == "c:n0" and child["node"] == "c:n1"
    assert [c["tool"] for c in child["child_calls"]] == ["read_file", "grep", "write_file"]
    assert [c["outcome"] for c in child["child_calls"]] == ["allow", "allow", "deny"]
    assert child["child_calls"][1]["quantities"] == {"max_results": "11-100"}
    # task text is hashed by default (shipped corpus), features kept
    assert "summarize" not in json.dumps(child)
    assert child["task_hash"] and child["task_features"]["len_bucket"] == "11-100"
    assert child["source"] == "observed" and child["project"] == "demo" and child["framework"] == "langchain"


def test_task_text_can_be_kept_for_the_local_mirror_only():
    rows = audit_to_corpus_rows(AUDIT, run=RUN, task_text_mode="keep")
    assert rows[1]["task"] == "summarize the repo architecture"


def test_observed_envelope_is_the_benign_minimum():
    rows = audit_to_corpus_rows(AUDIT, run=RUN)
    env = observed_envelope(rows[1])
    # only ALLOWED calls define the envelope; the denied write is a negative
    assert env["tools"] == ["grep", "read_file"]
    assert env["quantities_max"] == {"max_results": "11-100"}
    assert rows[1]["negatives"] == ["write_file"]
    assert rows[1]["label_provenance"] == "observed"


def test_export_never_carries_raw_values_or_prompts():
    rows = audit_to_corpus_rows(AUDIT, run=RUN)
    blob = json.dumps(rows)
    assert "attacker" not in blob and "/secrets" not in blob
