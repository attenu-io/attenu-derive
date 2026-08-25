"""A3: close the framework-concentration bound — CrewAI + LangGraph on a customer-domain workload. The LIVE enforce
runs (held payment denied, offline-verifiable) are in the eval card; these CI-safe tests pin the deriver on the new
travel-booking domain (T16 over-reach blocked / T17 no widening) and the a3 runners' importability + pack."""
from attenu_derive.catalog.coverage import load_domain
from attenu_derive.derive.propose import Deriver, DelegationEvent
from attenu_guard import Authority


def _ev(task, tools, parent):
    return DelegationEvent(task=task, role="child", agent="booking_agent", tools_available=tools,
                           parent_authority=parent, declared_subagents=[])


def test_travel_booking_pack_holds_payment_until_granted():
    ov = load_domain("travel-booking")
    wide = Authority({"data.read", "payments.transfer"}, [], ttl=None)
    ev = _ev("Search flights and book flight AB123", ["search_flights", "get_weather", "book_flight"], wide)
    held, _ = Deriver(domain=ov).propose(ev)
    assert held.covers_scope("data.read") and not held.covers_scope("payments.transfer")   # book_flight held
    granted, _ = Deriver(domain=ov, operator_grants={"payments.transfer"}).propose(ev)
    assert granted.covers_scope("payments.transfer")


def test_t17_injection_no_widening_on_travel_domain():
    from attenu_derive.eval.injection import inject, POISONS
    ov = load_domain("travel-booking"); d = Deriver(domain=ov)
    wide = Authority({"data.read", "payments.transfer", "mail.send", "fs.write"}, [], ttl=None)
    clean, _ = d.propose(_ev("Search flights to Paris and the weather", ["search_flights", "get_weather"], wide))
    for variant in inject("Search flights to Paris and the weather", POISONS):
        g, _ = d.propose(_ev(variant, ["search_flights", "get_weather"], wide))
        assert set(g.scopes) <= set(clean.scopes), (variant[:50], sorted(set(g.scopes) - set(clean.scopes)))   # never wider


def test_t16_overreach_blocked_on_travel_booker():
    """A booker derived with only reads (payment held) has any payment/mail/write over-reach denied by the shim."""
    from attenu_guard import Guard
    ov = load_domain("travel-booking")
    granted, _ = Deriver(domain=ov).propose(_ev("Search flights and weather", ["search_flights", "get_weather", "book_flight"],
                                                Authority({"data.read"}, [], ttl=None)))
    g = Guard.issue("booking_agent", granted, task="t")
    assert g.check("data.read", tool="search_flights").allowed                       # granted read passes
    for foreign in ("payments.transfer", "mail.send", "fs.write", "data.delete"):
        assert not g.check(foreign, tool="x").allowed, foreign                       # every over-reach blocked


def test_a3_runners_import():
    import attenu_derive.sample.a3_crewai_enforce as c
    import attenu_derive.sample.a3_langgraph_enforce as l
    assert hasattr(c, "run") and hasattr(l, "run") and "book_flight" in c.TRAVEL_TOOLS
