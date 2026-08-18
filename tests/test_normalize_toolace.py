"""T11: ToolACE normalizer — system-prompt function list, `[Name(a="x")]` call syntax (names with spaces), redaction."""
from attenu_derive.corpus.normalize_toolace import LICENCE, parse_calls, parse_functions, row_from_dialog

_SYS = ('You are an expert. Here is a list of functions in JSON format that you can invoke: [{"name": "Market Trends API", "description": "d", '
        '"parameters": {"type": "dict", "properties": {"trend_type": {"type": "string"}}}}, {"name": "newAddress", "description": "x", "parameters": {}}]. Should you decide...')


def test_parse_functions_and_calls():
    assert [f["name"] for f in parse_functions(_SYS)] == ["Market Trends API", "newAddress"]
    assert parse_calls('[Market Trends API(trend_type="MARKET_INDEXES", country="us"), newAddress()]') == \
        [("Market Trends API", {"trend_type": "MARKET_INDEXES", "country": "us"}), ("newAddress", {})]
    assert parse_calls('[calc(values=[1, 2, 3], opts={"a": 1})]') == [("calc", {"values": [1, 2, 3], "opts": {"a": 1}})]
    assert parse_calls("Here are the trends: ...") is None


def test_row_schema_and_redaction():
    e = {"system": _SYS, "conversations": [
        {"from": "user", "value": "Top US market trends please, mail to bob@x.io"},
        {"from": "assistant", "value": '[Market Trends API(trend_type="MARKET_INDEXES", country="us")]'},
        {"from": "tool", "value": '[{"name": "Market Trends API", "results": {}}]'},
        {"from": "assistant", "value": "Here they are."}]}
    row, mirror = row_from_dialog(e, 7)
    assert row["licence"] == LICENCE and row["label_provenance"] == "dataset" and row["event_id"] == "toolace:7"
    assert row["tools_available"] == ["Market Trends API", "newAddress"] and [c["tool"] for c in row["child_calls"]] == ["Market Trends API"]
    assert "MARKET_INDEXES" not in str(row) and "bob@x.io" not in str(row) and mirror["task"].startswith("Top US")
    assert row_from_dialog({"system": _SYS, "conversations": [{"from": "user", "value": "hi"}, {"from": "assistant", "value": "hello"}]}, 8) is None
