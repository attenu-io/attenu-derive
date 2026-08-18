"""T13 item 5 (PM, 2026-08-18): G1 must derive against the REAL parent chain, as shadow does; the synthetic wide
parent is demoted to per-template unit scoring. Transitive use: a scope held to pass down and used downstream
counts as used — with the PINNING test that a scope whose entire subtree never uses it still counts as unused."""
from attenu_derive.catalog.coverage import load_catalog
from attenu_derive.derive.propose import Deriver
from attenu_derive.eval.g1 import score

ROOT_TASK = "Produce REPORT.md for this repository. Delegate all reading to the researcher subagent; write only the final file yourself."


def _root(fw="claude-agent-sdk", env_tools=("Agent", "Write"), label_scopes=("agent.delegate.researcher", "fs.write", "fs.read"), held=("fs.read",)):
    return {"event_id": "p:r0", "run_key": "run:0", "node": "chain:n0", "parent_node": None, "project": "p", "framework": fw, "agent": "orchestrator",
            "role": "orchestrator", "task": ROOT_TASK, "delegated_to": ["researcher"], "negatives": [],
            "observed_envelope": {"tools": list(env_tools), "quantities_max": {}},
            "label": {"scopes": list(label_scopes), "held_for_delegation": list(held), "ceilings": [], "ttl_bucket_s": 3600}}


def _child(fw="claude-agent-sdk", env_tools=("Read", "Grep"), qmax=None):
    return {"event_id": "p:r1", "run_key": "run:0", "node": "chain:n1", "parent_node": "chain:n0", "project": "p", "framework": fw, "agent": "researcher",
            "role": "subagent", "task": "Explore the repository and report findings with file paths", "delegated_to": [], "negatives": [],
            "observed_envelope": {"tools": list(env_tools), "quantities_max": qmax or {"limit": "11-100"}},
            "label": {"scopes": ["fs.read"], "ceilings": [], "ttl_bucket_s": 900}}


def test_chain_mode_derives_children_against_the_parents_derived_authority():
    d = Deriver(); cat = load_catalog()
    chain = score([_root(), _child()], d, cat, parent="chain")
    synth = score([_root(), _child()], d, cat, parent="synthetic")
    assert chain["parent_mode"] == "chain" and synth["parent_mode"] == "synthetic"
    assert chain["benign_deny_rate"] == 0.0 and synth["benign_deny_rate"] == 0.0     # the parent now HOLDS fs.read for delegation
    assert chain["escalation_count"] == 0


def test_chain_mode_sees_what_synthetic_scoring_hides():
    """A root whose tools offer no read family holds none; under the real chain its child cannot read.
    The synthetic parent reports 0.0 for the same rows — the 240x understatement, pinned."""
    d = Deriver(); cat = load_catalog()
    rows = [_root(fw="unknown-fw", env_tools=("delegate_task", "write_file"), label_scopes=("agent.delegate.researcher", "fs.write"), held=()),
            _child(fw="unknown-fw")]
    chain = score(rows, d, cat, parent="chain"); synth = score(rows, d, cat, parent="synthetic")
    assert synth["benign_deny_rate"] == 0.0
    assert chain["benign_deny_rate"] > 0.0
    denied = [x for r in chain["per_row"] for x in r["denied_benign"]]
    assert denied and all(x[3] == "parent-chain" for x in denied)                    # cause attributed, kept separate from 'proposal'


def test_transitive_use_counts_downstream_use_and_pins_unused_when_no_one_uses_it():
    d = Deriver(); cat = load_catalog()
    used_down = score([_root(), _child()], d, cat, parent="chain")
    root_row = next(r for r in used_down["per_row"] if r["agent"] == "orchestrator")
    assert "fs.read" not in root_row["unused"]                                        # held for delegation AND used by the child -> used
    assert root_row["held_for_delegation"] == ["fs.read"] and root_row["held_used_downstream"] == ["fs.read"]
    nobody = score([_root(), _child(env_tools=())], d, cat, parent="chain")            # the whole subtree never reads
    root_row2 = next(r for r in nobody["per_row"] if r["agent"] == "orchestrator")
    assert "fs.read" in root_row2["unused"]                                            # PIN: still unused — metric correction, not laundering
    assert nobody["held_for_delegation_total"] == 1 and nobody["held_used_downstream_total"] == 0


