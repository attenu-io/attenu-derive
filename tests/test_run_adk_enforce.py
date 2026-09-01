"""T30: live-enforce wiring (offline, scripted model) — the deriver -> pack -> meet -> shim-enforce path denies a
tool whose scope the operator did not grant, and passes it once granted. Live Haiku/Sonnet runs are in the eval cards."""
import pytest
pytest.importorskip("google.adk")
pytest.importorskip("litellm")

from attenu_derive.catalog.coverage import load_domain
from attenu_derive.sample.run_adk_enforce import installation_authority, tool_authorities


def test_installation_authority_holds_requires_grant_until_operator_opts_in():
    dom = load_domain("retail-support")
    assert "mail.send" not in installation_authority(dom, set()).scopes            # held pending grant
    assert "mail.send" in installation_authority(dom, {"mail.send"}).scopes
    assert {"crm.write", "data.read", "data.write"} <= set(installation_authority(dom, set()).scopes)


def test_enforce_denies_a_held_scope_and_passes_a_granted_one():
    """Drive the real ADK Runner with a scripted model that calls send_care_instructions (mail.send)."""
    import asyncio
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.apps.app import App
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.genai import types
    from attenu_guard import Authority, Guard
    from attenu_guard.adapters.google_adk import DelegationGuardPlugin

    dom = load_domain("retail-support")

    class Scripted(BaseLlm):
        model: str = "scripted"
        fired: bool = False
        async def generate_content_async(self, llm_request, stream=False):
            if not self.fired:
                self.fired = True
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_function_call(name="send_care_instructions", args={"customer_id": "1"})]))
            else:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="done")]))

    def send_care_instructions(customer_id: str) -> dict:
        """Sends care instructions."""
        return {"sent": True}

    def build(grant):
        m = Scripted(); agent = LlmAgent(name="customer_service_agent", model=m, description="cs", tools=[send_care_instructions])
        inst = installation_authority(dom, grant); g = Guard.issue(agent.name, inst, task="t")
        plugin = DelegationGuardPlugin(g, root_agent_name=agent.name, delegations={}, tools=tool_authorities(agent, dom))
        app = App(name="t", root_agent=agent, plugins=[plugin]); ss = InMemorySessionService()
        runner = Runner(app=app, session_service=ss)
        async def go():
            s = await ss.create_session(app_name="t", user_id="u"); calls = []
            async for ev in runner.run_async(user_id="u", session_id=s.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text="email care")])):
                for p in (getattr(getattr(ev, "content", None), "parts", None) or []):
                    if getattr(p, "function_response", None): calls.append(p.function_response.response)
            return g, calls
        return asyncio.run(go())

    g_held, calls_held = build(set())
    denied = [c for c in calls_held if isinstance(c, dict) and c.get("error") == "authority_denied"]
    assert denied and denied[0]["scope"] == "mail.send"                             # HELD -> denied live
    assert any(e["event"] == "deny" and e.get("scope") == "mail.send" for e in g_held.audit_log().entries)
    held_deny = [e for e in g_held.audit_log().entries if e["event"] == "deny" and e.get("scope") == "mail.send"][0]
    assert held_deny["disposition"] == "held_pending_grant"                           # the ledger now says WHY (Plan A)
    assert denied[0]["disposition"] == "held_pending_grant"                           # and so does the denial handed to the model
    g_ok, calls_ok = build({"mail.send"})
    assert not [c for c in calls_ok if isinstance(c, dict) and c.get("error") == "authority_denied"]   # GRANTED -> passes


