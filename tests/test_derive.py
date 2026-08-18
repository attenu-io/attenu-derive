"""T2: derive/ v0 — L1 templates + L2 catalog + L4 fail-closed. No model, no network."""
import time
from delegation_guard import Authority, Guard, RowLimit, EgressRank
from attenu_derive.derive.propose import Deriver, DelegationEvent

PARENT = Authority({"fs.*", "agent.delegate.*", "agent.message", "web.fetch"}, [RowLimit(100_000), EgressRank("any")], ttl=3600)


def _ev(**kw):
    base = dict(task="", role="child", agent="x", tools_available=[], parent_authority=PARENT, declared_subagents=[])
    base.update(kw); return DelegationEvent(**base)


def test_l1_researcher_template_from_task_and_role():
    d = Deriver()
    ev = _ev(task="Explore the repository to understand its architecture and report findings", role="child", agent="researcher",
             tools_available=["ls", "glob", "grep", "read_file", "write_file", "edit_file"])
    auth, rec = d.propose(ev)
    assert rec.layer == "L1" and rec.template == "explorer"
    assert auth.scopes == {"fs.read"}
    assert auth.ceiling("max_rows").max_rows == 1000 and auth.ceiling("egress").level == "none"
    assert not auth.covers_scope("fs.write")


def test_l1_orchestrator_template_grants_delegate_and_write_with_call_limit():
    d = Deriver()
    ev = _ev(task="Produce a short architecture overview and save it to REPORT.md. Delegate the reading to the researcher.",
             role="root", agent="orchestrator", tools_available=["ls", "glob", "grep", "read_file", "write_file", "task"],
             declared_subagents=["researcher"])
    auth, rec = d.propose(ev)
    assert rec.layer == "L1" and rec.template == "delegating-writer"
    assert auth.covers_scope("agent.delegate.researcher") and auth.covers_scope("fs.write")
    assert not auth.covers_scope("agent.delegate.exfiltrator")
    # rubric v1 said "explicit 'delegate the reading' -> no reads"; rubric v1.2 (T13) reversed it: child ⊆ parent, so the
    # orchestrator HOLDS fs.read for delegation (marked), and its OWN reads are the over-exploration signal, not a scope
    assert auth.covers_scope("fs.read") and rec.spec.get("held_for_delegation") == ["fs.read"]
    cl = auth.ceiling("max_calls[fs.write]"); assert cl is not None and cl.max_calls == 5 and cl.applies_to == "fs.write"
    assert rec.spec["ceilings"][0].get("applies_to") == "fs.write" or any(c.get("applies_to") == "fs.write" for c in rec.spec["ceilings"])


def test_l2_catalog_path_when_no_template_matches():
    d = Deriver()
    ev = _ev(task="Fetch the latest release notes from the project website and summarise them", role="child", agent="webbot",
             tools_available=["WebFetch", "Read"])
    auth, rec = d.propose(ev)
    assert rec.layer == "L2"
    assert auth.scopes == {"web.fetch", "fs.read"}
    assert auth.ceiling("egress") is not None            # web.fetch consumes egress -> ceiling present, met with parent


def test_l4_fail_closed_on_unknown_tools_and_never_grants_unknown_scope():
    d = Deriver()
    ev = _ev(task="do the thing", role="child", agent="mystery", tools_available=["frobnicate", "zap"])
    auth, rec = d.propose(ev)
    assert rec.layer == "L4" and auth.scopes == set()
    assert not any(s.startswith("unknown.") for s in auth.scopes)
    assert "frobnicate" in rec.evidence["unknown_tools"]


def test_proposals_never_escalate_beyond_the_parent():
    d = Deriver()
    narrow = Authority({"fs.read"}, [RowLimit(50)], ttl=60)
    ev = _ev(task="Produce a report and save it to REPORT.md; delegate reading to the researcher", role="root", agent="o",
             tools_available=["read_file", "write_file", "task"], declared_subagents=["researcher"], parent_authority=narrow)
    auth, rec = d.propose(ev)
    assert auth.is_narrower_than(narrow)              # meet: cannot grant fs.write the parent lacks
    assert not auth.covers_scope("fs.write") and not auth.covers_scope("agent.delegate.researcher")


def test_latency_budget_l1_l2():
    d = Deriver()
    ev1 = _ev(task="Explore the repo and report", role="child", agent="r", tools_available=["read_file", "grep"])
    ev2 = _ev(task="Fetch release notes", role="child", agent="w", tools_available=["WebFetch"])
    for ev, cap in ((ev1, 0.005), (ev2, 0.05)):
        ts = []
        for _ in range(200):
            t0 = time.perf_counter(); d.propose(ev); ts.append(time.perf_counter() - t0)
        ts.sort(); assert ts[int(0.95 * len(ts))] < cap


