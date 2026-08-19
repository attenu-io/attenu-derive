"""Slice 2 / D2 — signed config revisions + ceiling. Every change to what a product may do (grants, declarations,
policy) is a revision: hashed, chained by parent_hash, SIGNED (locally by the product's key; in the cloud by the
Attenu signer), verified before it is applied; grants may never exceed the ceiling; a bad revision is refused and
last-known-good stays. The control plane is attenuated like everything else (Schneier)."""
import json
from pathlib import Path

import pytest

from attenu_derive import product
from attenu_derive import config as cfg


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    product.init_product(tmp_path / "proj", "T")
    return tmp_path / "proj"


def test_init_starts_at_revision_0_and_changes_commit_signed_revisions(proj):
    head = cfg.head(proj)
    assert head["rev"] == 0 and head["grants"] == [] and head["declared_tools"] == {} and head["policy"] == {"unknown_tools": "deny"}
    assert cfg.verify_revision(proj, head) is True                                          # signed at init by the product key
    product.add_grant(proj, "payments.transfer")
    product.declare_tool(proj, "lookup_loyalty_tier", scope="data.read", tier=0)
    product.set_policy(proj, "unknown_tools", "heuristic")
    log = cfg.log(proj)
    assert [r["rev"] for r in log] == [0, 1, 2, 3]
    assert log[1]["grants"] == ["payments.transfer"] and log[2]["declared_tools"]["lookup_loyalty_tier"]["scope"] == "data.read"
    assert log[3]["policy"]["unknown_tools"] == "heuristic"
    for prev, r in zip(log, log[1:]):
        assert r["parent_hash"] == cfg.revision_hash(prev) and cfg.verify_revision(proj, r)
    assert r["signer_kid"] == product.load_product_json(proj)["anchor_kid"] and r["by"]
    # the materialized files are the revision's content (what the runners read)
    assert product.load_grants(proj) == {"payments.transfer"} and product.get_policy(proj)["unknown_tools"] == "heuristic"
    diff = cfg.diff(log[0], log[3])
    assert diff["grants"]["added"] == ["payments.transfer"] and "lookup_loyalty_tier" in diff["declared_tools"]["added"] and diff["policy"]["changed"]["unknown_tools"] == ("deny", "heuristic")


def test_tampered_foreign_and_over_ceiling_revisions_are_refused_and_last_known_good_stays(proj, monkeypatch):
    product.add_grant(proj, "data.write")
    good = cfg.head(proj)
    # tampered content under a valid-looking signature
    bad = dict(good); bad["grants"] = ["data.write", "payments.transfer"]
    with pytest.raises(cfg.RevisionError, match="signature"):
        cfg.apply_revision(proj, bad)
    # signed by a foreign key
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    sk = ed25519.Ed25519PrivateKey.generate()
    priv = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()).hex()
    foreign = cfg.build_revision(parent=good, grants=["data.write", "payments.transfer"], declared_tools={}, policy=good["policy"], by="mallory")
    foreign = cfg.sign_revision(foreign, private_hex=priv, kid="not-ours")
    with pytest.raises(cfg.RevisionError, match="kid"):
        cfg.apply_revision(proj, foreign)
    assert product.load_grants(proj) == {"data.write"} and cfg.head(proj)["rev"] == good["rev"]   # last-known-good stays
    # ceiling: grants may never exceed it
    cfg.set_ceiling(proj, ["data.read", "data.write", "crm.write"])
    with pytest.raises(cfg.RevisionError, match="ceiling"):
        product.add_grant(proj, "payments.transfer")
    assert product.load_grants(proj) == {"data.write"}
    assert product.add_grant(proj, "crm.write") == {"crm.write", "data.write"}                  # within the ceiling: fine
    assert cfg.get_ceiling(proj) == ["crm.write", "data.read", "data.write"]


def test_cloud_revision_applies_on_fast_forward_and_is_kept_out_on_conflict(proj, monkeypatch):
    """A revision signed by the Attenu issuer (the cloud) applies when it extends local HEAD; a diverged one is
    refused (local last-known-good kept) — the engine never silently widens on a conflict."""
    from attenu_derive import license
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    sk = ed25519.Ed25519PrivateKey.generate()
    priv = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()).hex()
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    monkeypatch.setitem(license.ISSUER_KEYS, "cloud-test", pub)
    head = cfg.head(proj)
    cloud_rev = cfg.sign_revision(cfg.build_revision(parent=head, grants=["mail.send"], declared_tools={}, policy=head["policy"], by="alice@bank.test"),
                                  private_hex=priv, kid="cloud-test")
    assert cfg.apply_revision(proj, cloud_rev)["rev"] == 1 and product.load_grants(proj) == {"mail.send"}
    product.add_grant(proj, "data.write")                                                   # local edit -> rev 2
    stale = cfg.sign_revision(cfg.build_revision(parent=cloud_rev, grants=["mail.send", "payments.transfer"], declared_tools={}, policy=head["policy"], by="alice@bank.test"),
                              private_hex=priv, kid="cloud-test")                           # built on rev 1, not on local HEAD (rev 2)
    with pytest.raises(cfg.RevisionError, match="conflict"):
        cfg.apply_revision(proj, stale)
    assert product.load_grants(proj) == {"data.write", "mail.send"} and cfg.head(proj)["rev"] == 2


def test_cli_config_log_and_ceiling(proj, capsys):
    from attenu_derive.cli import main
    product.add_grant(proj, "data.write")
    assert main(["config", "--dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert '"rev": 1' in out and "data.write" in out
    assert main(["ceiling", "--dir", str(proj), "--set", "data.read", "data.write"]) == 0
    assert '"data.write"' in capsys.readouterr().out
    assert main(["ceiling", "--dir", str(proj)]) == 0 and "data.read" in capsys.readouterr().out
