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
    assert not auth.covers_scope("fs.read")            # rubric v1: explicit "delegate the reading" -> no reads
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