def test_l2_heuristic_grants_low_risk_only_and_lowers_confidence():
    d = Deriver()
    wide = Authority({"data.*", "payments.*", "web.*"}, [], ttl=None)     # parent holds both families
    ev = _ev(task="Check the weather and place the order", role="child", agent="bot",
             tools_available=["get_weather_forecast", "place_order"], parent_authority=wide)   # place_order: deliberately uncurated (catalog v1)
    auth, rec = d.propose(ev)
    assert rec.layer == "L2" and rec.confidence < 0.6
    assert auth.covers_scope("data.read")                        # tier-0 heuristic family: granted (low confidence)
    assert not auth.covers_scope("payments.transfer")            # tier-2 via heuristic: withheld (fail closed)
    assert ("place_order", "payments.transfer") in rec.evidence["withheld_heuristic"]


def test_t7_read_verb_tool_grantable_while_write_verb_tier2_withheld():
    """T7 DoD: `get_order_details` -> data.read GRANTED; `place_order` -> payments.transfer still WITHHELD (heuristic)."""
    d = Deriver()
    wide = Authority({"data.*", "payments.*"}, [], ttl=None)
    ev = _ev(task="Look up the order and then place a new one", role="child", agent="bot",
             tools_available=["get_order_details", "place_order"], parent_authority=wide)
    auth, rec = d.propose(ev)
    assert rec.layer == "L2"
    assert auth.covers_scope("data.read") and not auth.covers_scope("payments.transfer")
    assert ("get_order_details", "data.read") in rec.evidence["heuristic_grants"]
    assert ("place_order", "payments.transfer") in rec.evidence["withheld_heuristic"]


def test_curated_tier2_entry_is_granted_bounded_by_parent():
    """A CURATED tier-2 tool (book_flight, catalog v1) is granted at L2 — the parent's meet still bounds it."""
    d = Deriver()
    ev = _ev(task="Book the flight", role="child", agent="bot", tools_available=["book_flight"],
             parent_authority=Authority({"payments.transfer"}, [], ttl=None))
    auth, rec = d.propose(ev)
    assert rec.layer == "L2" and auth.covers_scope("payments.transfer")
    ev2 = _ev(task="Book the flight", role="child", agent="bot", tools_available=["book_flight"],
              parent_authority=Authority({"data.read"}, [], ttl=None))              # parent lacks it -> meet removes it
    auth2, rec2 = d.propose(ev2)
    assert not auth2.covers_scope("payments.transfer")


# ---- T12 (PM W2→P2 slice, 2026-08-18): L1 templates COMPUTE their scopes from tools_available -----------------
WIDE = Authority({"fs.*", "data.*", "agent.delegate.*", "agent.message", "web.*", "code.exec", "compute.pure"}, [RowLimit(1_000_000), EgressRank("any")], ttl=None)


def test_explorer_grants_the_read_families_its_tools_resolve_to_never_wider():
    """Site 1 (`templates.py` explorer): `{"fs.read"}` was hard-coded. An ADK explorer holding
    list_files/read_file/search_text (search_text -> data.read) was benign-denied on every search
    (G1 train benign-deny 0.0204). Scopes = read families of tools_available, intersected with the
    template's allowed families — data.read only when a tool resolves to it."""
    d = Deriver()
    ev = _ev(task="Explore the repository and report findings with file paths", role="child", agent="researcher",
             tools_available=["list_files", "read_file", "search_text"], parent_authority=WIDE)
    auth, rec = d.propose(ev)
    assert rec.layer == "L1" and rec.template == "explorer"
    assert auth.covers_scope("fs.read") and auth.covers_scope("data.read")
    assert auth.permits("data.read", {"rows": 10}) and auth.permits("fs.read", {"rows": 10})
    assert not auth.covers_scope("fs.write") and not auth.covers_scope("data.write")
    # never wider: a Claude-SDK explorer (Read/Grep/Glob) still gets fs.read ONLY
    ev2 = _ev(task="Explore the repository and report findings", role="child", agent="researcher",
              tools_available=["Read", "Grep", "Glob", "Write", "Agent", "SendMessage"], parent_authority=WIDE)
    auth2, rec2 = d.propose(ev2)
    assert rec2.template == "explorer" and auth2.scopes == {"fs.read"}


def test_explorer_with_no_read_tool_gets_neither_read_scope_and_no_write():
    d = Deriver()
    ev = _ev(task="Explore the repository and report findings", role="child", agent="researcher",
             tools_available=["Write"], parent_authority=WIDE)
    auth, rec = d.propose(ev)
    assert not auth.covers_scope("fs.read") and not auth.covers_scope("data.read") and not auth.covers_scope("fs.write")


def test_delegating_writer_grants_the_write_family_its_tool_resolves_to():
    """Site 2: `{"fs.write"}` was hard-coded. An orchestrator whose deliverable tool resolves to
    data.write (e.g. `save_document`) must get data.write, not fs.write; no write tool -> no write scope."""
    d = Deriver()
    base = dict(task="Produce a short architecture overview and save it to REPORT.md. Delegate the reading to the researcher.",
                role="root", agent="orchestrator", declared_subagents=["researcher"], parent_authority=WIDE)
    a1, r1 = d.propose(_ev(**base, tools_available=["task", "write_file"]))
    assert r1.template == "delegating-writer" and a1.covers_scope("fs.write") and not a1.covers_scope("data.write")
    a2, r2 = d.propose(_ev(**base, tools_available=["task", "save_document"]))
    assert r2.template == "delegating-writer" and a2.covers_scope("data.write") and not a2.covers_scope("fs.write")
    assert a2.covers_scope("agent.delegate.researcher")
    a3, r3 = d.propose(_ev(**base, tools_available=["task"]))
    assert r3.template == "delegating-writer" and not a3.covers_scope("fs.write") and not a3.covers_scope("data.write")
    assert a3.covers_scope("agent.delegate.researcher")


