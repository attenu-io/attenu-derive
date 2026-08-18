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
    from delegation_guard import Authority, Guard
    from delegation_guard.adapters.google_adk import DelegationGuardPlugin

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
    g_ok, calls_ok = build({"mail.send"})
    assert not [c for c in calls_ok if isinstance(c, dict) and c.get("error") == "authority_denied"]   # GRANTED -> passes
