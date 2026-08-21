"""Slice 2 / D3 — the evidence report: a printable HTML rendered from the bundle + its verification (a fold; no
engine). It must show the three checks, the anchor key, every agent with its authority and its parent, every denial
with its disposition in the user's words, and how to re-verify; it fails CLOSED on a bad bundle."""
import json
from pathlib import Path

from attenu_derive import product, report
from attenu_derive.sample.demo_local import run_demo
from delegation_guard import evidence


def _chain(tmp_path, monkeypatch, scenario="fanout"):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "Fanout Demo")
    rep = run_demo(tmp_path / "proj", scenario=scenario)
    bundle = json.loads(Path(rep["bundle_path"]).read_text())
    return tmp_path / "proj", bundle, rep


def test_chain_report_contains_checks_agents_denials_and_reverify_instructions(tmp_path, monkeypatch):
    d, bundle, rep = _chain(tmp_path, monkeypatch)
    verifier = product.load_anchor_verifier(d)
    html = report.render_chain_report(bundle, evidence.verify_bundle(bundle, verifier), product.load_product_json(d))
    for needle in ("verified", "integrity", "monotonicity", "containment", product.load_product_json(d)["anchor_kid"],
                   "trip_planner", "reviews_scout", "support_agent", "⊂", "request held", "out of authority", "unresolved",
                   "revoked", "book_flight", "update_crm_note", "attenu verify", "--pubkey"):
        assert needle in html, needle
    assert html.count("<tr") >= 9                                             # one row per agent at least
    assert "<script" not in html                                              # printable, static, no scripts


def test_report_fails_closed_on_a_tampered_bundle(tmp_path, monkeypatch):
    d, bundle, rep = _chain(tmp_path, monkeypatch, scenario="basic")
    bundle["entries"][-1]["tool"] = "tampered"
    html = report.render_chain_report(bundle, evidence.verify_bundle(bundle, product.load_anchor_verifier(d)), product.load_product_json(d))
    assert "verification failed" in html and "✗" in html


def test_write_report_and_cli(tmp_path, monkeypatch, capsys):
    d, bundle, rep = _chain(tmp_path, monkeypatch, scenario="basic")
    out = report.write_chain_report(d, Path(rep["bundle_path"]))
    assert out.name.endswith(".report.html") and out.read_text().startswith("<!doctype html>")
    from attenu_derive.cli import main
    assert main(["report", "--dir", str(d)]) == 0
    assert ".report.html" in capsys.readouterr().out
    prod_html = report.render_product_report(d)
    assert "Fanout Demo" in prod_html and "chains" in prod_html.lower()