def test_l1_never_grants_tier2_even_when_a_tool_resolves_there():
    d = Deriver()
    ev = _ev(task="Explore the repository and report findings", role="child", agent="researcher",
             tools_available=["read_file", "delete_message", "Bash"], parent_authority=WIDE)
    auth, rec = d.propose(ev)
    assert rec.template == "explorer" and auth.scopes == {"fs.read"}


# ---- T13 fix (rubric ruling 2 reversed, 2026-08-18): a parent HOLDS what it delegates -------------------------
def test_delegating_writer_holds_the_read_families_of_its_tools_for_delegation():
    """Monotonic attenuation: child ⊆ parent, so an orchestrator that delegates exploration must HOLD the read
    families its sub-agents need. Computed from its own tools_available under T12 semantics (never wider, tier<=1),
    and MARKED as held_for_delegation on the record — the only trace of held-to-pass-down vs held-to-use."""
    d = Deriver()
    base = dict(task="Produce REPORT.md. Delegate all reading to the researcher subagent; write only the final file yourself.",
                role="root", agent="orchestrator", declared_subagents=["researcher"], parent_authority=WIDE)
    auth, rec = d.propose(_ev(**base, tools_available=["Read", "Grep", "Glob", "Write", "Agent", "SendMessage"]))
    assert rec.template == "delegating-writer"
    assert auth.covers_scope("fs.read") and auth.covers_scope("fs.write") and auth.covers_scope("agent.delegate.researcher")
    assert rec.spec.get("held_for_delegation") == ["fs.read"]                     # marked on the record / ledger
    assert "fs.read" in rec.evidence.get("held_for_delegation", [])
    # the child's meet now works: an explorer derived against THIS authority keeps its reads
    child, crec = d.propose(_ev(task="Explore the repository and report findings", role="child", agent="researcher",
                                tools_available=["Read", "Grep", "Glob"], parent_authority=auth))
    assert child.covers_scope("fs.read") and child.permits("fs.read", {"rows": 10})
    # never wider: no read tools -> nothing held; a data.read tool -> data.read held, not fs.read
    a2, r2 = d.propose(_ev(**base, tools_available=["task", "write_file"]))
    assert not a2.covers_scope("fs.read") and r2.spec.get("held_for_delegation", []) == []
    a3, r3 = d.propose(_ev(**base, tools_available=["task", "write_file", "get_stock_info"]))
    assert a3.covers_scope("data.read") and not a3.covers_scope("fs.read") and r3.spec.get("held_for_delegation") == ["data.read"]


def test_held_for_delegation_comes_from_the_declared_subtree_not_only_own_tools():
    """Role-specific suites (ADK, CrewAI): the orchestrator holds only write_file + AgentTools, its explorers hold the
    read tools. 'Minimal closure over the delegation subtree' means the parent holds the read families its DECLARED
    sub-agents' tools resolve to — still never wider than the subtree's tools, never tier 2."""
    d = Deriver()
    ev = _ev(task="Produce REPORT.md; delegate each section to the specialist sub-agent tools; write REPORT.md yourself with write_file.",
             role="root", agent="orchestrator", tools_available=["write_file", "researcher", "security_reviewer"],
             declared_subagents=["researcher", "security_reviewer"],
             subagent_tools={"researcher": ["list_files", "read_file", "search_files"], "security_reviewer": ["list_files", "read_file", "search_files"]},
             parent_authority=WIDE)
    auth, rec = d.propose(ev)
    assert rec.template == "delegating-writer"
    assert auth.covers_scope("fs.read") and rec.spec.get("held_for_delegation") == ["fs.read"]
    assert not auth.covers_scope("data.read") and not auth.covers_scope("code.exec")
    child, _ = d.propose(_ev(task="Explore the repository and report findings", role="child", agent="researcher",
                             tools_available=["list_files", "read_file", "search_files"], parent_authority=auth))
    assert child.permits("fs.read", {"rows": 10})
    # a sub-agent whose tools include a tier-2 tool: never held at L1
    ev2 = _ev(task="Produce REPORT.md; delegate to the ops sub-agent; write REPORT.md yourself with write_file.", role="root", agent="orchestrator",
              tools_available=["write_file"], declared_subagents=["ops"], subagent_tools={"ops": ["read_file", "Bash", "delete_message"]}, parent_authority=WIDE)
    a2, r2 = d.propose(ev2)
    assert a2.covers_scope("fs.read") and not a2.covers_scope("code.exec") and not a2.covers_scope("data.delete")
