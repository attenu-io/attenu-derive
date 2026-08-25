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
    from attenu_guard import Authority, Guard, evidence
    from attenu_guard.wire import HS256TestSigner
    key = os.urandom(16); signer = HS256TestSigner(secret=key, kid="k1")
    root = Guard.issue("o", Authority({"crm.read", "agent.delegate.s"}, [], ttl=None), task="t")
    root.delegate("s", Authority({"crm.read"}, [], ttl=None), task="x").check("crm.read", tool="q")
    b = tmp_path / "bundle.json"; b.write_text(json.dumps(evidence.export_bundle(root.audit_log(), signer)))
    assert main(["verify", str(b), "--hs256-key", key.hex()]) == 0
    assert '"ok": true' in capsys.readouterr().out


def test_cli_init_and_products(tmp_path, monkeypatch, capsys):
    from attenu_derive.cli import main
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    assert main(["init", "--product", "Mortgage Assistant", "--env", "dev", "--dir", str(tmp_path / "proj")]) == 0
    assert (tmp_path / "proj" / ".attenu" / "product.json").exists()
    capsys.readouterr()
    assert main(["products"]) == 0 and "Mortgage Assistant" in capsys.readouterr().out


def test_cli_verify_with_a_public_key(tmp_path, monkeypatch, capsys):
    import json
    from attenu_derive import product
    from attenu_derive.cli import main
    from attenu_guard import Authority, Guard, evidence
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    meta = product.init_product(tmp_path / "proj", "CS")
    g = Guard.issue("a", Authority({"crm.read"}, [], ttl=None), task="t"); g.check("crm.read", tool="q")
    bundle = evidence.export_bundle(g.audit_log(), product.load_anchor_signer(tmp_path / "proj"))
    (tmp_path / "b.json").write_text(json.dumps(bundle))
    assert main(["verify", str(tmp_path / "b.json"), "--pubkey", meta["anchor_pub"], "--kid", meta["anchor_kid"]]) == 0
    bundle["entries"][-1]["tool"] = "tampered"; (tmp_path / "b2.json").write_text(json.dumps(bundle))
    assert main(["verify", str(tmp_path / "b2.json"), "--pubkey", meta["anchor_pub"], "--kid", meta["anchor_kid"]]) == 1


def test_cli_ui_without_console_installed_says_how_to_get_it(monkeypatch, capsys):
    import builtins
    from attenu_derive.cli import main
    real = builtins.__import__
    def fake(name, *a, **k):
        if name.startswith("attenu_console"):
            raise ImportError("nope")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert main(["ui"]) == 2 and "attenu-console" in capsys.readouterr().err


def test_cli_demo_writes_a_chain_into_the_product(tmp_path, monkeypatch, capsys):
    from attenu_derive.cli import main
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    assert main(["init", "--product", "Travel Demo", "--dir", str(tmp_path / "proj")]) == 0
    capsys.readouterr()
    assert main(["demo", "--dir", str(tmp_path / "proj")]) == 0
    out = capsys.readouterr().out
    assert "held_pending_grant" in out and list((tmp_path / "proj" / ".attenu" / "ledger").glob("*/*.jsonl"))


def test_cli_sync_on_an_unlinked_product_says_so(tmp_path, monkeypatch, capsys):
    from attenu_derive.cli import main
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    assert main(["init", "--product", "T", "--dir", str(tmp_path / "proj")]) == 0
    capsys.readouterr()
    rc = main(["sync", "--dir", str(tmp_path / "proj")]); out = capsys.readouterr()
    try:
        import attenu_cloud  # noqa: F401 — the optional client: with it, sync reports "not linked"
        assert rc == 1 and "not linked" in out.out
    except ImportError:                                   # without it, the open engine says what to install
        assert rc == 2 and "cloud client" in out.err


def test_cli_policy_show_and_set(tmp_path, monkeypatch, capsys):
    from attenu_derive.cli import main
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    assert main(["init", "--product", "T", "--dir", str(tmp_path / "proj")]) == 0
    capsys.readouterr()
    assert main(["policy", "--dir", str(tmp_path / "proj")]) == 0 and '"unknown_tools": "deny"' in capsys.readouterr().out
    assert main(["policy", "--dir", str(tmp_path / "proj"), "--unknown-tools", "heuristic"]) == 0
    assert '"unknown_tools": "heuristic"' in capsys.readouterr().out
