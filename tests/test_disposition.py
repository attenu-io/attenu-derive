"""Slice 1 / Plan A, Task 9 — one classifier answers "why would this tool's scope be absent?" for every runner:
held_pending_grant (curated tier-2 without an operator grant, or a scope the operator held back), withheld_tier2
(resolvable only to a tier-2 heuristic the deriver never grants), unresolved (no entry), or None (grantable — a
later deny is the shim's own out_of_authority). Mirrors catalog.coverage._classify so console and coverage agree."""
import pytest

from attenu_derive.catalog.coverage import load_catalog, load_domain
from attenu_derive.derive.disposition import tool_dispositions, unknown_tool_scope


def test_dispositions_on_the_retail_support_pack():
    cat, dom = load_catalog(), load_domain("retail-support")
    d = tool_dispositions(cat, dom, ["send_care_instructions", "update_salesforce_crm", "totally_unknown_tool"], operator_grants=set())
    assert d["send_care_instructions"] == ("mail.send", "held_pending_grant")          # curated tier-2, no grant
    assert d["update_salesforce_crm"] == ("crm.write", None)                            # curated, grantable
    assert d["totally_unknown_tool"] == ("unknown.totally_unknown_tool", "unresolved")
    d2 = tool_dispositions(cat, dom, ["send_care_instructions"], operator_grants={"mail.send"})
    assert d2["send_care_instructions"] == ("mail.send", None)                          # granted -> nothing to explain
    d3 = tool_dispositions(cat, dom, ["update_salesforce_crm"], operator_grants=set(), held={"crm.write"})
    assert d3["update_salesforce_crm"] == ("crm.write", "held_pending_grant")           # operator did not enable it


def test_heuristic_tier2_is_withheld_only_when_heuristics_are_on():
    cat = load_catalog()
    off = tool_dispositions(cat, None, ["place_order"], operator_grants=set(), heuristics=False)
    on = tool_dispositions(cat, None, ["place_order"], operator_grants=set(), heuristics=True)
    assert off["place_order"] == ("unknown.place_order", "unresolved")                  # enforce = curated only
    assert on["place_order"] == ("payments.transfer", "withheld_tier2")                 # resolvable only to a tier-2 heuristic


def test_every_disposition_value_is_a_shim_constant():
    from attenu_guard import Disposition
    cat, dom = load_catalog(), load_domain("retail-support")
    d = tool_dispositions(cat, dom, ["send_care_instructions", "x_unknown"], operator_grants=set(), heuristics=True)
    assert all(v[1] is None or v[1] in Disposition.ALL for v in d.values())


# --- CamelCase / non-grammar tool names (attenu-guard 0.8.0 D1 scope grammar) -------------------------------

def test_unresolved_camelcase_tool_name_still_produces_a_valid_scope():
    """attenu-guard 0.8.0 (D1) tightened the scope grammar to lowercase dot-separated segments, enforced at
    `Authority` construction (`Authority(scopes=["unknown.SendEmail"])` raises `ValueError: invalid scope`).
    A CamelCase tool name (LangChain tool classes, MCP tool names) is routine in real agent frameworks, so
    the `unknown.<tool>` fallback must normalise it rather than pass it through raw."""
    from attenu_guard import Authority
    cat = load_catalog()
    d = tool_dispositions(cat, None, ["SendEmail"], operator_grants=set())
    scope, disposition = d["SendEmail"]
    assert disposition == "unresolved"
    assert scope != "unknown.SendEmail"          # the raw (invalid) form from before the fix
    assert scope.startswith("unknown.")
    Authority(scopes=[scope])                    # must not raise: this is the exact operation D1 tightened


def test_run_adk_enforce_tool_authorities_pattern_survives_unfiltered_collection():
    """Reproduces the shape of `sample/run_adk_enforce.py::tool_authorities()` (the live-enforce path, T30):
    `{name: ToolAuthority(scope, disposition=d) for name, (scope, d) in disp.items()}` — UNFILTERED, unlike
    `demo_local.py`'s `{sc for sc, d in disp.values() if d is None}`. `ToolAuthority` itself does not validate
    (it is a plain dataclass), so this collection is the highest-level real call site that would put an
    unresolved CamelCase tool's scope in front of a `Authority`-consuming caller. Simulate the case where all
    of an agent's declared tools (including two unresolved ones with framework-typical names) end up in one
    Authority, as a defence-in-depth caller of `tool_authorities()`'s output might."""
    from attenu_guard import Authority
    cat, dom = load_catalog(), load_domain("retail-support")
    tools = ["update_salesforce_crm", "SendEmail", "MCPFileSystem.ReadFile"]
    disp = tool_dispositions(cat, dom, tools, operator_grants=set())
    tool_authority_scopes = {scope for _name, (scope, _d) in disp.items()}   # the exact, unfiltered collection shape
    Authority(scopes=tool_authority_scopes)       # must not raise


@pytest.mark.parametrize("name, expected", [
    ("SendEmail", "unknown.sendemail"),
    ("", "unknown.t"),
    ("___", "unknown.t"),
    ("123abc", "unknown.t_123abc"),
    ("_", "unknown.t"),
    ("MCP__ToolName!!", "unknown.mcp__toolname"),
    ("job:info@x", "unknown.job_info_x"),
    ("send_email", "unknown.send_email"),          # already-valid names are unchanged (backward compatible)
    ("already_valid-ok", "unknown.already_valid-ok"),
    ("mcp.filesystem.read", "unknown.mcp_filesystem_read"),   # a literal '.' would otherwise fabricate segments
])
def test_unknown_tool_scope_edge_cases_against_the_real_shim_validator(name, expected):
    from attenu_guard import Authority
    scope = unknown_tool_scope(name)
    assert scope == expected
    Authority(scopes=[scope])                      # ground truth: the shim's own validator, not a regex read by eye
