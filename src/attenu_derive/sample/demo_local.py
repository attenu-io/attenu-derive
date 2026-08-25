"""
attenu demo — USD-0, no-model, no-API-key runs that write a REAL ledger into a product directory, so the console
can be demonstrated anywhere:

    attenu init --product "Travel Demo" --dir .
    attenu demo [--scenario basic|fanout] [--slow 1]
    attenu ui

`basic`  — the A3 travel-booking story: a planner delegates to a booking agent; reads pass, the payment is HELD
           pending an operator grant, an unknown tool is UNRESOLVED.
`fanout` — what a real multi-agent app looks like (reviewer: "I would expect more agents and tooling"): a trip planner
           delegating to four specialists, two of which delegate further (9 agents, 18 tools); reads allowed; payments,
           reservations and mail HELD; an unknown tool UNRESOLVED; a read-only support agent exceeding its declared
           role → OUT OF AUTHORITY; a reviews scout that keeps calling an unknown tool is REVOKED by the strike policy
           (3 same-scope denials) and every later call is denied as revoked.

Honest label: scripted runs — the enforcement, meet, ledger, strike policy, anchor and verifier are the real ones;
only the model's choices are scripted. Grants (`.attenu/grants.json`), declarations (`.attenu/pack.json`) and the
product policy all apply, so the console's decisions change the next run. `--slow` animates a watching UI.
"""
from __future__ import annotations

import time
from pathlib import Path

from attenu_guard import Authority, Guard, RowLimit, EgressRank, StrikePolicy, identity
from attenu_guard.sinks import SpoolSink

from attenu_derive.catalog.coverage import load_catalog, load_domain
from attenu_derive.derive.disposition import tool_dispositions
from attenu_derive.evidence_out import write_evidence
from attenu_derive.product import effective_domain, get_policy, load_grants, note_run

PLANNER = "travel_planner"
BOOKER = "booking_agent"
CHILD_TOOLS = ["search_flights", "get_weather", "book_flight", "lookup_loyalty_tier"]

# ---- the fan-out scenario's world ---------------------------------------------------------------------------
# tools the travel-planning pack does not already curate (merged over it; a product's own declarations win over both)
FANOUT_TOOLS = {
    "fetch_page": {"scope": "web.fetch", "tier": 1}, "summarize_text": {"scope": "compute.pure", "tier": 0},
    "search_flights": {"scope": "data.read", "tier": 0}, "book_flight": {"scope": "payments.transfer", "tier": 2, "requires_grant": True},
    "search_hotels": {"scope": "data.read", "tier": 0}, "send_update_email": {"scope": "mail.send", "tier": 2, "requires_grant": True},
    "update_crm_note": {"scope": "crm.write", "tier": 1}, "read_customer_profile": {"scope": "data.read", "tier": 0},
    # NOT declared on purpose (unknown to everything unless the product declares them or its policy says heuristic):
    #   fetch_reviews_raw, lookup_loyalty_tier
}
# agent -> (parent, requested scopes (before meet), [(tool, context)] it will call, in order)
FANOUT = {
    "trip_planner":  (None, None, [("memorize", {})]),
    # a parent holds what its delegation subtree needs (held_for_delegation — the T13 lesson): web.fetch + compute.pure for its children
    "research_agent": ("trip_planner", {"web.search", "data.read", "state.write", "web.fetch", "compute.pure", "agent.delegate.web_research", "agent.delegate.reviews_scout"},
                       [("google_search", {}), ("weather_impact_check", {}), ("memorize", {})]),
    "web_research":  ("research_agent", {"web.fetch", "compute.pure"}, [("fetch_page", {}), ("summarize_text", {})]),
    "reviews_scout": ("research_agent", {"web.fetch"},
                      [("fetch_reviews_raw", {}), ("fetch_reviews_raw", {}), ("fetch_reviews_raw", {}), ("fetch_reviews_raw", {}), ("fetch_page", {})]),
    "booking_agent": ("trip_planner", {"data.read", "agent.delegate.flights_desk", "agent.delegate.hotels_desk"},
                      [("flight_status_check", {}), ("event_booking_check", {})]),
    "flights_desk":  ("booking_agent", {"data.read"}, [("search_flights", {"rows": 20}), ("book_flight", {"spend": 184.0})]),
    "hotels_desk":   ("booking_agent", {"data.read"}, [("search_hotels", {"rows": 12}), ("create_reservation", {"spend": 420.0})]),
    "payments_agent": ("trip_planner", {"data.read"}, [("payment_choice", {}), ("process_payment", {"spend": 604.0}), ("lookup_loyalty_tier", {})]),
    "support_agent": ("trip_planner", {"data.read"},      # declared READ-ONLY; it will try a CRM write (role violation)
                      [("read_customer_profile", {}), ("send_update_email", {}), ("update_crm_note", {})]),
}
FANOUT_TASKS = {
    "trip_planner": "Plan a 3-day Lisbon trip for Alex: inspiration, flights, hotel, itinerary; book the cheapest flight",
    "research_agent": "Research Lisbon: weather for the 12th-15th, top sights, neighbourhood reviews",
    "web_research": "Fetch and summarise the top 3 Lisbon itineraries", "reviews_scout": "Pull raw reviews for Alfama hotels",
    "booking_agent": "Find the cheapest LIS flight and a hotel in Alfama; check event conflicts",
    "flights_desk": "Search and book the cheapest LIS flight", "hotels_desk": "Search and reserve an Alfama hotel",
    "payments_agent": "Settle the trip: choose a payment method and pay", "support_agent": "Keep Alex informed and note preferences",
}


