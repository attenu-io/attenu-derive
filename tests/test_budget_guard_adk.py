"""T9 (PM W2 slice, 2026-08-18): the ADK cost guardrail must count DELEGATED (sub-agent) model usage —
same class as T1 (deepagents) — because that is the traffic that loops. RED if usage from a child
agent's model call never reaches the guard, or if crossing the cap does not abort the run."""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import pytest

pytest.importorskip("google.adk")

from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from delegation_guard import Authority, Guard
from delegation_guard.adapters.google_adk import DelegationGuardPlugin, ToolAuthority

from attenu_derive.sample.run_adk import BudgetExceeded, BudgetPlugin, estimate_cost, make_plugin

STEP = 1_000
_AGENT_LABEL = "adk_agent_name"


class UsageScriptedLlm(BaseLlm):
    """Per-agent scripted parts; every response reports STEP prompt tokens."""
    model: str = "usage-scripted"
    script: dict = {}

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        labels = (llm_request.config.labels or {}) if llm_request.config else {}
        agent = labels.get(_AGENT_LABEL)
        queue = self.script.get(agent) or []
        part = queue.pop(0) if queue else types.Part.from_text(text=f"[{agent}] finished.")
        yield LlmResponse(content=types.Content(role="model", parts=[part]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=STEP, candidates_token_count=10, total_token_count=STEP + 10))


def _tree(model, calls: list):
    def read_file(path: str) -> dict:
        """Read a file."""
        calls.append(("read_file", path)); return {"content": "..."}
    child = LlmAgent(name="researcher", model=model, description="Explores.", tools=[read_file])
    root = LlmAgent(name="orchestrator", model=model, description="Routes.", tools=[AgentTool(agent=child)])
    return root


async def _run(max_input_tokens: int):
    calls: list = []
    model = UsageScriptedLlm(script={
        "orchestrator": [types.Part.from_function_call(name="researcher", args={"request": "explore"}), types.Part.from_text(text="done")],
        "researcher": [types.Part.from_function_call(name="read_file", args={"path": "README.md"}), types.Part.from_text(text="found")],
    })
    root_guard, dg = make_plugin(salt="s")
    budget = BudgetPlugin(max_input_tokens)
    app = App(name="t9", root_agent=_tree(model, calls), plugins=[dg, budget])
    sessions = InMemorySessionService(); runner = Runner(app=app, session_service=sessions)
    session = await sessions.create_session(app_name="t9", user_id="u")
    err = None
    try:
        async for _ in runner.run_async(user_id="u", session_id=session.id,
                                        new_message=types.Content(role="user", parts=[types.Part.from_text(text="go")])):
            pass
    except Exception as exc:      # noqa: BLE001
        err = exc
    return budget, root_guard, calls, err


def test_delegated_usage_is_counted():
    budget, root_guard, calls, err = asyncio.run(_run(max_input_tokens=10 * STEP))
    assert err is None
    # orchestrator: 2 model calls; researcher (DELEGATED, via AgentTool): 2 model calls -> 4 x STEP
    assert budget.used == 4 * STEP, budget.used
    assert budget.by_agent.get("researcher", 0) == 2 * STEP          # the delegated share is what T1 found missing
    assert ("read_file", "README.md") in calls


def test_budget_aborts_mid_run_including_the_child():
    budget, root_guard, calls, err = asyncio.run(_run(max_input_tokens=int(1.5 * STEP)))
    assert err is not None and (isinstance(err, BudgetExceeded) or isinstance(err.__cause__, BudgetExceeded)), err
    assert budget.used > 1.5 * STEP and budget.used <= 3 * STEP        # aborted at the 2nd model call, not after the run
    assert budget.aborted
    # audit log is complete up to the abort: the spawn (if reached) or at least the root exists
    assert root_guard.audit_log().entries


def test_estimate_cost_is_free_tier_aware():
    assert estimate_cost("gemini-2.5-flash", 1_000_000, 0) == pytest.approx(0.30)
    assert estimate_cost("gemini-2.5-flash-lite", 1_000_000, 0) < 0.30
