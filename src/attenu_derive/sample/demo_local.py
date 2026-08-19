"""
attenu demo — a USD-0, no-model, no-API-key run that writes a REAL ledger into a product directory, so the console
can be demonstrated anywhere in four commands:

    attenu init --product "Travel Demo" --dir .
    attenu demo [--slow 1]            # this module
    attenu ui

What it does (the A3 travel-booking story, scripted): a `travel_planner` root delegates to a `booking_agent`
whose authority is the **meet** of what the planner holds and what booking needs; the child's reads pass
(`search_flights`, `get_weather` → data.read), its `book_flight` (payments.transfer, curated tier-2) is **HELD
pending an operator grant** unless the product has granted it (`.attenu/grants.json` — the console's Decisions
screen writes that), and a tool the pack does not know (`lookup_loyalty_tier`) is **UNRESOLVED**. Everything lands
on the hash-chained ledger with a `disposition`, is anchored with the product's own Ed25519 key and written as an
offline-verifiable bundle. Honest label: a scripted run — the *enforcement, ledger, anchor and verifier* are the
real ones; only the model's choices are scripted. `--slow` sleeps between steps so a watching UI animates.
"""
from __future__ import annotations

import time
from pathlib import Path

from delegation_guard import Authority, Guard, RowLimit, EgressRank, identity
from delegation_guard.sinks import SpoolSink

from attenu_derive.catalog.coverage import load_catalog, load_domain
from attenu_derive.derive.disposition import tool_dispositions
from attenu_derive.product import load_grants
from attenu_derive.sample.run_adk_enforce import write_evidence

PLANNER = "travel_planner"
BOOKER = "booking_agent"
CHILD_TOOLS = ["search_flights", "get_weather", "book_flight", "lookup_loyalty_tier"]


def run_demo(product_dir: Path, *, slow: float = 0.0, grants: set[str] | None = None) -> dict:
    product_dir = Path(product_dir)
    grants = set(grants) if grants is not None else load_grants(product_dir)
    domain = load_domain("travel-booking"); cat = load_catalog()
    disp = tool_dispositions(cat, domain, CHILD_TOOLS, grants, heuristics=False)

    # installation authority: what the operator is willing to hand this app at all (held tier-2 absent)
    inst_scopes = {sc for sc, d in disp.values() if d is None} | {f"agent.delegate.{BOOKER}"}
    inst = Authority(inst_scopes, [RowLimit(1_000_000), EgressRank("any")], ttl=None)

    chain_id = identity.new_chain_id("demo")
    root = Guard.issue(PLANNER, inst, task="Plan a 3-day trip to Lisbon and book the cheapest flight",
                       chain_id=chain_id, audit_path=identity.ledger_path(product_dir, chain_id),
                       audit_sinks=(SpoolSink(identity.spool_path(product_dir)),))
    _pause(slow)
    # the booking agent asks for what booking needs; `meet` narrows it to what the planner actually holds
    requested = Authority({"data.read", "payments.transfer"}, [RowLimit(1_000), EgressRank("none")], ttl=None)
    child = root.delegate(BOOKER, requested, task="Find and book the cheapest LIS flight for the 12th")
    _pause(slow)
    calls = [("search_flights", {"rows": 20}), ("get_weather", {"rows": 1}), ("book_flight", {"spend": 184.0}),
             ("lookup_loyalty_tier", {})]
    for tool, ctx in calls:
        scope, d = disp[tool]
        child.check(scope, context=ctx, tool=tool, disposition=d)
        _pause(slow)
    child.complete()
    ev = write_evidence(root, product_dir)
    return {"chain_id": chain_id, "product_dir": str(product_dir), "grants": sorted(grants),
            "child_scopes": sorted(child.authority.scopes), "narrower_than_root": child.is_narrower_than(root),
            **ev}


def _pause(s: float) -> None:
    if s > 0:
        time.sleep(s)
