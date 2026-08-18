"""T13 (P2 open, 2026-08-18): shadow mode = derived authority replayed down the REAL chain, would-deny counted,
nothing blocked. Each would-be benign block is attributed to its cause: the node's own proposal, or the parent
chain (the parent's derived authority lacks the family the child needs — invisible to G1, which scores every
row against a synthetic wide parent)."""
from attenu_derive.eval.shadow import shadow

ROOT = {"node": "chain:n0", "parent_node": None, "agent": "orchestrator", "project": "p", "framework": "claude-agent-sdk",
        "task": "Produce REPORT.md for this repository. Delegate all reading to the researcher subagent; write only the final file yourself.",
        "delegated_to": ["researcher"], "negatives": [],
        "child_calls": [{"tool": "Agent", "scope": "agent.delegate.researcher", "outcome": "allow", "quantities": {}},
                        {"tool": "Write", "scope": "observe.tool", "outcome": "allow", "quantities": {}}],
        "observed_envelope": {"tools": ["Agent", "Write"], "quantities_max": {}}}
CHILD = {"node": "chain:n1", "parent_node": "chain:n0", "agent": "researcher", "project": "p", "framework": "claude-agent-sdk",
         "task": "Explore the repository and report findings with file paths", "delegated_to": [], "negatives": [],
         "child_calls": [{"tool": "Read", "scope": "observe.tool", "outcome": "allow", "quantities": {"limit": "11-100"}},
                         {"tool": "Grep", "scope": "observe.tool", "outcome": "allow", "quantities": {}}],
         "observed_envelope": {"tools": ["Read", "Grep"], "quantities_max": {"limit": "11-100"}}}


def test_shadow_replays_the_chain_and_attributes_blocks():
    rep = shadow([ROOT, CHILD])
    assert rep["nodes"] == 2 and rep["calls"] == 4
    assert rep["would_block"] == len(rep["blocks"])
    # the orchestrator's own calls (delegate + write) are permitted by its derived authority
    assert not [b for b in rep["blocks"] if b["node"] == "chain:n0"]
    # every block is attributed, and the per-scope / per-cause breakdowns add up
    assert sum(rep["by_cause"].values()) == rep["would_block"] == sum(rep["by_scope"].values())
    for b in rep["blocks"]:
        assert b["cause"] in ("proposal", "parent-chain") and b["layer"] in ("L1", "L2", "L4")


def test_shadow_child_reads_survive_only_if_the_parent_holds_the_read_family():
    """The chain invariant made explicit: a child ⊆ parent. If the orchestrator's derived authority lacks fs.read,
    every child read is a would-be block with cause 'parent-chain' — even though the child's OWN proposal allows it."""
    rep = shadow([ROOT, CHILD])
    child_blocks = [b for b in rep["blocks"] if b["node"] == "chain:n1"]
    parent_holds_read = "fs.read" in rep["derived"]["chain:n0"]["scopes"]
    if parent_holds_read:
        assert child_blocks == []
    else:
        assert child_blocks and all(b["cause"] == "parent-chain" and b["scope"] == "fs.read" for b in child_blocks)


def test_shadow_reports_zero_when_authority_fits():
    root = dict(ROOT); child = dict(CHILD, child_calls=[], observed_envelope={"tools": [], "quantities_max": {}})
    rep = shadow([root, child])
    assert not [b for b in rep["blocks"] if b["node"] == "chain:n1"]
