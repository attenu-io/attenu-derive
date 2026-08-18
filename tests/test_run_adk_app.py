"""Generic ADK-app runner (real-world workloads): tree walking, declared suites from the real tree, model override + Gemini-only tool stubbing."""
import pytest
pytest.importorskip("google.adk")
pytest.importorskip("litellm")
from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools import google_search

from attenu_derive.sample.run_adk_app import declared_suites, override_models, walk_agents


def _tree():
    def get_customer(customer_id: str) -> dict:
        """Get a customer."""
        return {}
    def send_email(to: str) -> dict:
        """Send."""
        return {}
    analyst = LlmAgent(name="data_analyst", model="gemini-2.5-pro", description="d", tools=[google_search])
    risk = LlmAgent(name="risk_analyst", model="gemini-2.5-pro", description="r", tools=[get_customer])
    root = LlmAgent(name="coordinator", model="gemini-2.5-pro", description="c", tools=[AgentTool(agent=analyst), AgentTool(agent=risk), send_email])
    return root


def test_declared_suites_come_from_the_real_tree():
    root = _tree()
    assert [a.name for a in walk_agents(root)] and len(walk_agents(root)) == 3
    tools, subs = declared_suites(root)
    assert tools["coordinator"] == ["data_analyst", "risk_analyst", "send_email"]           # AgentTools appear under the parent as the child's name
    assert tools["risk_analyst"] == ["get_customer"] and tools["data_analyst"] == ["google_search"]
    assert subs["coordinator"] == {"data_analyst": ["google_search"], "risk_analyst": ["get_customer"]}


def test_override_models_routes_the_whole_tree_and_stubs_gemini_only_tools():
    root = _tree()
    changed = override_models(root, "anthropic/claude-haiku-4-5-20251001")
    assert set(changed["agents"]) == {"coordinator", "data_analyst", "risk_analyst"}
    assert changed["stubbed_tools"] == ["data_analyst:GoogleSearchTool"]
    from google.adk.models.lite_llm import LiteLlm
    assert all(isinstance(a.model, LiteLlm) for a in walk_agents(root))
    tools, _ = declared_suites(root)
    assert tools["data_analyst"] == ["google_search"]                                        # the stub keeps the tool NAME (the call is still recorded)
    root2 = _tree(); ch2 = override_models(root2, "gemini-3.6-flash")
    assert ch2["stubbed_tools"] == [] and all(a.model == "gemini-3.6-flash" for a in walk_agents(root2))
