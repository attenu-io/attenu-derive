"""Signed config revisions + ceiling — control with integrity (console design §3 config plane; slice 2 / D2).

Everything an operator decides about what a product may do — operator grants, declared tools, policy — is a
REVISION: a plain dict, hashed (sha256 over canonical JSON without `sig`), chained by `parent_hash`, and SIGNED —
locally by the product's own Ed25519 anchor key (`signer_kid` = the product's `anchor_kid`), in the cloud by the
Attenu signer whose public half the engine already bundles (`license.ISSUER_KEYS`). `apply_revision` verifies the
signature against exactly that keyring, checks the chain (fast-forward from local HEAD, else conflict), checks the
CEILING (`.attenu/ceiling.json`: grants may never exceed `max_scopes`), and only then MATERIALIZES the files the
runners read (`grants.json`, `pack.json`). A bad revision is refused loudly; last-known-good stays. The control
plane is attenuated like everything else — a compromised console cannot push a wider grant than the ceiling, and
cannot push anything unsigned.

Layout: `.attenu/config/<rev>.json`, `.attenu/config/HEAD` (the rev number), `.attenu/ceiling.json`.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from attenu_guard.wire import Ed25519Signer, Ed25519Verifier

__all__ = ["RevisionError", "head", "log", "revision_hash", "build_revision", "sign_revision", "verify_revision",
           "apply_revision", "commit", "diff", "get_ceiling", "set_ceiling", "ensure_initialized"]


class RevisionError(RuntimeError):
    pass


def _dir(product_dir: Path) -> Path:
    return Path(product_dir) / ".attenu" / "config"


def _canonical(body: dict) -> bytes:
    return json.dumps({k: v for k, v in body.items() if k != "sig"}, sort_keys=True, separators=(",", ":")).encode()


def revision_hash(rev: dict) -> str:
    return hashlib.sha256(_canonical(rev)).hexdigest()


def build_revision(*, parent: dict | None, grants, declared_tools: dict, policy: dict, by: str | None) -> dict:
    return {"v": 1, "rev": (parent["rev"] + 1) if parent else 0, "parent_hash": revision_hash(parent) if parent else None,
            "created": int(time.time()), "by": by or os.environ.get("USER") or "operator",
            "grants": sorted(set(grants or ())), "declared_tools": dict(declared_tools or {}), "policy": dict(policy or {})}


def sign_revision(rev: dict, *, private_hex: str | None = None, kid: str, signer=None) -> dict:
    """Sign with a raw Ed25519 private key (hex) or with any `Signer` object (e.g. a KMS signer)."""
    signer = signer or Ed25519Signer.from_private_bytes(bytes.fromhex(private_hex), kid=kid)
    body = {k: v for k, v in rev.items() if k != "sig"}; body["signer_kid"] = kid
    body["sig"] = signer.sign(_canonical(body)).hex()
    return body


def _keyring(product_dir: Path) -> dict[str, str]:
    from attenu_derive import license
    from attenu_derive.product import load_product_json
    ring = dict(license.ISSUER_KEYS)
    try:
        meta = load_product_json(product_dir); ring[meta["anchor_kid"]] = meta["anchor_pub"]
    except Exception:  # noqa: BLE001
        pass
    return ring


def verify_revision(product_dir: Path, rev: dict) -> bool:
    kid = rev.get("signer_kid"); pub = _keyring(product_dir).get(kid)
    if not pub or not rev.get("sig"):
        return False
    try:
        from attenu_derive.product import load_anchor_verifier, load_product_json
        meta = load_product_json(product_dir)
        if kid == meta.get("anchor_kid"):
            return load_anchor_verifier(product_dir).verify(_canonical(rev), bytes.fromhex(rev["sig"]))   # Ed25519 or ES256 (KMS)
        return Ed25519Verifier(bytes.fromhex(pub), kid=kid).verify(_canonical(rev), bytes.fromhex(rev["sig"]))   # the Attenu signer
    except Exception:  # noqa: BLE001
        return False


def head(product_dir: Path) -> dict:
    ensure_initialized(product_dir)
    n = int((_dir(product_dir) / "HEAD").read_text().strip())
    return json.loads((_dir(product_dir) / f"{n}.json").read_text())


def log(product_dir: Path) -> list[dict]:
    ensure_initialized(product_dir)
    n = int((_dir(product_dir) / "HEAD").read_text().strip())
    return [json.loads((_dir(product_dir) / f"{i}.json").read_text()) for i in range(n + 1)]


def get_ceiling(product_dir: Path) -> list[str] | None:
    p = Path(product_dir) / ".attenu" / "ceiling.json"
    return sorted(json.loads(p.read_text()).get("max_scopes", [])) if p.exists() else None


def set_ceiling(product_dir: Path, max_scopes) -> list[str]:
    p = Path(product_dir) / ".attenu" / "ceiling.json"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"max_scopes": sorted(set(max_scopes))}, indent=2))
    return get_ceiling(product_dir)


def _materialize(product_dir: Path, rev: dict) -> None:
    """grants.json holds what THIS installation's runners read: plain grants + the ones tagged for its environment
    (`scope@env`), tag stripped. The revision keeps the full tagged truth."""
    from attenu_derive.product import load_product_json
    att = Path(product_dir) / ".attenu"
    my_env = (load_product_json(product_dir) or {}).get("environment")
    mine = set()
    for g in rev["grants"]:
        scope, _, env = g.partition("@")
        if not env or env == my_env:
            mine.add(scope)
    (att / "grants.json").write_text(json.dumps({"operator_grants": sorted(mine)}, indent=2))
    (att / "pack.json").write_text(json.dumps({"tools": rev["declared_tools"], "policy": rev["policy"]}, indent=2))


def apply_revision(product_dir: Path, rev: dict) -> dict:
    """Verify signature → chain → ceiling → materialize. Raises RevisionError (last-known-good untouched)."""
    product_dir = Path(product_dir)
    if not verify_revision(product_dir, rev):
        kid = rev.get("signer_kid")
        if kid not in _keyring(product_dir):
            raise RevisionError(f"revision signed by unknown kid {kid!r} — refused")
        raise RevisionError("revision signature invalid — refused (tampered?)")
    cur = head(product_dir)
    if rev.get("rev") != cur["rev"] + 1 or rev.get("parent_hash") != revision_hash(cur):
        raise RevisionError(f"revision conflict: rev {rev.get('rev')} does not extend local HEAD rev {cur['rev']} — keeping last-known-good; resolve in the console")
    ceiling = get_ceiling(product_dir)
    if ceiling is not None:
        over = sorted({g.partition("@")[0] for g in rev.get("grants", [])} - set(ceiling))   # the ceiling caps the SCOPE, any env
        if over:
            raise RevisionError(f"revision grants {over} exceed the ceiling {ceiling} — refused")
    d = _dir(product_dir); d.mkdir(parents=True, exist_ok=True)
    (d / f"{rev['rev']}.json").write_text(json.dumps(rev, indent=2))
    (d / "HEAD").write_text(str(rev["rev"]))
    _materialize(product_dir, rev)
    return rev


def commit(product_dir: Path, *, grants=None, declared_tools=None, policy=None, by: str | None = None) -> dict:
    """Build rev HEAD+1 from the given content (None = unchanged), sign with the product key, apply."""
    from attenu_derive.product import load_anchor_signer, load_product_json
    cur = head(product_dir)
    rev = build_revision(parent=cur, grants=cur["grants"] if grants is None else grants,
                         declared_tools=cur["declared_tools"] if declared_tools is None else declared_tools,
                         policy=cur["policy"] if policy is None else policy, by=by)
    meta = load_product_json(product_dir); signer = load_anchor_signer(product_dir)
    signed = sign_revision(rev, kid=meta["anchor_kid"], signer=signer)
    return apply_revision(product_dir, signed)


def ensure_initialized(product_dir: Path) -> None:
    """Revision 0 = the product's current files (or empty), signed by the product key — created once, lazily."""
    d = _dir(product_dir)
    if (d / "HEAD").exists():
        return
    from attenu_derive.product import POLICY_DEFAULTS, load_anchor_signer, load_product_json
    att = Path(product_dir) / ".attenu"
    grants = json.loads((att / "grants.json").read_text()).get("operator_grants", []) if (att / "grants.json").exists() else []
    pack = json.loads((att / "pack.json").read_text()) if (att / "pack.json").exists() else {}
    policy = {**POLICY_DEFAULTS, **(pack.get("policy") or {})}
    rev = build_revision(parent=None, grants=grants, declared_tools=pack.get("tools") or {}, policy=policy, by="init")
    meta = load_product_json(product_dir); signer = load_anchor_signer(product_dir)
    signed = sign_revision(rev, kid=meta["anchor_kid"], signer=signer)
    d.mkdir(parents=True, exist_ok=True)
    (d / "0.json").write_text(json.dumps(signed, indent=2)); (d / "HEAD").write_text("0")
    _materialize(product_dir, signed)


def diff(a: dict, b: dict) -> dict:
    ga, gb = set(a.get("grants", [])), set(b.get("grants", []))
    ta, tb = a.get("declared_tools", {}), b.get("declared_tools", {})
    pa, pb = a.get("policy", {}), b.get("policy", {})
    return {"grants": {"added": sorted(gb - ga), "removed": sorted(ga - gb)},
            "declared_tools": {"added": sorted(set(tb) - set(ta)), "removed": sorted(set(ta) - set(tb)),
                               "changed": sorted(k for k in set(ta) & set(tb) if ta[k] != tb[k])},
            "policy": {"changed": {k: (pa.get(k), pb.get(k)) for k in set(pa) | set(pb) if pa.get(k) != pb.get(k)}}}
