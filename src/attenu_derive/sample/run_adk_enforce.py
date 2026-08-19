"""
attenu — LIVE ENFORCE of a real Google ADK application (T30). Unlike the observe runners, this issues each agent
the DERIVED authority (from the deriver + a curated domain pack + operator grants) and installs the shim in
ENFORCE mode, so a tool call outside that authority is actually DENIED mid-run — the deriver → meet → enforce
path a customer runs, end to end, on a real model.

    python -m attenu_derive.sample.run_adk_enforce --app <dir>/customer_service --prompt "..." \
        --domain retail-support --grant crm.write --grant data.write --model anthropic/claude-haiku-4-5-...

Demonstrates both G2 clauses live on one app:
  - with the workload's scopes granted, its own calls pass  -> 0 benign blocks;
  - a scope left UNGRANTED (e.g. mail.send held pending an operator grant) makes the matching tool call DENIED
    live, with the machine-readable denial handed back to the model (denial contract) and a `deny` on the
    hash-chained ledger, which is then ANCHORED (T27).

Scope resolution is the curated pack (per-tool), never a heuristic — enforce runs on curated authority only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from delegation_guard import Authority, Guard, RowLimit, EgressRank, identity

from attenu_derive.catalog.coverage import load_catalog, load_domain
from attenu_derive.derive.disposition import tool_dispositions
from attenu_derive.evidence_out import effective_grants, product_meta as _product_meta, write_evidence  # noqa: F401 (re-exported)
from attenu_derive.sample.run_adk_app import declared_suites, load_root_agent, override_models, walk_agents

# The installation's ceiling: the scopes an operator is willing to hand this app AT ALL. The derived authority
# narrows within it; a scope the operator did not enable (a requires_grant tool they left off) is simply absent.
def installation_authority(domain: dict, operator_grants: set[str]) -> Authority:
    scopes = set()
    for e in (domain.get("tools") or {}).values():
        sc = e.get("scope")
        if not sc:
            continue
        if e.get("requires_grant") and sc not in operator_grants:
            continue                                    # held pending an operator grant: NOT in the installation authority
        scopes.add(sc)
    scopes |= {f"agent.delegate.{a.name}" for a in []}   # (single-agent apps have no sub-agents; multi-agent adds them below)
    return Authority(scopes, [RowLimit(1_000_000), EgressRank("any")], ttl=None)


def tool_authorities(agent, domain: dict, *, grants=frozenset(), held=frozenset(), heuristics: bool = False):
    """{tool_name: ToolAuthority(scope, disposition=...)} for an agent's tools, resolved through the CURATED pack
    (enforce = curated only). Every tool is declared — including the ones the pack does not know, which get an
    `unknown.<name>` scope and `disposition=unresolved` so a call to them is denied AS unresolved rather than
    looking like over-reach. A curated tier-2 tool without a grant (or a scope the operator held back) is declared
    `held_pending_grant`: the shim still denies it (the scope is absent from the installation authority), but the
    ledger and the denial say "waiting on you", not "stopped"."""
    from delegation_guard.adapters.google_adk import ToolAuthority
    try:
        from google.adk.tools.agent_tool import AgentTool
    except Exception:  # noqa: BLE001 - tests may pass plain stand-ins without google.adk on the path
        AgentTool = ()
    names = []
    for t in (getattr(agent, "tools", None) or []):
        if AgentTool and isinstance(t, AgentTool):
            continue                                    # delegation, governed by `delegations`
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        if name:
            names.append(name)
    disp = tool_dispositions(load_catalog(), domain, names, set(grants), held=set(held), heuristics=heuristics)
    return {name: ToolAuthority(scope, disposition=d) for name, (scope, d) in disp.items()}


def delegation_requests(inst: Authority, agents, root_agent, subs_by_agent: dict) -> tuple[Authority, dict]:
    """(installation authority incl. agent.delegate.* for EVERY non-root agent, {sub-agent: requested Authority}).
    A parent holds what its delegation subtree needs (the T13 rule): each sub-agent requests the installation's
    grantable scopes PLUS `agent.delegate.<x>` for its OWN declared descendants (transitively) — a mid-tree agent can
    delegate to its children, a leaf delegates to nobody, nobody requests a sibling's subtree; `meet` narrows each
    request to what its parent actually holds."""
    names = {a.name for a in agents if a is not root_agent}
    children = {a: set(c.keys()) for a, c in (subs_by_agent or {}).items()}

    def descendants(name: str, seen=None) -> set[str]:
        seen = seen if seen is not None else set()
        for c in children.get(name, ()):
            if c not in seen and c in names:
                seen.add(c); descendants(c, seen)
        return seen

    inst_full = Authority(set(inst.scopes) | {f"agent.delegate.{n}" for n in names}, inst.ceilings, ttl=None)
    delegations = {}
    for a in agents:
        if a is root_agent:
            continue
        delegations[a.name] = Authority(set(inst.scopes) | {f"agent.delegate.{d}" for d in descendants(a.name)}, inst.ceilings, ttl=None)
    return inst_full, delegations


def run(app_dir: Path, prompt: str, *, domain_name: str, grants: set[str], model: str, max_llm_calls: int, timeout_s: float, hold: set[str] | None = None):
    import asyncio
    from google.adk.agents.run_config import RunConfig
    from google.adk.apps.app import App
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.genai import types
    from delegation_guard.adapters.google_adk import DelegationGuardPlugin

    from attenu_derive import license
    license.require("enforce", identity.find_product_dir())        # the licence gate — at START, never mid-run
    from attenu_derive.product import effective_domain
    domain = effective_domain(load_domain(domain_name), identity.find_product_dir())   # + the product's declarations (Decisions -> Declare)
    grants = effective_grants(grants, identity.find_product_dir())
    root_agent = load_root_agent(app_dir)
    changed = override_models(root_agent, model)
    agents = walk_agents(root_agent)
    tools_by_agent, subs_by_agent = declared_suites(root_agent)

    inst = installation_authority(domain, grants)
    hold = hold or set()
    if hold:                                            # demo lever: force these scopes OUT of the installation authority (an operator who did not enable them)
        inst = Authority(set(inst.scopes) - hold, inst.ceilings, ttl=None)
    # sub-agents (multi-agent apps): each requests the installation's grantable scopes + delegate scopes for its OWN
    # declared descendants; the chain's meet narrows each request to what its parent holds.
    inst, delegations = delegation_requests(inst, agents, root_agent, subs_by_agent)
    from attenu_derive.product import get_policy
    product_dir_for_policy = identity.find_product_dir()
    heur = bool(product_dir_for_policy) and get_policy(product_dir_for_policy)["unknown_tools"] == "heuristic"
    if heur:
        print("attenu: product policy unknown_tools=heuristic — tier-0/1 name heuristics may grant unknown tools (tier-2 always withheld)", file=sys.stderr)
    tools = {}
    for a in agents:
        tools.update(tool_authorities(a, domain, grants=grants, held=hold, heuristics=heur))
    if heur:   # heuristic-grantable scopes join the installation ceiling (they are what the policy allows); curated semantics unchanged
        extra = {ta.scope for ta in tools.values() if ta.disposition is None and not ta.scope.startswith("unknown.")}
        inst = Authority(set(inst.scopes) | extra, inst.ceilings, ttl=None)

    # Product identity (console design §5a): inside a product dir the ledger, spool and evidence live under
    # `.attenu/` with an assigned chain id and a per-process boot id; outside one, the runner behaves as before.
    product_dir = identity.find_product_dir()
    chain_id = identity.new_chain_id("adk")
    issue_kwargs = {}
    if product_dir is not None:
        from delegation_guard.sinks import SpoolSink
        from attenu_derive.product import note_run
        note_run(product_dir, identity.boot_id(), framework="google-adk", mode="enforce")
        issue_kwargs = {"chain_id": chain_id, "audit_path": identity.ledger_path(product_dir, chain_id),
                        "audit_sinks": (SpoolSink(identity.spool_path(product_dir)),)}
    root_guard = Guard.issue(root_agent.name, inst, task=prompt[:60], **issue_kwargs)
    plugin = DelegationGuardPlugin(root_guard, root_agent_name=root_agent.name, delegations=delegations, tools=tools,
                                   delegation_scope="agent.delegate")   # ENFORCE: no observe hooks; undeclared tool/agent fails closed
    app = App(name="attenu-enforce", root_agent=root_agent, plugins=[plugin])
    sessions = InMemorySessionService(); runner = Runner(app=app, session_service=sessions)

    async def _go():
        session = await sessions.create_session(app_name="attenu-enforce", user_id="u")
        tool_calls = []; denials_in_transcript = []
        async for ev in runner.run_async(user_id="u", session_id=session.id, run_config=RunConfig(max_llm_calls=max_llm_calls),
                                         new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)])):
            for part in (getattr(getattr(ev, "content", None), "parts", None) or []):
                if getattr(part, "function_call", None):
                    tool_calls.append(part.function_call.name)
                if getattr(part, "function_response", None):
                    resp = part.function_response.response
                    if isinstance(resp, dict) and resp.get("error") == "authority_denied":
                        denials_in_transcript.append({"tool": part.function_response.name, "scope": resp.get("scope"), "reasons": resp.get("reasons"),
                                                      "disposition": resp.get("disposition")})
        return tool_calls, denials_in_transcript
    async def _bounded():
        return await asyncio.wait_for(_go(), timeout=timeout_s)
    try:
        tool_calls, denials = asyncio.run(_bounded())
    except Exception as exc:                                          # noqa: BLE001
        tool_calls, denials = [], [{"error": f"{type(exc).__name__}: {str(exc)[:120]}"}]

    # delegation graph: parent + child derived authorities (proves child ⊆ parent)
    graph = {}
    for a in agents:
        try:
            g = plugin.guard_for(a.name)
        except (KeyError, Exception):                   # an agent that did not run this session was never minted a Guard
            g = None
        if g is not None:
            graph[a.name] = {"scopes": sorted(g.authority.scopes),
                             "narrower_than_root": g.is_narrower_than(root_guard) if a is not root_agent else None,
                             "parent": next((x.get("parent") for x in root_guard.audit_log().entries if x.get("event") == "spawn" and x.get("agent") == a.name), None)}
    entries = root_guard.audit_log().entries
    ledger_denies = [e for e in entries if e.get("event") == "deny"]
    spawns = [e for e in entries if e.get("event") == "spawn"]
    ev = write_evidence(root_guard, product_dir)
    return {"prompt": prompt, "model": model, "domain": domain_name, "operator_grants": sorted(grants),
            "installation_scopes": sorted(inst.scopes), "stubbed_tools": changed["stubbed_tools"],
            "tool_calls": tool_calls, "denials_returned_to_model": denials,
            "ledger_deny_events": [{"scope": e.get("scope"), "tool": e.get("tool"), "reason": e.get("reason"),
                                    "disposition": e.get("disposition")} for e in ledger_denies],
            "ledger_entries": len(entries), "delegation_graph": graph, "spawns": [{"agent": e.get("agent"), "parent": e.get("parent")} for e in spawns],
            "product": ({"dir": str(product_dir), **{k: v for k, v in _product_meta(product_dir).items() if k in ("product_id", "name", "environment")}}
                        if product_dir is not None else None),
            "chain_id": root_guard.chain_id, **ev}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True); ap.add_argument("--prompt", required=True)
    ap.add_argument("--domain", required=True); ap.add_argument("--grant", action="append", default=[], help="a scope the operator enabled (repeatable)")
    ap.add_argument("--model", default="anthropic/claude-haiku-4-5-20251001")
    ap.add_argument("--max-llm-calls", type=int, default=20); ap.add_argument("--timeout-s", type=float, default=180.0)
    ap.add_argument("--hold", action="append", default=[], help="a scope to force OUT of the installation authority (demo: operator did not enable it)")
    ap.add_argument("--out", default=None, help="write the evidence JSON here")
    args = ap.parse_args(argv)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    if not args.model.startswith("gemini") and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr); return 2
    rep = run(Path(args.app), args.prompt, domain_name=args.domain, grants=set(args.grant), model=args.model,
              max_llm_calls=args.max_llm_calls, timeout_s=args.timeout_s, hold=set(args.hold))
    out = json.dumps(rep, indent=2)
    if args.out:
        Path(args.out).write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