def test_chain_enforce_child_is_narrower_and_denied_a_scope_the_parent_lacks():
    """T34 (offline, scripted): monotonic attenuation across a real ADK delegation chain — a coordinator delegates to an
    analyst; the analyst is minted meet(parent, request) (⊂ parent) and a call needing a scope the parent lacks is denied."""
    import asyncio
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.apps.app import App
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.adk.tools.agent_tool import AgentTool
    from google.genai import types
    from attenu_guard import Authority, Guard, RowLimit, EgressRank
    from attenu_guard.adapters.google_adk import DelegationGuardPlugin, ToolAuthority

    class Chain(BaseLlm):
        model: str = "scripted"; seen: dict = {}
        async def generate_content_async(self, llm_request, stream=False):
            labels = (llm_request.config.labels or {}) if llm_request.config else {}
            who = labels.get("adk_agent_name"); i = self.seen.get(who, 0); self.seen[who] = i + 1
            if who == "coordinator" and i == 0:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_function_call(name="analyst", args={"request": "analyse GOOGL"})]))
            elif who == "analyst" and i == 0:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_function_call(name="web_search", args={"q": "GOOGL"})]))
            else:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="done")]))

    def web_search(q: str) -> dict:
        """Search the web."""
        return {"results": []}

    m = Chain()
    analyst = LlmAgent(name="analyst", model=m, description="a", tools=[web_search])
    coordinator = LlmAgent(name="coordinator", model=m, description="c", tools=[AgentTool(agent=analyst)])
    # the coordinator (root) holds delegate scopes but NOT web.search — so the analyst cannot inherit it
    root_auth = Authority({"agent.delegate.analyst"}, [RowLimit(10**6), EgressRank("any")], ttl=None)
    root_guard = Guard.issue("coordinator", root_auth, task="t")
    plugin = DelegationGuardPlugin(root_guard, root_agent_name="coordinator",
                                   delegations={"analyst": Authority({"web.search"}, [], ttl=None)},   # analyst REQUESTS web.search...
                                   tools={"web_search": ToolAuthority("web.search")}, delegation_scope="agent.delegate")
    app = App(name="t", root_agent=coordinator, plugins=[plugin]); ss = InMemorySessionService()
    runner = Runner(app=app, session_service=ss)
    async def go():
        from google.adk.agents.run_config import RunConfig
        s = await ss.create_session(app_name="t", user_id="u"); denied = []
        async for ev in runner.run_async(user_id="u", session_id=s.id, run_config=RunConfig(max_llm_calls=8), new_message=types.Content(role="user", parts=[types.Part.from_text(text="go")])):
            for p in (getattr(getattr(ev, "content", None), "parts", None) or []):
                if getattr(p, "function_response", None) and isinstance(p.function_response.response, dict) and p.function_response.response.get("error") == "authority_denied":
                    denied.append(p.function_response.response)
        return denied
    denied = asyncio.run(go())
    analyst_guard = plugin.guard_for("analyst")
    assert analyst_guard.is_narrower_than(root_guard)                              # child ⊆ parent (meet removed web.search)
    assert not analyst_guard.authority.covers_scope("web.search")                  # ...because the parent never held it
    # the LEDGER is the source of truth (an AgentTool sub-run's denial may not surface as a top-level event): the analyst's
    # web.search call was denied mid-chain, on the same chain ledger the coordinator's root guard owns.
    denies = [e for e in root_guard.audit_log().entries if e["event"] == "deny" and e.get("scope") == "web.search"]
    assert denies, "analyst's web.search was not denied on the chain ledger"
    spawns = [e for e in root_guard.audit_log().entries if e["event"] == "spawn" and e.get("agent") == "analyst"]
    assert spawns, "the coordinator never spawned the analyst"


def test_unresolved_tools_are_registered_and_denied_as_unresolved():
    """A tool the curated pack does not know is declared with an unknown.* scope and disposition=unresolved, so a
    call to it is denied AS unresolved (not silently treated like over-reach, and not waved through)."""
    from attenu_derive.catalog.coverage import load_domain
    from attenu_derive.sample.run_adk_enforce import tool_authorities
    class T:  # a minimal stand-in for an ADK tool object
        def __init__(self, name): self.name = name
    class A:
        tools = [T("send_care_instructions"), T("totally_unknown_tool")]
    ta = tool_authorities(A(), load_domain("retail-support"))
    assert ta["send_care_instructions"].scope == "mail.send" and ta["send_care_instructions"].disposition == "held_pending_grant"
    assert ta["totally_unknown_tool"].scope == "unknown.totally_unknown_tool" and ta["totally_unknown_tool"].disposition == "unresolved"
    granted = tool_authorities(A(), load_domain("retail-support"), grants={"mail.send"})
    assert granted["send_care_instructions"].disposition is None