def test_gold_v12_marks_held_for_delegation_from_descendants():
    from attenu_derive.corpus.gold_v0 import apply_held_for_delegation
    root = {"run_key": "r:0", "node": "n0", "parent_node": None, "label": {"scopes": ["agent.delegate.researcher", "fs.write"]}}
    child = {"run_key": "r:0", "node": "n1", "parent_node": "n0", "label": {"scopes": ["fs.read"]}}
    grandchild = {"run_key": "r:0", "node": "n2", "parent_node": "n1", "label": {"scopes": ["data.read"]}}
    other_run = {"run_key": "r:1", "node": "n1", "parent_node": "n0", "label": {"scopes": ["fs.read"]}}   # a different run: never mixed in
    apply_held_for_delegation([root, child, grandchild, other_run])
    assert root["label"]["held_for_delegation"] == ["data.read", "fs.read"] and set(root["label"]["scopes"]) >= {"fs.read", "data.read", "fs.write"}
    assert child["label"]["held_for_delegation"] == ["data.read"] and "data.read" in child["label"]["scopes"]
    assert grandchild["label"]["held_for_delegation"] == []


def test_gold_marks_writes_as_negatives_when_the_role_forbids_them():
    """Ruling 1 extended (2026-08-18): the role prompt is part of the contract. A specialist whose definition says
    'Do NOT write files' and writes anyway is over-reach (negative), not a benign use the template must admit."""
    from attenu_derive.corpus.gold_v0 import label_row
    from attenu_derive.catalog.coverage import load_catalog
    row = {"event_id": "p:x", "project": "p", "framework": "langchain/deepagents", "agent": "researcher", "node": "n1", "parent_node": "n0",
           "task": "Identify security-relevant code paths and report with file paths", "delegated_to": [], "negatives": [],
           "role_constraints": {"no_write": True},
           "child_calls": [{"tool": "read_file", "outcome": "allow", "quantities": {}}, {"tool": "write_file", "outcome": "allow", "quantities": {}}],
           "observed_envelope": {"tools": ["read_file", "write_file"], "quantities_max": {}}}
    g = label_row(row, load_catalog())
    assert "write_file" in g["negatives"] and "fs.write" not in g["label"]["scopes"] and "fs.read" in g["label"]["scopes"]
    row2 = dict(row, role_constraints={})
    g2 = label_row(row2, load_catalog())
    assert "write_file" not in g2["negatives"] and "fs.write" in g2["label"]["scopes"]        # without the constraint the v0 default stands


def test_declared_subagents_come_from_the_roster_not_from_observed_spawns():
    """T21 (eval leak): a root's declared sub-agents must be what the app DECLARES (available at derivation time), not the
    set it happened to spawn — otherwise agent.delegate.* can never be a benign-deny and unused delegate scopes are invisible."""
    from attenu_derive.derive.propose import event_from_row
    row = {"node": "n0", "parent_node": None, "agent": "orchestrator", "framework": "langchain/deepagents", "task": "Produce REPORT.md, delegate reading",
           "delegated_to": ["researcher"], "declared_subagents": ["researcher", "security-reviewer", "test-analyst", "api-surveyor"],
           "child_calls": [], "observed_envelope": {"tools": ["task", "write_file"], "quantities_max": {}}}
    ev = event_from_row(row, task_text=row["task"])
    assert ev.declared_subagents == ["researcher", "security-reviewer", "test-analyst", "api-surveyor"]
    assert set(ev.subagent_tools) == set(ev.declared_subagents)
    from attenu_derive.eval.g1 import score
    from attenu_derive.catalog.coverage import load_catalog
    from attenu_derive.derive.propose import Deriver
    g = dict(_root(), task="Produce REPORT.md; delegate all reading to your sub-agents; write only the final file yourself.",   # names none
             delegated_to=["researcher"], declared_subagents=["researcher", "security-reviewer"])
    s = score([g, _child()], Deriver(), load_catalog(), parent="chain")
    root_row = next(r for r in s["per_row"] if r["agent"] == "orchestrator")
    assert "agent.delegate.security-reviewer" in root_row["unused"]                       # a declared-but-unused delegate is now VISIBLE as unused
    assert "agent.delegate.researcher" not in root_row["unused"]
    g2 = dict(g, task=ROOT_TASK)                                                            # names "researcher" -> the other is not granted at all (derived from the task)
    s2 = score([g2, _child()], Deriver(), load_catalog(), parent="chain")
    root_row2 = next(r for r in s2["per_row"] if r["agent"] == "orchestrator")
    assert "agent.delegate.security-reviewer" not in root_row2["granted"] and "agent.delegate.researcher" in root_row2["granted"]
