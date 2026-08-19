"""Product identity + local anchor-key custody (console design §5a, §7).

A product has an identity BEFORE it has a token or a cloud — `attenu init` writes `.attenu/product.json` (a ULID,
a name, an environment) and a product-local Ed25519 **anchor key** (`.attenu/keys/anchor.key`, mode 0600; the
public half is in `product.json`). Several products on one machine are several directories; a machine-level
registry (in the Attenu home config dir, `ATTENU_HOME` to override) lists the ones this machine has seen so the
local console can offer a product switcher. No key, no network, no derivation here.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from delegation_guard.wire import ECDSAP256Verifier, Ed25519Signer, Ed25519Verifier

__all__ = ["home_dir", "registry_path", "registry_list", "registry_add", "init_product", "load_product_json",
           "load_anchor_signer", "load_anchor_verifier", "grants_path", "load_grants", "add_grant", "remove_grant", "grant_key", "note_run", "run_meta",
           "pack_path", "load_pack", "declare_tool", "effective_domain", "get_policy", "set_policy", "POLICY_DEFAULTS", "POLICY_CHOICES"]

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


def _kms_client(region: str | None = None):
    from attenu_derive.signers import kms_client
    return kms_client(region)


def init_product(product_dir: Path, name: str, environment: str = "dev", *, anchor: str = "local",
                 kms_key_id: str | None = None, kms_region: str | None = None) -> dict:
    """Create (or re-create) a product identity in `product_dir`: product.json + the anchor key custody of choice —
    `local` (a fresh Ed25519 keypair, private half in .attenu/keys/anchor.key, 0600) or `kms` (the key stays in a cloud
    KMS; only its SPKI public key is recorded — nothing private ever touches disk)."""
    product_dir = Path(product_dir); att = product_dir / ".attenu"
    (att / "keys").mkdir(parents=True, exist_ok=True)
    existing = load_product_json(product_dir) if (att / "product.json").exists() else None
    base = {"product_id": (existing or {}).get("product_id") or _ulid(),   # re-init keeps the identity, rotates the key
            "name": name, "environment": environment, "created": (existing or {}).get("created") or int(time.time())}
    if anchor == "kms":
        if not kms_key_id:
            raise ValueError("anchor='kms' needs kms_key_id")
        from attenu_derive.signers import KMSSigner
        kid = f"kms-{hashlib.sha256(kms_key_id.encode()).hexdigest()[:8]}"
        signer = KMSSigner(_kms_client(kms_region), key_id=kms_key_id, kid=kid)
        key = att / "keys" / "anchor.key"
        if key.exists():
            key.unlink()                                              # switching to KMS: no private key on disk, ever
        meta = {**base, "anchor_kind": "kms", "anchor_alg": "ES256", "anchor_kid": kid, "anchor_pub": signer.public_spki_der().hex(),
                "kms_key_id": kms_key_id, **({"kms_region": kms_region} if kms_region else {})}
    elif anchor == "local":
        signer = Ed25519Signer.generate(kid=f"anchor-{secrets.token_hex(4)}")
        key = att / "keys" / "anchor.key"
        key.write_text(signer.private_bytes_raw().hex()); os.chmod(key, 0o600)
        meta = {**base, "anchor_kind": "local", "anchor_alg": "EdDSA", "anchor_kid": signer.kid, "anchor_pub": signer.public_bytes_raw().hex()}
    else:
        raise ValueError("anchor must be 'local' or 'kms'")
    (att / "product.json").write_text(json.dumps(meta, indent=2))
    registry_add(product_dir, meta)
    from attenu_derive import config as _cfg
    if existing is None or not (att / "config" / "HEAD").exists():
        _cfg.ensure_initialized(product_dir)                       # revision 0, signed by the product key
    return meta


def load_product_json(product_dir: Path) -> dict:
    return json.loads((Path(product_dir) / ".attenu" / "product.json").read_text())


def load_anchor_signer(product_dir: Path):
    meta = load_product_json(product_dir)
    if meta.get("anchor_kind") == "kms":
        from attenu_derive.signers import KMSSigner
        return KMSSigner(_kms_client(meta.get("kms_region")), key_id=meta["kms_key_id"], kid=meta["anchor_kid"])
    raw = bytes.fromhex((Path(product_dir) / ".attenu" / "keys" / "anchor.key").read_text().strip())
    return Ed25519Signer.from_private_bytes(raw, kid=meta["anchor_kid"])


def load_anchor_verifier(product_dir: Path):
    """Public-key-only verifier for the product's anchors — Ed25519 for local keys, ECDSA P-256 (ES256) for KMS."""
    meta = load_product_json(product_dir)
    if meta.get("anchor_kind") == "kms" or meta.get("anchor_alg") == "ES256":
        return ECDSAP256Verifier(bytes.fromhex(meta["anchor_pub"]), kid=meta["anchor_kid"])
    return Ed25519Verifier(bytes.fromhex(meta["anchor_pub"]), kid=meta["anchor_kid"])


# ---- operator grants (the console's "Grant scope" decision lands here; the runners read it) -------------------
def grants_path(product_dir: Path) -> Path:
    return Path(product_dir) / ".attenu" / "grants.json"


def load_grants(product_dir: Path) -> set[str]:
    p = grants_path(product_dir)
    return set(json.loads(p.read_text()).get("operator_grants", [])) if p.exists() else set()


