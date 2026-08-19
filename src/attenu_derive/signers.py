"""Custody options for the product's anchor key (console design §7).

  LocalKeySigner   = the shim's Ed25519Signer loaded from `.attenu/keys/anchor.key` (0600) — laptop / VPC.
  KMSSigner        = the key lives in a cloud KMS and NEVER leaves the HSM; we call `sign` per anchor and publish
                     only the SPKI public key. AWS-KMS-shaped client (`sign`, `get_public_key`); ECDSA P-256 / SHA-256
                     because cloud KMSs have no Ed25519. Verifiers (console, auditors) use the shim's
                     `ECDSAP256Verifier` and need no cloud SDK. boto3 is an optional extra; tests use a stub client.
"""
from __future__ import annotations

__all__ = ["KMSSigner", "kms_client"]


def kms_client(region: str | None = None):
    try:
        import boto3  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("KMS anchoring needs boto3: pip install 'attenu-derive[kms]'") from exc
    return boto3.client("kms", region_name=region) if region else boto3.client("kms")


class KMSSigner:
    alg = "ES256"

    def __init__(self, client, *, key_id: str, kid: str):
        self._c = client; self.key_id = key_id; self.kid = kid
        self._spki = None

    def public_spki_der(self) -> bytes:
        if self._spki is None:
            self._spki = self._c.get_public_key(KeyId=self.key_id)["PublicKey"]
        return self._spki

    def sign(self, signing_input: bytes) -> bytes:
        return self._c.sign(KeyId=self.key_id, Message=signing_input, MessageType="RAW", SigningAlgorithm="ECDSA_SHA_256")["Signature"]

    def verify(self, signing_input: bytes, sig: bytes, key_id: str | None = None) -> bool:
        from delegation_guard.wire import ECDSAP256Verifier
        return ECDSAP256Verifier(self.public_spki_der(), kid=self.kid).verify(signing_input, sig, key_id)
