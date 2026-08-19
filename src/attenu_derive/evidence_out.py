"""Evidence output shared by every runner and by `attenu demo` — framework-free on purpose (no google.adk, no
crewai here), so a console or a demo can import it with nothing but the shim and this package installed.

Custody (console design §7): INSIDE a product the anchor is signed with the product's own Ed25519 key
(`attenu init`), the bundle is written under `.attenu/evidence/<boot>/<chain_id>.bundle.json`, and it verifies with
the product's PUBLIC key; OUTSIDE a product the ephemeral HMAC test signer is used and the report says so
("attenu-anchor-TEST") so it can never be mistaken for custody.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from delegation_guard import AuditLog, evidence, identity
from delegation_guard.wire import HS256TestSigner

__all__ = ["write_evidence", "effective_grants", "product_meta"]


def product_meta(product_dir) -> dict:
    from attenu_derive.product import load_product_json
    return load_product_json(product_dir)


def effective_grants(cli_grants: set[str], product_dir) -> set[str]:
    """CLI `--grant`s plus the product's operator grants (`.attenu/grants.json`, written by the console's
    Decisions screen) — so a grant decided in the console reaches the live runner without a flag."""
    g = set(cli_grants or ())
    if product_dir is not None:
        from attenu_derive.product import load_grants
        g |= load_grants(product_dir)
    return g


def write_evidence(root_guard, product_dir) -> dict:
    """Anchor + export the offline evidence bundle and verify it from the bundle ALONE (no engine)."""
    if product_dir is not None:
        from attenu_derive.product import load_anchor_signer, load_anchor_verifier
        signer = load_anchor_signer(product_dir); verifier = load_anchor_verifier(product_dir)
    else:
        signer = HS256TestSigner(secret=os.urandom(16), kid="attenu-anchor-TEST"); verifier = signer
    log = root_guard.audit_log(); entries = log.entries
    bundle = evidence.export_bundle(log, signer)
    bundle_check = evidence.verify_bundle(bundle, verifier)
    anchor = log.anchor(signer)
    anchor_ok, _ = AuditLog.verify_anchor(entries, anchor, verifier)
    spawns = [e for e in entries if e.get("event") == "spawn"]
    bundle_path = None
    if product_dir is not None:
        out = Path(product_dir) / ".attenu" / "evidence" / identity.boot_id() / f"{root_guard.chain_id}.bundle.json"
        out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(bundle, indent=2)); bundle_path = str(out)
    return {"anchor": {"seq": anchor["seq"], "head": anchor["head"], "verified": anchor_ok, "covers_chain": len(spawns) > 0},
            "anchor_kid": anchor.get("kid"), "ledger_path": str(log.path) if log.path else None, "bundle_path": bundle_path,
            "evidence_bundle_offline_verify": bundle_check, "delegation_graph_view": evidence.delegation_graph(bundle),
            "denials_view": evidence.denials(bundle)}