def grant_key(scope: str, env: str | None) -> str:
    """A grant entry in the signed revision: plain `scope` = every environment; `scope@env` = that environment only.
    A string convention on the unchanged revision schema — old verifiers keep verifying."""
    return scope if not env else f"{scope}@{env}"


def add_grant(product_dir: Path, scope: str, *, by: str | None = None, env: str | None = None) -> set[str]:
    """Idempotently record an operator grant for `scope` — as a SIGNED config revision (see attenu_derive.config);
    refused if it exceeds the product's ceiling. `env` scopes the grant to one environment (None = all).
    Returns the set THIS installation's runners read. One flip, one revision."""
    from attenu_derive import config as _cfg
    key = grant_key(scope, env)
    g = set(_cfg.head(product_dir)["grants"])                             # the signed truth, unfiltered
    if key in g:
        return load_grants(product_dir)
    _cfg.commit(product_dir, grants=sorted(g | {key}), by=by)
    return load_grants(product_dir)


def remove_grant(product_dir: Path, scope: str, *, by: str | None = None, env: str | None = None) -> set[str]:
    """The revert of add_grant — a SIGNED revision that removes the grant (the diff shows `- revoked grant`), so the
    runners stop seeing the scope on their next pull. Idempotent: removing what was never granted commits nothing."""
    from attenu_derive import config as _cfg
    key = grant_key(scope, env)
    g = set(_cfg.head(product_dir)["grants"])
    if key not in g:
        return load_grants(product_dir)
    _cfg.commit(product_dir, grants=sorted(g - {key}), by=by)
    return load_grants(product_dir)


# ---- per-process run metadata (what a heartbeat says about an app process: framework, mode) -------------------
def note_run(product_dir: Path, boot_id: str, *, framework: str | None, mode: str | None) -> None:
    d = Path(product_dir) / ".attenu" / "runs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{boot_id}.json").write_text(json.dumps({"framework": framework, "mode": mode, "started": int(time.time())}))


def run_meta(product_dir: Path, boot_id: str) -> dict:
    p = Path(product_dir) / ".attenu" / "runs" / f"{boot_id}.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ---- product-local pack overlay: "declare" a tool the catalog does not know (the console's Declare decision) -----
import re as _re
_SCOPE_RE = _re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_*]+)+$")


def pack_path(product_dir: Path) -> Path:
    return Path(product_dir) / ".attenu" / "pack.json"


def load_pack(product_dir: Path) -> dict:
    p = pack_path(product_dir)
    return json.loads(p.read_text()) if p.exists() else {"tools": {}}


def declare_tool(product_dir: Path, tool: str, *, scope: str, tier: int = 0, requires_grant: bool | None = None) -> dict:
    """Curate `tool` -> `scope` into the product's pack overlay. Tier 2 is NEVER auto-granted (requires_grant=True
    unless explicitly overridden); the operator still grants it in Decisions. Validates the scope shape."""
    if not isinstance(scope, str) or not _SCOPE_RE.match(scope):
        raise ValueError(f"scope must look like family.action (got {scope!r})")
    if tier not in (0, 1, 2):
        raise ValueError("tier must be 0, 1 or 2")
    entry: dict = {"scope": scope, "tier": int(tier)}
    if tier == 2 and requires_grant is not False:
        entry["requires_grant"] = True
    from attenu_derive import config as _cfg
    tools = dict(load_pack(product_dir).get("tools") or {}); tools[tool] = entry
    _cfg.commit(product_dir, declared_tools=tools)
    return entry


def effective_domain(domain: dict | None, product_dir: Path | None) -> dict:
    """The domain pack with the product's declarations merged on top (product wins). Never widens a curated entry
    silently: the merge is explicit per tool, and the file is the operator's own."""
    base = dict(domain or {}); tools = dict(base.get("tools") or {})
    if product_dir is not None:
        tools.update(load_pack(product_dir).get("tools") or {})
    base["tools"] = tools
    return base


# ---- product policy: the defaults an operator chooses for what the catalog cannot resolve ----------------------
# unknown_tools:
#   deny       (default) an unknown tool is DENIED, disposition `unresolved`, until declared — fail-closed.
#   heuristic  the catalog's NAME heuristics may grant tier-0/1 families (reads, plain writes) for unknown tools;
#              tier-2 (money, mail, delete, exec) is ALWAYS withheld by a heuristic — a standing decision.
POLICY_DEFAULTS = {"unknown_tools": "deny"}
POLICY_CHOICES = {"unknown_tools": ("deny", "heuristic")}


def get_policy(product_dir: Path) -> dict:
    pol = dict(POLICY_DEFAULTS); pol.update(load_pack(product_dir).get("policy") or {})
    return pol


def set_policy(product_dir: Path, key: str, value: str) -> dict:
    if key not in POLICY_CHOICES:
        raise ValueError(f"unknown policy {key!r}; known: {sorted(POLICY_CHOICES)}")
    if value not in POLICY_CHOICES[key]:
        raise ValueError(f"{key} must be one of {POLICY_CHOICES[key]} (got {value!r})")
    from attenu_derive import config as _cfg
    pol = get_policy(product_dir); pol[key] = value
    _cfg.commit(product_dir, policy=pol)
    return get_policy(product_dir)
