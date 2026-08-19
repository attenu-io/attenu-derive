"""Slice 1 / Plan A, Task 10 — a product has an identity (and an anchor key) before it has a cloud or a token."""
import json
import stat

import pytest

from attenu_derive import product


def test_init_product_writes_identity_and_a_private_anchor_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    meta = product.init_product(tmp_path / "proj", "Mortgage Assistant", "dev")
    pj = json.loads((tmp_path / "proj" / ".attenu" / "product.json").read_text())
    assert pj["name"] == "Mortgage Assistant" and pj["environment"] == "dev" and len(pj["product_id"]) == 26 and pj["anchor_pub"]
    assert meta["product_id"] == pj["product_id"]
    key = tmp_path / "proj" / ".attenu" / "keys" / "anchor.key"
    assert key.exists() and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert product.registry_list()[0]["dir"] == str((tmp_path / "proj").resolve())
    signer = product.load_anchor_signer(tmp_path / "proj"); verifier = product.load_anchor_verifier(tmp_path / "proj")
    assert verifier.verify(b"m", signer.sign(b"m")) and signer.kid == verifier.kid == pj["anchor_kid"]
    assert "anchor_pub" in pj and "private" not in json.dumps(pj)                 # the private key never enters product.json


def test_two_products_on_one_machine_are_distinct_without_any_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    a = product.init_product(tmp_path / "a", "A"); b = product.init_product(tmp_path / "b", "B")
    assert a["product_id"] != b["product_id"] and len(product.registry_list()) == 2
    product.init_product(tmp_path / "a", "A renamed")                             # re-init the same dir: one registry row, not two
    assert len(product.registry_list()) == 2


def test_grants_file_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    product.init_product(tmp_path / "proj", "T")
    assert product.load_grants(tmp_path / "proj") == set()
    assert product.add_grant(tmp_path / "proj", "payments.transfer") == {"payments.transfer"}
    assert product.add_grant(tmp_path / "proj", "payments.transfer") == {"payments.transfer"}     # idempotent
    assert product.load_grants(tmp_path / "proj") == {"payments.transfer"}


def test_product_pack_declares_a_tool_and_merges_over_the_domain(tmp_path, monkeypatch):
    """Declaring = curating a tool into a PRODUCT-LOCAL pack overlay (.attenu/pack.json); the runners merge it over
    the domain pack (product wins). Tier 2 is never auto-granted: it is written requires_grant."""
    from attenu_derive.catalog.coverage import load_catalog, load_domain
    from attenu_derive.derive.disposition import tool_dispositions
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "T")
    assert product.load_pack(tmp_path / "proj") == {"tools": {}}
    e = product.declare_tool(tmp_path / "proj", "lookup_loyalty_tier", scope="data.read", tier=0)
    assert e == {"scope": "data.read", "tier": 0}
    e2 = product.declare_tool(tmp_path / "proj", "refund_customer", scope="payments.transfer", tier=2)
    assert e2["requires_grant"] is True
    with pytest.raises(ValueError):
        product.declare_tool(tmp_path / "proj", "x", scope="Not A Scope", tier=0)
    with pytest.raises(ValueError):
        product.declare_tool(tmp_path / "proj", "x", scope="data.read", tier=7)
    dom = product.effective_domain(load_domain("travel-booking"), tmp_path / "proj")
    d = tool_dispositions(load_catalog(), dom, ["lookup_loyalty_tier", "refund_customer", "book_flight"], operator_grants=set())
    assert d["lookup_loyalty_tier"] == ("data.read", None)                                   # declared -> grantable
    assert d["refund_customer"] == ("payments.transfer", "held_pending_grant")                 # tier-2 -> held
    assert d["book_flight"][0] == "payments.transfer"                                          # the domain pack still applies


def test_unknown_tools_policy_default_deny_then_heuristic(tmp_path, monkeypatch):
    """Rafael: 'where can I define default actions for unknowns?' -> the product policy. Default = deny (fail-closed,
    unresolved). `heuristic` lets the catalog's NAME heuristics grant tier-0/1 families (reads, plain writes);
    tier-2 (money, mail, delete, exec) is ALWAYS withheld by a heuristic — that is a standing decision."""
    from attenu_derive.sample.demo_local import run_demo
    from delegation_guard import evidence
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "T")
    assert product.get_policy(tmp_path / "proj") == {"unknown_tools": "deny"}
    rep = run_demo(tmp_path / "proj")
    assert [r for r in rep["denials_view"] if r["tool"] == "lookup_loyalty_tier"][0]["disposition"] == "unresolved"
    with pytest.raises(ValueError):
        product.set_policy(tmp_path / "proj", "unknown_tools", "yolo")
    with pytest.raises(ValueError):
        product.set_policy(tmp_path / "proj", "not_a_policy", "deny")
    assert product.set_policy(tmp_path / "proj", "unknown_tools", "heuristic") == {"unknown_tools": "heuristic"}
    rep = run_demo(tmp_path / "proj")
    assert not [r for r in rep["denials_view"] if r["tool"] == "lookup_loyalty_tier"]       # read-verb heuristic -> data.read -> allowed
    assert [r for r in rep["denials_view"] if r["tool"] == "book_flight"]                   # curated tier-2 still held
    # a tier-2 heuristic is withheld, never granted, even under the heuristic policy
    from attenu_derive.catalog.coverage import load_catalog
    from attenu_derive.derive.disposition import tool_dispositions
    d = tool_dispositions(load_catalog(), None, ["delete_account", "run_shell"], set(), heuristics=True)
    assert d["delete_account"][1] == "withheld_tier2" and d["run_shell"][1] == "withheld_tier2"
