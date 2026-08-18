"""RED→GREEN: the recorder's feature extractor must be structurally incapable of
carrying content (ADR-05). Every raw string/number value in a tool call's args
must be replaced by shape/type/bucket/salted-hash — never echoed."""
import json
import pytest
from hypothesis import given, strategies as st

from attenu_derive.sample.features import extract_features, quantity_bucket


SECRETS = ["s3://attacker-bucket/dump.csv", "alice@example.com", "sk-ant-api03-XYZ", "/secrets/keys.txt"]


def test_no_raw_string_value_survives():
    args = {"destination": SECRETS[0], "to": SECRETS[1], "nested": {"key": SECRETS[2], "path": SECRETS[3]},
            "rows": 4200, "flags": ["a", SECRETS[1]]}
    feats = extract_features(args, salt="run-salt")
    blob = json.dumps(feats)
    for s in SECRETS:
        assert s not in blob, s
    assert "4200" not in blob                       # numbers are bucketed, not echoed
    # what IS there: shape + types + buckets + hashes
    assert feats["arg_shape"]["destination"] == "str"
    assert feats["arg_shape"]["rows"] == "int"
    assert feats["arg_shape"]["nested"] == {"key": "str", "path": "str"}
    assert feats["arg_shape"]["flags"] == ["str"]
    assert feats["quantities"]["rows"] == quantity_bucket(4200)
    assert set(feats["arg_hashes"]) >= {"destination", "to", "nested.key", "nested.path"}
    assert all(len(h) == 16 for h in feats["arg_hashes"].values())      # truncated salted sha256


def test_hashes_are_salted_and_stable_within_a_run():
    a = extract_features({"to": "alice@example.com"}, salt="s1")
    b = extract_features({"to": "alice@example.com"}, salt="s1")
    c = extract_features({"to": "alice@example.com"}, salt="s2")
    assert a["arg_hashes"]["to"] == b["arg_hashes"]["to"] != c["arg_hashes"]["to"]


def test_quantity_buckets_are_coarse_and_monotonic():
    assert quantity_bucket(0) == "0"
    assert quantity_bucket(1) == "1"
    assert quantity_bucket(7) == "2-10"
    assert quantity_bucket(4200) == "1k-10k"
    assert quantity_bucket(10**9) == "1M+"
    assert quantity_bucket(3.5) == "2-10"
    order = [quantity_bucket(n) for n in (0, 1, 5, 50, 500, 5000, 50000, 500000, 5000000)]
    assert order == ["0", "1", "2-10", "11-100", "101-1k", "1k-10k", "10k-100k", "100k-1M", "1M+"]


def test_string_length_is_bucketed_not_measured_exactly():
    f = extract_features({"query": "x" * 1234}, salt="s")
    assert f["str_len_buckets"]["query"] == "1k-10k"


_LABELS = {"0", "1", "2-10", "11-100", "101-1k", "1k-10k", "10k-100k", "100k-1M", "1M+", "neg"}
_TYPES = {"bool", "int", "float", "str", "null", "object", "array", "other"}
_content = (st.text(min_size=6)
            .filter(lambda s: any(ch in s for ch in " @/:.\n") or not s.isidentifier())
            .filter(lambda s: s not in _LABELS | _TYPES))


@given(st.dictionaries(st.text(min_size=1, max_size=40), st.one_of(_content, st.integers(), st.floats(allow_nan=False)), max_size=6))
def test_property_no_string_value_ever_appears_in_output(args):
    # Values are CONTENT-like strings (not identifiers), numbers, floats; keys are arbitrary
    # (identifier-like keys are kept as schema, anything else is hashed).
    feats = extract_features(args, salt="p")
    blob = json.dumps(feats, ensure_ascii=False)
    for v in args.values():
        if isinstance(v, str):
            assert v not in blob
    # numbers: never echoed as JSON values — only buckets (short digit runs can appear inside hashes)
    for v in args.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 100:
            assert f": {v}" not in blob and f'"{v}"' not in blob


def test_content_like_keys_are_hashed_identifier_keys_kept():
    f = extract_features({"alice@example.com": 1, "/secrets/x": 2, "rows": 3, "000000": "000000"}, salt="s")
    keys = set(f["arg_shape"])
    assert "rows" in keys
    assert not ({"alice@example.com", "/secrets/x", "000000"} & keys)
    assert "000000" not in json.dumps(f)
