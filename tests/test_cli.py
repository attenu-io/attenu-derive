"""A1: the `attenu` CLI (packaged path) — public SDK surface + coverage / onboard / verify through the console entry."""
import json
import os

from attenu_derive import Deriver, DelegationEvent, load_domain, __version__
from attenu_derive.cli import build_parser, main, scaffold_pack


def test_public_sdk_surface_imports():
    import attenu_derive
    for name in ("Deriver", "DelegationEvent", "load_catalog", "load_domain", "coverage"):
        assert hasattr(attenu_derive, name), name
    assert __version__ >= "0.1.0"


def test_scaffold_pack_flags_tier2_requires_grant():
    rows = [{"child_calls": [{"tool": "send_care_instructions"}, {"tool": "access_cart_information"}, {"tool": "read_file"}]}]
    pack = scaffold_pack(rows, "my-app")
    assert "read_file" not in pack["tools"]                                  # already curated by the base kit: no entry
    assert pack["tools"]["send_care_instructions"]["requires_grant"] is True  # tier-2 held pending grant
    assert pack["tools"]["access_cart_information"]["scope"] == "data.read"
    assert all("_review" in e for e in pack["tools"].values())               # every entry flagged for review


def test_cli_coverage_and_onboard_run(tmp_path, capsys):
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(json.dumps({"child_calls": [{"tool": "access_cart_information"}, {"tool": "send_care_instructions"}]}) + "\n")
    assert main(["coverage", str(corpus)]) == 0
    out = capsys.readouterr().out; assert '"calls": 2' in out
    scaf = tmp_path / "pack.yaml"
    assert main(["onboard", str(corpus), "--scaffold", str(scaf)]) == 0
    assert scaf.exists() and "requires_grant" in scaf.read_text()


def test_cli_verify_a_real_bundle(tmp_path, capsys):
    from delegation_guard import Authority, Guard, evidence
    from delegation_guard.wire import HS256TestSigner
    key = os.urandom(16); signer = HS256TestSigner(secret=key, kid="k1")
    root = Guard.issue("o", Authority({"crm.read", "agent.delegate.s"}, [], ttl=None), task="t")
    root.delegate("s", Authority({"crm.read"}, [], ttl=None), task="x").check("crm.read", tool="q")
    b = tmp_path / "bundle.json"; b.write_text(json.dumps(evidence.export_bundle(root.audit_log(), signer)))
    assert main(["verify", str(b), "--hs256-key", key.hex()]) == 0
    assert '"ok": true' in capsys.readouterr().out
