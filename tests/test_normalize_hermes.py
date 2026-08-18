"""T11 (PM W2 slice, 2026-08-18): second public dataset normalizer — schema, licence, provenance, tool-call parsing."""
from attenu_derive.corpus.normalize_hermes import LICENCE, parse_tool_calls, parse_tools, row_from_conversation

_TOOLS = '[{"type": "function", "function": {"name": "get_stock_price", "description": "Get price", "parameters": {"type": "object", "properties": {"company": {"type": "string"}}}}}, ' \
         '{"type": "function", "function": {"name": "send_email", "description": "Send", "parameters": {"type": "object", "properties": {"to": {"type": "string"}}}}}]'
_ENTRY = {"id": "abc", "category": "Stocks and Orders", "subcategory": "Get Stock Price", "tools": _TOOLS, "conversations": [
    {"from": "system", "value": "You are a function calling AI model. <tools>...</tools>"},
    {"from": "human", "value": "What is Apple's stock price? Email it to bob@example.com"},
    {"from": "gpt", "value": '<tool_call>\n{"name": "get_stock_price", "arguments": {"company": "Apple"}}\n</tool_call>'},
    {"from": "tool", "value": '{"price": 150}'},
    {"from": "gpt", "value": '<tool_call> {"name": "send_email", "arguments": "{\\"to\\": \\"bob@example.com\\"}"} </tool_call> done'},
]}


def test_parsers():
    assert [t["name"] for t in parse_tools(_TOOLS)] == ["get_stock_price", "send_email"]
    calls = parse_tool_calls('<tool_call> {"name": "a", "arguments": {"x": 1}} </tool_call> <tool_call> {bad json} </tool_call>')
    assert calls == [("a", {"x": 1})]


def test_row_schema_licence_and_redaction():
    row, mirror = row_from_conversation(_ENTRY, file="glaive-function-calling-5k.json")
    assert row["label_provenance"] == "dataset" and row["licence"] == LICENCE and row["source"] == "dataset"
    assert row["tools_available"] == ["get_stock_price", "send_email"]
    assert [c["tool"] for c in row["child_calls"]] == ["get_stock_price", "send_email"]     # stringified arguments are parsed too
    assert row["observed_envelope"]["tools"] == ["get_stock_price", "send_email"]
    assert "task" not in row and mirror["task"].startswith("What is Apple's")              # text only in the mirror
    blob = str(row)
    assert "Apple" not in blob and "bob@example.com" not in blob                              # ADR-05: no values in the corpus row
    assert row["task_features"]["has_email"] is True and row["turns"] == 1


def test_conversation_without_calls_is_skipped():
    e = dict(_ENTRY, conversations=[{"from": "human", "value": "hi"}, {"from": "gpt", "value": "hello"}])
    assert row_from_conversation(e, file="f.json") is None


def test_python_literal_pseudo_json_calls_are_recovered():
    calls = parse_tool_calls("<tool_call>\n{'arguments': {'queries': ['How do you define x?', \"why\"]}, 'name': 'ExpertQAExtractor'}\n</tool_call>")
    assert calls and calls[0][0] == "ExpertQAExtractor" and "queries" in calls[0][1]


def test_literal_backslash_n_wrapping_is_tolerated():
    calls = parse_tool_calls('<tool_call>\\n{"arguments": {"q": ["a"]}, "name": "X"}\\n</tool_call>')     # literal backslash-n, as in ~730 rows
    assert calls == [("X", {"q": ["a"]})]


def test_name_nested_inside_arguments_is_recovered():
    calls = parse_tool_calls('<tool_call>\\n{"arguments": {"queries": [\'How do you define "x"?\'], "name": "ExpertQAExtractor"}}\\n</tool_call>')
    assert calls == [("ExpertQAExtractor", {"queries": ['How do you define "x"?']})]
