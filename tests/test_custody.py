"""Slice 2 / D4 — custody options + out-of-band anchoring.
KMSSigner: the product's anchor key lives in a cloud KMS (AWS-shaped client: sign / get_public_key); the console and
auditors hold only the SPKI public key (shim ECDSAP256Verifier). Stub-tested — honest bound: not run against a real
KMS until an account exists. AnchorScheduler: anchors on a timer, never inside the ledger's append path."""
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from attenu_derive import product
from attenu_derive.signers import KMSSigner


class StubKMS:
    """The two calls we use from an AWS-KMS-shaped client, backed by a local P-256 key so tests are offline."""
    def __init__(self):
        self.sk = ec.generate_private_key(ec.SECP256R1()); self.calls = 0
    def get_public_key(self, KeyId):
        return {"PublicKey": self.sk.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),
                "SigningAlgorithms": ["ECDSA_SHA_256"]}
    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        assert MessageType == "RAW" and SigningAlgorithm == "ECDSA_SHA_256"; self.calls += 1
        return {"Signature": self.sk.sign(Message, ec.ECDSA(hashes.SHA256()))}


def test_kms_signer_anchors_and_the_bundle_verifies_with_the_public_key_only(tmp_path, monkeypatch):
    from delegation_guard import Authority, Guard, evidence
    from delegation_guard.wire import ECDSAP256Verifier
    kms = StubKMS(); signer = KMSSigner(kms, key_id="arn:aws:kms:eu-west-1:123:key/abc", kid="kms-abc")
    g = Guard.issue("a", Authority({"crm.read"}, [], ttl=None), task="t"); g.check("crm.read", tool="q")
    bundle = evidence.export_bundle(g.audit_log(), signer)
    assert kms.calls >= 1 and bundle["anchor"]["kid"] == "kms-abc"
    verifier = ECDSAP256Verifier(signer.public_spki_der(), kid="kms-abc")
    assert evidence.verify_bundle(bundle, verifier)["ok"] is True
    bundle["entries"][-1]["tool"] = "x"
    assert evidence.verify_bundle(bundle, verifier)["ok"] is False


def test_init_with_kms_anchor_stores_only_the_public_key_and_loads_the_right_signer(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home"))
    kms = StubKMS()
    monkeypatch.setattr(product, "_kms_client", lambda region=None: kms)
    meta = product.init_product(tmp_path / "proj", "Bank App", anchor="kms", kms_key_id="arn:aws:kms:eu-west-1:123:key/abc")
    assert meta["anchor_kind"] == "kms" and meta["anchor_alg"] == "ES256" and meta["anchor_pub"] and meta["kms_key_id"].endswith("/abc")
    assert not (tmp_path / "proj" / ".attenu" / "keys" / "anchor.key").exists()            # no private key on disk — it never leaves the HSM
    s = product.load_anchor_signer(tmp_path / "proj"); v = product.load_anchor_verifier(tmp_path / "proj")
    assert isinstance(s, KMSSigner) and v.verify(b"m", s.sign(b"m"))
    # the demo runs end to end on a KMS-anchored product and the bundle verifies with the public key only
    from attenu_derive.sample.demo_local import run_demo
    from delegation_guard import evidence
    rep = run_demo(tmp_path / "proj")
    assert evidence.verify_bundle(json.loads(Path(rep["bundle_path"]).read_text()), v)["ok"] is True


def test_anchor_scheduler_anchors_out_of_band_and_on_stop(tmp_path, monkeypatch):
    from attenu_derive.evidence_out import AnchorScheduler
    from delegation_guard import Authority, Guard, evidence, identity
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "T")
    cid = identity.new_chain_id("long")
    g = Guard.issue("a", Authority({"crm.read"}, [], ttl=None), task="t", chain_id=cid, audit_path=identity.ledger_path(tmp_path / "proj", cid))
    with AnchorScheduler(g, tmp_path / "proj", every_s=0.05) as sched:
        for _ in range(3):
            g.check("crm.read", tool="q"); time.sleep(0.07)
        assert sched.anchors >= 2                                                        # anchored while running, on a timer
    bp = Path(tmp_path / "proj" / ".attenu" / "evidence" / identity.boot_id() / f"{cid}.bundle.json")
    bundle = json.loads(bp.read_text())
    assert bundle["anchor"]["seq"] == len(g.audit_log().entries) - 1                     # the final anchor (on stop) covers everything
    assert evidence.verify_bundle(bundle, product.load_anchor_verifier(tmp_path / "proj"))["ok"] is True
