"""T-KMS-LIVE (ran 2026-08-19, PASSED): the anchor custody seam against a REAL AWS KMS key
(ECC_NIST_P256, SIGN_VERIFY; created with `aws kms create-key --key-spec ECC_NIST_P256 --key-usage SIGN_VERIFY`,
validated, then scheduled for deletion — a customer runs this against THEIR key: `python tools/kms_live_validation.py <key-id>`).
Result: HSM sign + offline verify (integrity · monotonicity · containment) + tamper detection, with the verifier
holding only the SPKI public half — no cloud SDK. The anchor custody seam — the key never leaves the HSM; the anchor
verifies OFFLINE with the shim's ECDSAP256Verifier (no cloud SDK on the verifier side)."""
import json, sys, tempfile
from pathlib import Path

from attenu_derive.signers import KMSSigner, kms_client
from delegation_guard.wire import ECDSAP256Verifier
from delegation_guard import evidence

KEY_ID = sys.argv[1]
client = kms_client("us-east-1")
signer = KMSSigner(client, key_id=KEY_ID, kid="kms-live-1")

# 1) raw sign/verify round-trip through the HSM
msg = b"attenu kms live validation"
sig = signer.sign(msg)
assert signer.verify(msg, sig), "HSM signature did not verify"
assert not signer.verify(b"tampered", sig), "tampered message verified (!)"

# 2) a REAL ledger anchored via KMS, then verified offline from the bundle alone
from delegation_guard import Guard, Authority
tmp = Path(tempfile.mkdtemp())
g = Guard.issue("root", Authority(scopes={"data.read", "mail.send"}), audit_path=tmp / "ledger.jsonl")
g.check("data.read", tool="read_db")
try:
    g.check("payments.transfer", tool="charge_card")
except Exception:
    pass
bundle = evidence.export_bundle(g.audit_log(), signer)
ver = ECDSAP256Verifier(signer.public_spki_der(), kid="kms-live-1")     # offline: public half only
out = evidence.verify_bundle(bundle, ver)
assert out["ok"], f"bundle failed offline verification: {out['failures']}"
# tamper -> fails
bad = json.loads(json.dumps(bundle)); bad["entries"][0]["scope"] = "root.everything"
assert not evidence.verify_bundle(bad, ver)["ok"], "tampered bundle verified (!)"
print("KMS-LIVE OK: HSM sign + offline verify + tamper detection", out["checks"])