def test_run_with_a_product_dir_uses_the_product_anchor_key_never_the_test_signer(tmp_path, monkeypatch):
    """RED->GREEN for custody: inside a product, the evidence must verify with the product's PUBLIC key and the
    test HMAC signer must not appear; the ledger + bundle land under the product dir."""
    import json
    from pathlib import Path
    from attenu_derive import product
    from attenu_derive.sample import run_adk_enforce as R
    from attenu_guard import Authority, Guard, evidence, identity
    monkeypatch.setenv("ATTENU_HOME", str(tmp_path / "home")); product.init_product(tmp_path / "proj", "CS", "dev")
    monkeypatch.setenv("ATTENU_PRODUCT_DIR", str(tmp_path / "proj"))
    pd = identity.find_product_dir()
    chain_id = identity.new_chain_id("adk")
    g = Guard.issue("customer_service_agent", Authority({"crm.read"}, [], ttl=None), task="t",
                    chain_id=chain_id, audit_path=identity.ledger_path(pd, chain_id))
    g.check("mail.send", tool="send_care_instructions", disposition="held_pending_grant")
    out = R.write_evidence(g, pd)
    assert out["anchor_kid"] != "attenu-anchor-TEST" and out["anchor_kid"] == product.load_product_json(pd)["anchor_kid"]
    bundle = json.loads(Path(out["bundle_path"]).read_text())
    assert evidence.verify_bundle(bundle, product.load_anchor_verifier(pd))["ok"] is True
    assert Path(out["ledger_path"]).exists() and "/.attenu/ledger/" in out["ledger_path"]
    assert "/.attenu/evidence/" in out["bundle_path"]
    # and OUTSIDE a product the runner still works, but says so loudly
    out2 = R.write_evidence(Guard.issue("x", Authority({"crm.read"}, [], ttl=None), task="t"), None)
    assert out2["anchor_kid"] == "attenu-anchor-TEST" and out2["bundle_path"] is None


def test_delegation_requests_include_the_agents_own_descendants():
    """The T13 rule applied to the enforce runner: a mid-tree agent must be able to delegate to ITS declared children
    (found live on travel-concierge: planning_agent -> flight_search_agent was denied out_of_authority because every
    sub-agent was delegated the installation authority captured BEFORE the delegate scopes were added)."""
    from attenu_derive.catalog.coverage import load_domain
    from attenu_derive.sample.run_adk_enforce import delegation_requests, installation_authority
    class A:
        def __init__(self, name): self.name = name
    agents = [A("root_agent"), A("planning_agent"), A("flight_search_agent"), A("hotel_search_agent"), A("booking_agent")]
    subs = {"root_agent": {"planning_agent": [], "booking_agent": []}, "planning_agent": {"flight_search_agent": [], "hotel_search_agent": []}}
    inst = installation_authority(load_domain("travel-planning"), set())
    inst_full, delegations = delegation_requests(inst, agents, agents[0], subs)
    assert {f"agent.delegate.{a.name}" for a in agents[1:]} <= set(inst_full.scopes)                  # root holds every descendant
    assert {"agent.delegate.flight_search_agent", "agent.delegate.hotel_search_agent"} <= set(delegations["planning_agent"].scopes)
    assert not {s for s in delegations["flight_search_agent"].scopes if s.startswith("agent.delegate.")}   # a leaf delegates to nobody
    assert "agent.delegate.planning_agent" not in delegations["booking_agent"].scopes                      # only its OWN subtree
    assert set(delegations["planning_agent"].scopes) <= set(inst_full.scopes)                              # requests never exceed the installation


def test_delegation_requests_survives_camelcase_agent_names():
    """Delta review (post e459db8): the more reachable half of the D1 grammar problem. Unlike `unknown.<tool>`,
    `agent.delegate.<name>` here is built straight from a declared ADK agent's `.name` into an `Authority`
    constructed DIRECTLY (not filtered through tool_dispositions) -- so a CamelCase agent name (routine for
    ADK/CrewAI agent classes) crashes the deriver's own installation/delegation authority, not just an eval.
    `Authority(scopes=["agent.delegate.TripPlanner"])` raises against the real guard 0.10.0."""
    from attenu_derive.catalog.coverage import load_domain
    from attenu_derive.derive.scope_grammar import delegate_scope
    from attenu_derive.sample.run_adk_enforce import delegation_requests, installation_authority
    class A:
        def __init__(self, name): self.name = name
    root = A("RootPlanner"); mid = A("TripPlanner"); grandchild = A("HotelBot")   # a mid-tree CamelCase agent delegating to another
    inst = installation_authority(load_domain("travel-planning"), set())
    inst_full, delegations = delegation_requests(inst, [root, mid, grandchild], root,
                                                  {"RootPlanner": {"TripPlanner": []}, "TripPlanner": {"HotelBot": []}})   # must not raise
    assert delegate_scope("TripPlanner") in inst_full.scopes            # == "agent.delegate.trippplanner" -- normalised
    assert delegate_scope("HotelBot") in inst_full.scopes
    assert "agent.delegate.TripPlanner" not in inst_full.scopes         # the raw (invalid) form from before the fix
    assert delegate_scope("HotelBot") in delegations[mid.name].scopes   # the mid-tree agent's OWN descendant (descendants()), also normalised
