"""Slice 1 / Plan A, Task 9 — one classifier answers "why would this tool's scope be absent?" for every runner:
held_pending_grant (curated tier-2 without an operator grant, or a scope the operator held back), withheld_tier2
(resolvable only to a tier-2 heuristic the deriver never grants), unresolved (no entry), or None (grantable — a
later deny is the shim's own out_of_authority). Mirrors catalog.coverage._classify so console and coverage agree."""
from attenu_derive.catalog.coverage import load_catalog, load_domain
from attenu_derive.derive.disposition import tool_dispositions


def test_dispositions_on_the_retail_support_pack():
    cat, dom = load_catalog(), load_domain("retail-support")
    d = tool_dispositions(cat, dom, ["send_care_instructions", "update_salesforce_crm", "totally_unknown_tool"], operator_grants=set())
    assert d["send_care_instructions"] == ("mail.send", "held_pending_grant")          # curated tier-2, no grant
    assert d["update_salesforce_crm"] == ("crm.write", None)                            # curated, grantable
    assert d["totally_unknown_tool"] == ("unknown.totally_unknown_tool", "unresolved")
    d2 = tool_dispositions(cat, dom, ["send_care_instructions"], operator_grants={"mail.send"})
    assert d2["send_care_instructions"] == ("mail.send", None)                          # granted -> nothing to explain
    d3 = tool_dispositions(cat, dom, ["update_salesforce_crm"], operator_grants=set(), held={"crm.write"})
    assert d3["update_salesforce_crm"] == ("crm.write", "held_pending_grant")           # operator did not enable it


def test_heuristic_tier2_is_withheld_only_when_heuristics_are_on():
    cat = load_catalog()
    off = tool_dispositions(cat, None, ["place_order"], operator_grants=set(), heuristics=False)
    on = tool_dispositions(cat, None, ["place_order"], operator_grants=set(), heuristics=True)
    assert off["place_order"] == ("unknown.place_order", "unresolved")                  # enforce = curated only
    assert on["place_order"] == ("payments.transfer", "withheld_tier2")                 # resolvable only to a tier-2 heuristic


def test_every_disposition_value_is_a_shim_constant():
    from delegation_guard import Disposition
    cat, dom = load_catalog(), load_domain("retail-support")
    d = tool_dispositions(cat, dom, ["send_care_instructions", "x_unknown"], operator_grants=set(), heuristics=True)
    assert all(v[1] is None or v[1] in Disposition.ALL for v in d.values())
