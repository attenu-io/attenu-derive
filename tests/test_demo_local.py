"""Slice 1 / Plan B, Task B1 — a USD-0 scripted run that writes a REAL ledger (no model, no API key) so the
console demo never depends on a key: a planner delegates to a booking agent; reads pass, the payment is HELD,
an unknown tool is UNRESOLVED; the bundle is anchored with the product key and verifies with its public key."""
import json
from pathlib import Path

import pytest

from attenu_derive import product
from attenu_derive.sample.demo_local import run_demo
from delegation_guard import evidence


def test_demo_writes_a_real_ledger_with_a_held_payment_denied_in_a_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "Travel Demo")
    rep = run_demo(tmp_path / "proj")
    bundle = json.loads(Path(rep["bundle_path"]).read_text())
    assert evidence.verify_bundle(bundle, product.load_anchor_verifier(tmp_path / "proj"))["ok"] is True
    rows = {(r["agent"], r["tool"]): r for r in evidence.denials(bundle)}
    assert rows[("booking_agent", "book_flight")]["disposition"] == "held_pending_grant"
    assert rows[("booking_agent", "lookup_loyalty_tier")]["disposition"] == "unresolved"
    graph = evidence.delegation_graph(bundle)
    assert len(graph["edges"]) == 1 and graph["nodes"][graph["edges"][0]["child"]]["agent"] == "booking_agent"
    assert graph["nodes"][graph["edges"][0]["child"]]["allows"] >= 2                  # the reads passed
    assert rep["anchor_kid"] != "attenu-anchor-TEST" and "/.attenu/ledger/" in rep["ledger_path"]
    assert rep["evidence_bundle_offline_verify"]["ok"] is True


def test_demo_respects_product_grants_so_the_loop_closes(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "Travel Demo")
    product.add_grant(tmp_path / "proj", "payments.transfer")
    rep = run_demo(tmp_path / "proj")
    bundle = json.loads(Path(rep["bundle_path"]).read_text())
    assert not [r for r in evidence.denials(bundle) if r["tool"] == "book_flight"]                # granted -> passes
    assert [r for r in evidence.denials(bundle) if r["tool"] == "lookup_loyalty_tier"]           # still unresolved


def test_enforce_runner_reads_product_grants(tmp_path, monkeypatch):
    """Inside a product, operator grants made in the console (.attenu/grants.json) reach the live runner."""
    from attenu_derive.sample import run_adk_enforce as R
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "CS")
    product.add_grant(tmp_path / "proj", "mail.send")
    assert R.effective_grants(set(), tmp_path / "proj") == {"mail.send"}
    assert R.effective_grants({"crm.write"}, tmp_path / "proj") == {"mail.send", "crm.write"}
    assert R.effective_grants({"crm.write"}, None) == {"crm.write"}


def test_fanout_scenario_is_a_real_tree_with_every_disposition_and_a_strike_revocation(tmp_path, monkeypatch):
    """Rafael: 'I would expect more agents and tooling.' The fan-out scenario: a planner delegating to four specialists,
    two of them delegating further (>= 8 agents, >= 16 distinct tools), reads allowed, payments HELD, an unknown tool
    UNRESOLVED, a role violation OUT OF AUTHORITY, and a sub-agent REVOKED by the strike policy after 3 same-scope
    denials — all on one anchored, offline-verifiable ledger. USD 0, no model."""
    from attenu_derive.sample.demo_local import run_demo
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "Fanout Demo")
    rep = run_demo(tmp_path / "proj", scenario="fanout")
    bundle = json.loads(Path(rep["bundle_path"]).read_text())
    assert evidence.verify_bundle(bundle, product.load_anchor_verifier(tmp_path / "proj"))["ok"] is True
    graph = evidence.delegation_graph(bundle)
    assert len(graph["nodes"]) >= 8 and len(graph["edges"]) >= 7
    depth2 = [e for e in graph["edges"] if graph["nodes"][e["parent"]]["parent"] is not None]       # a child of a child
    assert depth2, "the tree must be at least two delegations deep"
    tools = {e.get("tool") for e in bundle["entries"] if e.get("tool")}
    assert len(tools) >= 16
    disps = {r["disposition"] for r in evidence.denials(bundle)}
    assert {"held_pending_grant", "unresolved", "out_of_authority"} <= disps
    kills = [e for e in bundle["entries"] if e["event"] == "kill"]
    assert kills and kills[0].get("reason") == "strike_policy" and kills[0].get("strikes") == 3
    revoked_nodes = {n for k in kills for n in k.get("revoked", [])}
    assert any(graph["nodes"][n]["revoked"] for n in revoked_nodes)
    after = [e for e in bundle["entries"] if e["event"] == "deny" and e.get("reason") == "revoked"]
    assert after, "a call after revocation must be denied as revoked"
    for n, meta in graph["nodes"].items():                                  # monotonic: every child narrower than its parent
        if meta["parent"]:
            assert set(meta["scopes"]) <= set(graph["nodes"][meta["parent"]]["scopes"])
    assert rep["scenario"] == "fanout" and rep["agents"] >= 8 and rep["tools"] >= 16


def test_default_scenario_unchanged_and_unknown_scenario_refused(tmp_path, monkeypatch):
    from attenu_derive.sample.demo_local import run_demo
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "T")
    assert run_demo(tmp_path / "proj")["scenario"] == "basic"
    with pytest.raises(ValueError):
        run_demo(tmp_path / "proj", scenario="nope")


def test_fanout_out_of_authority_is_exactly_the_role_violation(tmp_path, monkeypatch):
    """The only out-of-authority denial must be the support agent's CRM write (declared read-only) — not an artefact
    of a parent that forgot to hold what its children need (the T13 class)."""
    from attenu_derive.sample.demo_local import run_demo
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "Fanout Demo")
    rep = run_demo(tmp_path / "proj", scenario="fanout")
    bundle = json.loads(Path(rep["bundle_path"]).read_text())
    ooa = [(r["agent"], r["tool"]) for r in evidence.denials(bundle) if r["disposition"] == "out_of_authority"]
    assert ooa == [("support_agent", "update_crm_note")]
    allowed_by = {}
    for e in bundle["entries"]:
        if e["event"] == "allow":
            allowed_by.setdefault(e.get("node"), set()).add(e.get("tool"))
    graph = evidence.delegation_graph(bundle)
    web_research = next(n for n, m in graph["nodes"].items() if m["agent"] == "web_research")
    assert allowed_by[web_research] == {"fetch_page", "summarize_text"}                    # the children's own work passes
