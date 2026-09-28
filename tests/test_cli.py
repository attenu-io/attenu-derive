"""A1: the `attenu` CLI (packaged path) — public SDK surface + coverage / onboard / verify through the console entry."""
import json
import os
from pathlib import Path

import pytest

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


def test_cli_ui_without_console_installed_says_what_works_today(monkeypatch, capsys):
    import builtins
    from attenu_derive.cli import main
    real = builtins.__import__
    def fake(name, *a, **k):
        if name.startswith("attenu_console"):
            raise ImportError("nope")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    err = capsys.readouterr().err if main(["ui"]) == 2 else pytest.fail("attenu ui should exit 2 without the console")
    assert "not published yet" in err and "attenu report" in err
    assert "attenu-console" not in err and "https://" not in err     # no pointer to a package that is not on PyPI


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
    except ImportError:                                   # without it, the open engine says the truth
        assert rc == 2 and "not published yet" in out.err


def test_cli_policy_show_and_set(tmp_path, monkeypatch, capsys):
    from attenu_derive.cli import main
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    assert main(["init", "--product", "T", "--dir", str(tmp_path / "proj")]) == 0
    capsys.readouterr()
    assert main(["policy", "--dir", str(tmp_path / "proj")]) == 0 and '"unknown_tools": "deny"' in capsys.readouterr().out
    assert main(["policy", "--dir", str(tmp_path / "proj"), "--unknown-tools", "heuristic"]) == 0
    assert '"unknown_tools": "heuristic"' in capsys.readouterr().out


def test_cli_verify_defaults_to_the_products_anchor_key(tmp_path, monkeypatch, capsys):
    """Inside a product directory, `attenu verify <bundle>` needs no key: the product's own anchor verifies it."""
    from attenu_derive.cli import main
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    d = tmp_path / "proj"
    assert main(["init", "--product", "T", "--dir", str(d)]) == 0
    assert main(["demo", "--scenario", "fanout", "--dir", str(d)]) == 0
    capsys.readouterr()
    bundles = sorted((d / ".attenu" / "evidence").rglob("*.bundle.json"))
    assert bundles, "the demo should have exported a bundle"
    assert main(["verify", str(bundles[-1]), "--dir", str(d)]) == 0
    assert '"ok": true' in capsys.readouterr().out


def test_cli_init_without_product_names_it_after_the_directory(tmp_path, monkeypatch, capsys):
    """The README's first command: `attenu init` with no flags works and names the product after its directory."""
    import json
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    d = tmp_path / "my-app"; d.mkdir()
    assert main(["init", "--dir", str(d)]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "my-app"
    assert json.loads((d / ".attenu" / "product.json").read_text())["name"] == "my-app"


def test_cli_init_without_product_keeps_an_existing_name(tmp_path, monkeypatch, capsys):
    import json
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    d = tmp_path / "proj"
    assert main(["init", "--product", "Mortgage Assistant", "--dir", str(d)]) == 0
    pid = json.loads(capsys.readouterr().out)["product_id"]
    assert main(["init", "--dir", str(d)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "Mortgage Assistant" and out["product_id"] == pid


def test_cli_demo_without_a_product_says_run_init_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    d = tmp_path / "proj"; d.mkdir()
    assert main(["demo", "--scenario", "fanout", "--dir", str(d)]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "attenu init" in out.err and len(out.err.strip().splitlines()) == 1
    assert "Traceback" not in out.err
    assert not (d / ".attenu").exists(), "nothing may be written before the product check"


def test_run_demo_without_a_product_refuses_before_writing(tmp_path):
    from attenu_derive.sample.demo_local import run_demo
    d = tmp_path / "proj"; d.mkdir()
    with pytest.raises(FileNotFoundError, match="attenu init"):
        run_demo(d)
    assert not (d / ".attenu").exists()


def test_cli_demo_ends_with_the_next_commands_and_real_paths(tmp_path, monkeypatch, capsys):
    """The last lines the reader sees are the three next commands, with the paths the demo just wrote; they run."""
    import json
    import shlex
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    d = tmp_path / "proj"
    assert main(["init", "--dir", str(d)]) == 0
    capsys.readouterr()
    assert main(["demo", "--scenario", "fanout", "--dir", str(d)]) == 0
    out = capsys.readouterr()
    rep = json.loads(out.out)                                         # stdout stays pure JSON for anything piping it
    tail = [l.strip() for l in out.err.strip().splitlines()]
    assert tail[0] == "next:" and len(tail) == 4
    view, verify, report = (shlex.split(l) for l in tail[1:])
    assert view == ["attenu-guard", "view", rep["ledger_path"]]
    assert verify == ["attenu", "verify", rep["bundle_path"], "--dir", str(d)]
    assert report == ["attenu", "report", "--dir", str(d)]
    assert Path(rep["ledger_path"]).is_file() and Path(rep["bundle_path"]).is_file()
    assert main(verify[1:]) == 0 and '"ok": true' in capsys.readouterr().out
    assert main(report[1:]) == 0 and "index.html" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["ui"], ["link", "--token", "t"], ["sync"]])
def test_console_commands_without_the_console_say_not_published_and_name_no_package(argv, tmp_path, monkeypatch, capsys):
    """ui / link / sync without the unpublished console packages: exit 2, one truthful line, no package name, no URL."""
    import builtins
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    real = builtins.__import__
    def fake(name, *a, **k):
        if name.startswith(("attenu_console", "attenu_cloud")):
            raise ImportError("nope")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert main(argv + ["--dir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "not published yet" in err and len(err.strip().splitlines()) == 1
    assert "attenu-console" not in err and "http" not in err and "attenu.io" not in err


@pytest.mark.parametrize("cmd", ["ui", "link", "sync"])
def test_console_commands_help_names_no_package_or_url(cmd):
    sub = next(a for a in build_parser()._actions if a.dest == "cmd")
    help_text = next(c.help for c in sub._choices_actions if c.dest == cmd)
    assert "not published yet" in help_text
    assert "attenu-console" not in help_text and "http" not in help_text
