"""Slice 1 / Plan B, Task B1 — a USD-0 scripted run that writes a REAL ledger (no model, no API key) so the
console demo never depends on a key: a planner delegates to a booking agent; reads pass, the payment is HELD,
an unknown tool is UNRESOLVED; the bundle is anchored with the product key and verifies with its public key."""
import json
from pathlib import Path

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
