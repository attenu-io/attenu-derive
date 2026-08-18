"""T1 (PM slice, 2026-08-18): the cost guardrail must count DELEGATED (sub-agent) model usage,
because that is exactly the traffic that loops. RED if sub-agent usage never reaches the guard."""
from __future__ import annotations

from typing import Any, List, Optional

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langchain")

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from delegation_guard import Authority, Guard
from delegation_guard.adapters.langchain import GuardedDelegation, ToolPolicy

from attenu_derive.sample.run_deepagents import BudgetExceeded, _BudgetGuard, estimate_cost

STEP_TOKENS = 1_000


class UsageScriptedModel(BaseChatModel):
    """Replays scripted AIMessages; every step reports STEP_TOKENS input tokens."""
    responses: List[AIMessage]
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "usage-scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = min(self.idx, len(self.responses) - 1); self.idx += 1
        msg = self.responses[i].model_copy()
        msg.usage_metadata = {"input_tokens": STEP_TOKENS, "output_tokens": 10, "total_tokens": STEP_TOKENS + 10}
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _call(name, args, cid):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid, "type": "tool_call"}])


def build_agent(parent_steps_before_done: int, sub_steps: int):
    """Parent: 1 step that delegates via `task`, then done. Sub-agent: `sub_steps` tool steps, then done."""
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend  # in-memory FS

    from langchain_core.tools import tool

    @tool
    def peek(x: int) -> str:
        """no-op tool"""
        return "ok"

    root = Guard.issue("orchestrator", Authority({"observe.*"}, [], ttl=None), max_fanout=1000)
    guarded = GuardedDelegation(root, tools={}, subagents={},
                                default_policy=lambda n: ToolPolicy(f"observe.{n}"),
                                default_subagent_authority=lambda n: Authority({"observe.*"}, [], ttl=None))
    mw = guarded.middleware()
    parent = UsageScriptedModel(responses=[
        _call("task", {"description": "look", "subagent_type": "researcher"}, "t1"),
        AIMessage(content="done"),
    ])
    sub = UsageScriptedModel(responses=[_call("peek", {"x": i}, f"p{i}") for i in range(sub_steps)] + [AIMessage(content="sub done")])
    agent = create_deep_agent(model=parent, tools=[peek], middleware=[mw],
                              subagents=[{"name": "researcher", "description": "r", "system_prompt": "r",
                                          "model": sub, "tools": [peek], "middleware": [mw]}])
    return agent, root


def test_guard_unit_aborts_when_cumulative_exceeds_budget():
    g = _BudgetGuard(1_500)

    class R:
        class G:
            class M: usage_metadata = {"input_tokens": 1_000}
            message = M()
        generations = [[G()]]
    g.on_llm_end(R())
    with pytest.raises(BudgetExceeded):
        g.on_llm_end(R())


def test_delegated_subagent_usage_is_counted_by_the_guard():
    # parent: 2 model steps (task call, done) = 2k; sub-agent: 3 tool steps + done = 4k -> total 6k
    agent, root = build_agent(parent_steps_before_done=1, sub_steps=3)
    guard = _BudgetGuard(10**9)
    agent.invoke({"messages": [("user", "go")]}, config={"recursion_limit": 60, "callbacks": [guard]})
    assert guard.used >= 6 * STEP_TOKENS, f"guard saw {guard.used} — sub-agent usage not counted"


def test_budget_between_parent_only_and_total_aborts_because_of_the_subagent():
    agent, root = build_agent(parent_steps_before_done=1, sub_steps=3)
    guard = _BudgetGuard(3 * STEP_TOKENS)         # more than the parent alone (2k), less than parent+sub (6k)
    with pytest.raises(BudgetExceeded):
        agent.invoke({"messages": [("user", "go")]}, config={"recursion_limit": 60, "callbacks": [guard]})


def test_estimate_cost_uses_public_prices():
    assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 0) == 1.0
    assert estimate_cost("claude-haiku-4-5-20251001", 0, 1_000_000) == 5.0
