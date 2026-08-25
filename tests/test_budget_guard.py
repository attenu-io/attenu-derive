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

from attenu_guard import Authority, Guard
from attenu_guard.adapters.langchain import GuardedDelegation, ToolPolicy

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


# ---- G3 volume run (the go decision, 2026-08-18): honest cost + a hard USD ceiling per batch ------------------
def test_estimate_cost_is_cache_aware():
    from attenu_derive.sample.run_deepagents import estimate_cost, usage_from_callback
    # Haiku 4.5 list: input 1.00 / output 5.00 / cache read 0.10 / cache write 1.25 (USD per 1M)
    assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.0)                       # no cache info: all input at list
    assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 0, cache_read=900_000) == pytest.approx(0.1 + 0.09)   # 100k fresh + 900k cached
    assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 200_000, cache_read=500_000, cache_creation=100_000) == \
        pytest.approx(0.4 * 1.0 + 0.5 * 0.10 + 0.1 * 1.25 + 0.2 * 5.0)
    u = usage_from_callback({"claude-haiku-4-5-20251001": {"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010,
                                                           "input_token_details": {"cache_read": 700, "cache_creation": 100}}}, "claude-haiku-4-5-20251001")
    assert u["input_tokens"] == 1000 and u["cache_read_tokens"] == 700 and u["cache_creation_tokens"] == 100
    assert u["est_cost_usd"] == pytest.approx(round(200 / 1e6 * 1.0 + 700 / 1e6 * 0.10 + 100 / 1e6 * 1.25 + 10 / 1e6 * 5.0, 6), abs=1e-6)
    assert u["est_cost_usd_list"] == pytest.approx(round(1000 / 1e6 * 1.0 + 10 / 1e6 * 5.0, 6), abs=1e-6)          # what we used to report (upper bound)


def test_batch_usd_ceiling_stops_the_batch():
    from attenu_derive.sample.run_deepagents import batch_should_stop
    assert batch_should_stop(spent_usd=0.49, max_usd=0.50, next_task_worst_case_usd=0.30) is True    # would breach on the next task
    assert batch_should_stop(spent_usd=0.10, max_usd=0.50, next_task_worst_case_usd=0.30) is False
    assert batch_should_stop(spent_usd=0.10, max_usd=None, next_task_worst_case_usd=0.30) is False   # no ceiling set
