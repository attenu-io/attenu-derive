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

from delegation_guard import Authority, Guard, RowLimit, EgressRank
from delegation_guard.wire import HS256TestSigner

from attenu_derive.catalog.coverage import load_catalog, load_domain, resolve
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


def tool_authorities(agent, domain: dict):
    """{tool_name: ToolAuthority(scope)} for an agent's tools, resolved through the CURATED pack (enforce = curated only)."""
    from delegation_guard.adapters.google_adk import ToolAuthority
    from google.adk.tools.agent_tool import AgentTool
    cat = load_catalog(); out = {}
    for t in (getattr(agent, "tools", None) or []):
        if isinstance(t, AgentTool):
            continue                                    # delegation, governed by `delegations`
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        e = resolve(cat, name, overlay=domain)
        if e and not str(e.get("scope", "")).startswith("unknown."):
            out[name] = ToolAuthority(e["scope"])
    return out


def run(app_dir: Path, prompt: str, *, domain_name: str, grants: set[str], model: str, max_llm_calls: int, timeout_s: float):
    import asyncio
    from google.adk.agents.run_config import RunConfig
    from google.adk.apps.app import App
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.genai import types
    from delegation_guard.adapters.google_adk import DelegationGuardPlugin

    domain = load_domain(domain_name)
    root_agent = load_root_agent(app_dir)
    changed = override_models(root_agent, model)
    agents = walk_agents(root_agent)
    tools_by_agent, subs_by_agent = declared_suites(root_agent)

    inst = installation_authority(domain, grants)
    # sub-agents (multi-agent apps): each is delegated the installation authority, meet-narrowed by the chain.
    delegations = {a.name: inst for a in agents if a is not root_agent}
    if delegations:
        inst = Authority(set(inst.scopes) | {f"agent.delegate.{a.name}" for a in agents if a is not root_agent}, inst.ceilings, ttl=None)
    tools = {}
    for a in agents:
        tools.update(tool_authorities(a, domain))

    root_guard = Guard.issue(root_agent.name, inst, task=prompt[:60])
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
                        denials_in_transcript.append({"tool": part.function_response.name, "scope": resp.get("scope"), "reasons": resp.get("reasons")})
        return tool_calls, denials_in_transcript
    async def _bounded():
        return await asyncio.wait_for(_go(), timeout=timeout_s)
    try:
        tool_calls, denials = asyncio.run(_bounded())
    except Exception as exc:                                          # noqa: BLE001
        tool_calls, denials = [], [{"error": f"{type(exc).__name__}: {str(exc)[:120]}"}]

    entries = root_guard.audit_log().entries
    ledger_denies = [e for e in entries if e.get("event") == "deny"]
    signer = HS256TestSigner(secret=os.urandom(16), kid="attenu-anchor")
    anchor = root_guard.audit_log().anchor(signer)
    from delegation_guard import AuditLog
    anchor_ok, _ = AuditLog.verify_anchor(entries, anchor, signer)
    return {"prompt": prompt, "model": model, "domain": domain_name, "operator_grants": sorted(grants),
            "installation_scopes": sorted(inst.scopes), "stubbed_tools": changed["stubbed_tools"],
            "tool_calls": tool_calls, "denials_returned_to_model": denials,
            "ledger_deny_events": [{"scope": e.get("scope"), "tool": e.get("tool"), "reason": e.get("reason")} for e in ledger_denies],
            "ledger_entries": len(entries), "anchor": {"seq": anchor["seq"], "head": anchor["head"], "verified": anchor_ok}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True); ap.add_argument("--prompt", required=True)
    ap.add_argument("--domain", required=True); ap.add_argument("--grant", action="append", default=[], help="a scope the operator enabled (repeatable)")
    ap.add_argument("--model", default="anthropic/claude-haiku-4-5-20251001")
    ap.add_argument("--max-llm-calls", type=int, default=20); ap.add_argument("--timeout-s", type=float, default=180.0)
    ap.add_argument("--out", default=None, help="write the evidence JSON here")
    args = ap.parse_args(argv)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    if not args.model.startswith("gemini") and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr); return 2
    rep = run(Path(args.app), args.prompt, domain_name=args.domain, grants=set(args.grant), model=args.model,
              max_llm_calls=args.max_llm_calls, timeout_s=args.timeout_s)
    out = json.dumps(rep, indent=2)
    if args.out:
        Path(args.out).write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
