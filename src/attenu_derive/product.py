"""Product identity + local anchor-key custody (console design §5a, §7).

A product has an identity BEFORE it has a token or a cloud — `attenu init` writes `.attenu/product.json` (a ULID,
a name, an environment) and a product-local Ed25519 **anchor key** (`.attenu/keys/anchor.key`, mode 0600; the
public half is in `product.json`). Several products on one machine are several directories; a machine-level
registry (in the Attenu home config dir, `ATTENU_HOME` to override) lists the ones this machine has seen so the
local console can offer a product switcher. No key, no network, no derivation here.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from delegation_guard.wire import Ed25519Signer, Ed25519Verifier

__all__ = ["home_dir", "registry_path", "registry_list", "registry_add", "init_product", "load_product_json",
           "load_anchor_signer", "load_anchor_verifier"]

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    ts = int(time.time() * 1000); head = ""
    for _ in range(10):
        head = _CROCKFORD[ts & 31] + head; ts >>= 5
    return head + "".join(secrets.choice(_CROCKFORD) for _ in range(16))


def home_dir() -> Path:
    return Path(os.environ.get("ATTENU_HOME") or (Path.home() / ".attenu"))


def registry_path() -> Path:
    return home_dir() / "registry.json"


def registry_list() -> list[dict]:
    p = registry_path()
    return json.loads(p.read_text()).get("products", []) if p.exists() else []


def registry_add(product_dir: Path, meta: dict) -> None:
    p = registry_path(); p.parent.mkdir(parents=True, exist_ok=True)
    key = str(Path(product_dir).resolve())
    items = [r for r in registry_list() if r.get("dir") != key]
    items.append({"dir": key, "product_id": meta["product_id"], "name": meta["name"], "environment": meta["environment"]})
    p.write_text(json.dumps({"products": items}, indent=2))


def init_product(product_dir: Path, name: str, environment: str = "dev") -> dict:
    """Create (or re-create) a product identity in `product_dir`: product.json + a fresh anchor keypair."""
    product_dir = Path(product_dir); att = product_dir / ".attenu"
    (att / "keys").mkdir(parents=True, exist_ok=True)
    existing = load_product_json(product_dir) if (att / "product.json").exists() else None
    signer = Ed25519Signer.generate(kid=f"anchor-{secrets.token_hex(4)}")
    key = att / "keys" / "anchor.key"
    key.write_text(signer.private_bytes_raw().hex()); os.chmod(key, 0o600)
    meta = {"product_id": (existing or {}).get("product_id") or _ulid(),   # re-init keeps the identity, rotates the key
            "name": name, "environment": environment, "created": (existing or {}).get("created") or int(time.time()),
            "anchor_kid": signer.kid, "anchor_pub": signer.public_bytes_raw().hex()}
    (att / "product.json").write_text(json.dumps(meta, indent=2))
    registry_add(product_dir, meta)
    return meta


def load_product_json(product_dir: Path) -> dict:
    return json.loads((Path(product_dir) / ".attenu" / "product.json").read_text())


def load_anchor_signer(product_dir: Path) -> Ed25519Signer:
    meta = load_product_json(product_dir)
    raw = bytes.fromhex((Path(product_dir) / ".attenu" / "keys" / "anchor.key").read_text().strip())
    return Ed25519Signer.from_private_bytes(raw, kid=meta["anchor_kid"])


def load_anchor_verifier(product_dir: Path) -> Ed25519Verifier:
    meta = load_product_json(product_dir)
    return Ed25519Verifier(bytes.fromhex(meta["anchor_pub"]), kid=meta["anchor_kid"])