def run_demo(product_dir: Path, *, slow: float = 0.0, grants: set[str] | None = None, scenario: str = "basic") -> dict:
    if scenario not in ("basic", "fanout"):
        raise ValueError(f"unknown scenario {scenario!r}; choose basic | fanout")
    product_dir = Path(product_dir)
    grants = set(grants) if grants is not None else load_grants(product_dir)
    heur = get_policy(product_dir)["unknown_tools"] == "heuristic"           # product policy for what the catalog cannot resolve
    note_run(product_dir, identity.boot_id(), framework="demo (scripted)", mode="enforce")
    return (_basic if scenario == "basic" else _fanout)(product_dir, grants, heur, slow)


def _issue(product_dir: Path, chain_id: str, root_agent: str, inst: Authority, task: str, **kw) -> Guard:
    return Guard.issue(root_agent, inst, task=task, chain_id=chain_id, audit_path=identity.ledger_path(product_dir, chain_id),
                       audit_sinks=(SpoolSink(identity.spool_path(product_dir)),), **kw)


def _basic(product_dir: Path, grants: set[str], heur: bool, slow: float) -> dict:
    domain = effective_domain(load_domain("travel-booking"), product_dir); cat = load_catalog()   # + the product's declarations
    disp = tool_dispositions(cat, domain, CHILD_TOOLS, grants, heuristics=heur)
    inst_scopes = {sc for sc, d in disp.values() if d is None} | {f"agent.delegate.{BOOKER}"}
    inst = Authority(inst_scopes, [RowLimit(1_000_000), EgressRank("any")], ttl=None)
    chain_id = identity.new_chain_id("demo")
    root = _issue(product_dir, chain_id, PLANNER, inst, "Plan a 3-day trip to Lisbon and book the cheapest flight")
    _pause(slow)
    requested = Authority({"data.read", "payments.transfer"}, [RowLimit(1_000), EgressRank("none")], ttl=None)
    child = root.delegate(BOOKER, requested, task="Find and book the cheapest LIS flight for the 12th")
    _pause(slow)
    for tool, ctx in [("search_flights", {"rows": 20}), ("get_weather", {"rows": 1}), ("book_flight", {"spend": 184.0}), ("lookup_loyalty_tier", {})]:
        scope, d = disp[tool]
        child.check(scope, context=ctx, tool=tool, disposition=d)
        _pause(slow)
    child.complete()
    ev = write_evidence(root, product_dir)
    return {"scenario": "basic", "chain_id": chain_id, "product_dir": str(product_dir), "grants": sorted(grants), "agents": 2,
            "tools": len(CHILD_TOOLS), "child_scopes": sorted(child.authority.scopes), "narrower_than_root": child.is_narrower_than(root), **ev}


def _fanout(product_dir: Path, grants: set[str], heur: bool, slow: float) -> dict:
    base = load_domain("travel-planning")
    base = {**base, "tools": {**(base.get("tools") or {}), **FANOUT_TOOLS}}
    domain = effective_domain(base, product_dir); cat = load_catalog()
    all_tools = sorted({t for _, _, calls in FANOUT.values() for t, _ in calls})
    disp = tool_dispositions(cat, domain, all_tools, grants, heuristics=heur)
    inst_scopes = {sc for sc, d in disp.values() if d is None} | {f"agent.delegate.{a}" for a in FANOUT if a != "trip_planner"}
    inst = Authority(inst_scopes, [RowLimit(1_000_000), EgressRank("any")], ttl=None)
    chain_id = identity.new_chain_id("demo")
    guards: dict[str, Guard] = {}
    guards["trip_planner"] = _issue(product_dir, chain_id, "trip_planner", inst, FANOUT_TASKS["trip_planner"],
                                    strikes=StrikePolicy(n=3, mode="same_scope"))   # the operator's strike policy: 3 same-scope denials -> revoke
    _pause(slow)
    # delegate breadth-first so parents exist before children; requested authority is MET down to what the parent holds
    for agent, (parent, requested, _) in FANOUT.items():
        if parent is None:
            continue
        req = Authority(set(requested), [RowLimit(5_000), EgressRank("none")], ttl=None)
        guards[agent] = guards[parent].delegate(agent, req, task=FANOUT_TASKS[agent])
        _pause(slow)
    # then each agent does its work, in a plausible interleaving (root last)
    order = ["research_agent", "web_research", "reviews_scout", "booking_agent", "flights_desk", "hotels_desk", "payments_agent", "support_agent", "trip_planner"]
    for agent in order:
        g = guards[agent]
        for tool, ctx in FANOUT[agent][2]:
            scope, d = disp[tool]
            g.check(scope, context=ctx, tool=tool, disposition=d)
            _pause(slow)
        if agent != "trip_planner":
            g.complete()
    ev = write_evidence(guards["trip_planner"], product_dir)
    return {"scenario": "fanout", "chain_id": chain_id, "product_dir": str(product_dir), "grants": sorted(grants),
            "agents": len(FANOUT), "tools": len(all_tools),
            "narrower_than_root": {a: guards[a].is_narrower_than(guards["trip_planner"]) for a in FANOUT if a != "trip_planner"}, **ev}


def _pause(s: float) -> None:
    if s > 0:
        time.sleep(s)
