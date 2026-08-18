"""T15 (P2 slice, 2026-08-18): the CrewAI cost guardrail must count DELEGATED (coworker) model usage — 4th
harness, same class as T1/T9 — and abort the crew when the cap is crossed. RED if a coworker's LLM calls
never reach the guard, or if crossing the cap does not stop the run."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("crewai")
os.environ.setdefault("OTEL_SDK_DISABLED", "true"); os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true"); os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai import Agent, Crew, Process, Task
from crewai.hooks import clear_all_global_hooks
from crewai.llms.base_llm import BaseLLM
from crewai.tools import tool

from delegation_guard import Authority, Guard
from delegation_guard.adapters.crewai import CrewAIGuardBridge, ToolPolicy

from attenu_derive.sample.run_crewai import BudgetExceeded, BudgetHook, estimate_cost, make_bridge

STEP = 1_000


class UsageScriptedLLM(BaseLLM):
    """Per-role ReAct script; every call reports STEP prompt tokens through CrewAI's own usage tracking."""
    script: dict = {}
    counters: dict = {}

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None, from_agent=None, response_model=None) -> str:
        role = getattr(from_agent, "role", "?"); i = self.counters.get(role, 0); self.counters[role] = i + 1
        self._track_token_usage_internal({"prompt_tokens": STEP, "completion_tokens": 10, "total_tokens": STEP + 10})
        steps = self.script.get(role, [])
        return steps[i] if i < len(steps) else "Thought: I am done.\nFinal Answer: done"


def _act(tool_name: str, payload: str) -> str:
    return f"Thought: next step.\nAction: {tool_name}\nAction Input: {payload}"


def _crew(llm, calls: list):
    @tool("read_file")
    def read_file(path: str) -> str:
        """Read a file."""
        calls.append(("read_file", path)); return "content"
    orch = Agent(role="orchestrator", goal="Produce a report by delegating.", backstory="Runs the show.", llm=llm, tools=[], allow_delegation=True, verbose=False, max_iter=6)
    res = Agent(role="researcher", goal="Explore the repository.", backstory="Reads files.", llm=llm, tools=[read_file], allow_delegation=False, verbose=False, max_iter=6)
    task = Task(description="Produce REPORT.md by delegating the reading to the researcher.", expected_output="A report.", agent=orch)
    return Crew(agents=[orch, res], tasks=[task], process=Process.sequential, telemetry=False)


def _run(max_input_tokens: int):
    clear_all_global_hooks(); calls: list = []
    llm = UsageScriptedLLM(model="scripted/offline", script={
        "orchestrator": [_act("Delegate work to coworker", '{"task": "explore the repo", "context": "c", "coworker": "researcher"}'), "Thought: done.\nFinal Answer: report written."],
        "researcher": [_act("read_file", '{"path": "README.md"}'), "Thought: done.\nFinal Answer: found."],
    }, counters={})
    root, bridge = make_bridge(salt="s")
    budget = BudgetHook(max_input_tokens); err = None
    try:
        with bridge, budget:
            _crew(llm, calls).kickoff()
    except Exception as exc:              # noqa: BLE001
        err = exc
    finally:
        clear_all_global_hooks()
    return budget, root, calls, err


def test_delegated_usage_is_counted():
    budget, root, calls, err = _run(max_input_tokens=100 * STEP)
    assert err is None, err
    assert ("read_file", "README.md") in calls
    assert budget.used >= 4 * STEP, budget.used                        # orchestrator 2 calls + researcher 2 calls at least
    assert budget.by_agent.get("researcher", 0) >= 2 * STEP             # the DELEGATED share is what T1 found missing


def test_budget_aborts_mid_run_including_the_coworker():
    budget, root, calls, err = _run(max_input_tokens=int(1.5 * STEP))
    assert budget.aborted and budget.used > 1.5 * STEP
    assert budget.used <= 3 * STEP, budget.used                          # stopped at the 2nd call, not after the crew finished
    assert root.audit_log().entries                                     # audit log complete up to the abort


def test_estimate_cost_haiku():
    assert estimate_cost("anthropic/claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.0)
