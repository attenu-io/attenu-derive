"""Slice 1 / Plan A, Task 10 — a product has an identity (and an anchor key) before it has a cloud or a token."""
import json
import stat

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
