"""T32 (G4 ≤1h onboarding, G5 day-0 story): a brand-new held-out app (travel-concierge) gets SAFE derivation from the
shipped kit alone (money tools withheld, unknowns fail-closed), and a curated domain pack takes it to 100% curated
with the money tools held pending an operator grant."""
from attenu_derive.catalog.coverage import load_catalog, load_domain, coverage
from attenu_derive.derive.propose import Deriver, DelegationEvent
from delegation_guard import Authority

TOOLS = ["create_reservation", "process_payment", "payment_choice", "event_booking_check", "flight_status_check",
         "weather_impact_check", "memorize", "google_search", "google_search_grounding"]


def test_day0_is_safe_money_withheld_unknown_failclosed():
    cat = load_catalog(); rows = [{"child_calls": [{"tool": t} for t in TOOLS]}]
    b = coverage(rows, cat)
    assert b["calls_withheld_share"] > 0                                   # the payment tools are withheld day-0
    assert b["calls_unresolved_share"] > 0                                 # memorize fails closed
    # every payment tool is withheld, never granted, from the base kit
    from attenu_derive.catalog.coverage import _classify
    for money in ("create_reservation", "process_payment"):
        assert _classify(cat, money) == "withheld"


def test_travel_pack_reaches_100_curated_with_money_held():
    cat = load_catalog(); ov = load_domain("travel-planning")
    o = coverage([{"child_calls": [{"tool": t} for t in TOOLS]}], cat, overlay=ov)
    assert o["calls_curated_share"] == 1.0 and o["calls_unresolved_share"] == 0.0
    assert set(o["requires_grant_tools"]) == {"create_reservation", "process_payment"}


def test_deriver_holds_process_payment_until_operator_grants_it():
    ov = load_domain("travel-planning")
    ev = DelegationEvent(task="Book the hotel and pay for it", role="root", agent="booking_agent",
                         tools_available=["payment_choice", "create_reservation", "process_payment", "memorize"],
                         parent_authority=Authority({"payments.transfer", "data.read", "state.write"}, [], ttl=None), declared_subagents=[])
    held, _ = Deriver(domain=ov).propose(ev)
    assert held.covers_scope("data.read") and not held.covers_scope("payments.transfer")   # money held
    granted, _ = Deriver(domain=ov, operator_grants={"payments.transfer"}).propose(ev)
    assert granted.covers_scope("payments.transfer")                                        # one grant enables it
