"""
Redacting feature extractor for the observe-mode recorder (ADR-05).

The recorder must be structurally incapable of carrying content: a tool call's
arguments are reduced to SHAPE (keys + JSON types), coarse QUANTITY buckets for
numbers, coarse LENGTH buckets for strings, and per-value salted, truncated
hashes (so equal values within one run are recognisable — "the same file was
read twice" — without ever storing the value). Nothing else. No raw string,
number or payload ever appears in the output; `tests/test_features.py` pins it
with a property test.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

__all__ = ["extract_features", "quantity_bucket", "length_bucket", "type_name"]

_BUCKETS = [(0, "0"), (1, "1"), (10, "2-10"), (100, "11-100"), (1_000, "101-1k"),
            (10_000, "1k-10k"), (100_000, "10k-100k"), (1_000_000, "100k-1M")]


def quantity_bucket(n: float) -> str:
    """Coarse, monotonic bucket for a number. Negative → 'neg'."""
    if n < 0:
        return "neg"
    for upper, label in _BUCKETS:
        if n <= upper:
            return label
    return "1M+"


def length_bucket(s: str) -> str:
    return quantity_bucket(len(s))


def type_name(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if v is None:
        return "null"
    if isinstance(v, Mapping):
        return "object"
    if isinstance(v, (list, tuple, set)):
        return "array"
    return "other"


_IDENT = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,63}$")


def _key(k: Any, salt: str) -> str:
    """Keys are kept ONLY when they look like schema (identifier-like); a key that
    looks like content (email, path, number, sentence, > 64 chars) is hashed —
    dynamic dict keys carry data as often as values do."""
    k = str(k)
    return k if _IDENT.match(k) else "#" + _hash(k, salt)


def _hash(value: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}\x1f{value}".encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _shape(v: Any, salt: str = ""):
    """Recursive JSON-type skeleton. Lists become [type-of-first] (or [] when empty)."""
    if isinstance(v, Mapping):
        return {_key(k, salt): _shape(x, salt) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        items = list(v)
        return [_shape(items[0], salt)] if items else []
    return type_name(v)


def extract_features(args: Mapping[str, Any] | None, *, salt: str, max_depth: int = 4) -> dict:
    """Reduce tool-call `args` to derived features only.

    Returns {"arg_shape", "quantities", "str_len_buckets", "arg_hashes"}.
    Keys of nested objects are joined with '.'; list elements are summarised
    (count bucket) not enumerated. Keys themselves are kept (they are schema,
    not content) — but if a KEY looks like content (> 64 chars) it is hashed too.
    """
    args = dict(args or {})
    quantities: dict[str, str] = {}
    str_lens: dict[str, str] = {}
    hashes: dict[str, str] = {}

    def walk(prefix: str, v: Any, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            quantities[prefix] = quantity_bucket(v)
        elif isinstance(v, str):
            str_lens[prefix] = length_bucket(v)
            hashes[prefix] = _hash(v, salt)
        elif isinstance(v, Mapping):
            for k, x in v.items():
                key = _key(k, salt)
                walk(f"{prefix}.{key}" if prefix else key, x, depth + 1)
        elif isinstance(v, (list, tuple, set)):
            items = list(v)
            quantities[f"{prefix}[]"] = quantity_bucket(len(items))
            for x in items[:8]:                          # bounded: hashes of the first few
                if isinstance(x, str):
                    hashes.setdefault(f"{prefix}[]", _hash(x, salt))
                elif isinstance(x, Mapping):
                    walk(f"{prefix}[]", x, depth + 1)

    for k, v in args.items():
        walk(_key(k, salt), v, 1)

    return {
        "arg_shape": {_key(k, salt): _shape(v, salt) for k, v in args.items()},
        "quantities": quantities,
        "str_len_buckets": str_lens,
        "arg_hashes": hashes,
    }
