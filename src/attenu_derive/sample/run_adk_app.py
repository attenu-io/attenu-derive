"""
attenu-sample — run a REAL Google ADK application (any package exposing `root_agent`, e.g. google/adk-samples)
in observe mode: every delegation and tool call of the app's OWN agents and tools is recorded through the
attenu-guard audit log (redacted at capture, ADR-05) and exported as corpus rows, so shadow mode can then
say what Attenu would have blocked on that real workload. This is G2's "real projects" done on the project's
own agents, tools and prompts — not on a workload shape we invented.

    python -m attenu_derive.sample.run_adk_app --app <dir>/customer_service --prompts <file> \
        --model anthropic/claude-haiku-4-5-20251001 --project adk-customer-service --limit 8

Model override: the samples are Gemini-native. `--model gemini-*` keeps them on Gemini; anything else is routed
through ADK's LiteLlm wrapper (e.g. `anthropic/...`) for EVERY LlmAgent in the tree, and Gemini-only built-in
tools (`google_search`) are replaced by a stub FunctionTool that records the call and returns nothing — the
manifest lists what was stubbed. Declared tool suites are recorded from the real tree (`tools_available`,
`subagent_tools`), which is what the deriver needs at issuance time.

Guardrails: BudgetPlugin (after_model_callback — fires on EVERY model call in ADK, incl. sub-agents; proven by
tests/test_budget_guard_adk.py), RunConfig.max_llm_calls, per-prompt wall-clock timeout, batch USD ceiling,
incremental export, cache-aware est_cost_usd with price_basis.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import platform
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from attenu_guard import Authority, Guard, __version__ as DG_VERSION

from attenu_derive import __version__ as AD_VERSION
from attenu_derive.corpus.export import _task_features, audit_to_corpus_rows
from attenu_derive.sample.features import extract_features
from attenu_derive.sample.pricing import estimate_cost, price_basis
from attenu_derive.sample.run_adk import BudgetExceeded, BudgetPlugin

OBSERVE = Authority({"observe.*", "agent.delegate.*"}, [], ttl=None)


def load_root_agent(app_dir: Path):
    """Import `<package>.agent:root_agent` from an ADK app directory (the package's parent goes on sys.path)."""
    app_dir = app_dir.resolve(); pkg = app_dir.name
    if str(app_dir.parent) not in sys.path:
        sys.path.insert(0, str(app_dir.parent))
    mod = importlib.import_module(f"{pkg}.agent")
    return getattr(mod, "root_agent")


def _tool_name(t: Any) -> str:
    return getattr(t, "name", None) or getattr(t, "__name__", None) or type(t).__name__


def walk_agents(root) -> list:
    """Every agent reachable from root: sub_agents (transfer / workflow agents) and AgentTool-wrapped agents."""
    from google.adk.tools.agent_tool import AgentTool
    seen, out, stack = set(), [], [root]
    while stack:
        a = stack.pop()
        if id(a) in seen: continue
        seen.add(id(a)); out.append(a)
        stack += list(getattr(a, "sub_agents", None) or [])
        for t in (getattr(a, "tools", None) or []):
            if isinstance(t, AgentTool): stack.append(t.agent)
    return out


def declared_suites(root) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """({agent: [tool names]}, {agent: {child: [tool names]}}) — AgentTools appear under the parent as the child's name."""
    from google.adk.tools.agent_tool import AgentTool
    tools, subs = {}, {}
    for a in walk_agents(root):
        names = [t.agent.name if isinstance(t, AgentTool) else _tool_name(t) for t in (getattr(a, "tools", None) or [])]
        tools[a.name] = names
        children = [t.agent for t in (getattr(a, "tools", None) or []) if isinstance(t, AgentTool)] + list(getattr(a, "sub_agents", None) or [])
        if children:
            subs[a.name] = {c.name: [t.agent.name if isinstance(t, AgentTool) else _tool_name(t) for t in (getattr(c, "tools", None) or [])] for c in children}
    return tools, subs


def override_models(root, model: str) -> dict:
    """Point every LlmAgent at `model`; stub Gemini-only built-in tools when the model is not Gemini. Returns what changed."""
    from google.adk.agents.llm_agent import LlmAgent
    changed = {"agents": [], "stubbed_tools": []}
    gemini = model.startswith("gemini")
    llm = None
    if not gemini:
        from google.adk.models.lite_llm import LiteLlm
        llm = LiteLlm(model=model)
    changed["sampling_fixed"] = []
    for a in walk_agents(root):
        if isinstance(a, LlmAgent):
            a.model = model if gemini else llm; changed["agents"].append(a.name)
            if not gemini:
                # Anthropic rejects `temperature` AND `top_p` together on current models (Gemini-native apps often set
                # both); keep temperature, drop top_p (and top_k) so the app runs unchanged otherwise.
                gcc = getattr(a, "generate_content_config", None)
                if gcc is not None and (getattr(gcc, "top_p", None) is not None or getattr(gcc, "top_k", None) is not None):
                    try:
                        gcc.top_p = None; gcc.top_k = None; changed["sampling_fixed"].append(a.name)
                    except Exception:  # noqa: BLE001 - frozen config objects: leave as is
                        pass
                new_tools = []
                for t in (a.tools or []):
                    tn = type(t).__name__
                    if tn in ("GoogleSearchTool", "BuiltInCodeExecutionTool", "VertexAiSearchTool", "UrlContextTool", "EnterpriseWebSearchTool"):
                        def google_search(query: str) -> dict:
                            """Web search (STUBBED during sampling: returns no results)."""
                            return {"results": [], "note": "google_search stubbed by attenu-sample (Gemini-only tool)"}
                        new_tools.append(google_search); changed["stubbed_tools"].append(f"{a.name}:{tn}")
                    else:
                        new_tools.append(t)
                a.tools = new_tools
    return changed


def make_plugin(salt: str, root_name: str):
    from attenu_guard.adapters.google_adk import DelegationGuardPlugin, ToolAuthority
    root = Guard.issue(root_name, OBSERVE, task="sample", max_depth=8, max_fanout=10_000)
    plugin = DelegationGuardPlugin(root, root_agent_name=root_name, delegations={}, tools={},
                                   default_tool_authority=lambda name: ToolAuthority(f"observe.{name}", lambda a: extract_features(a, salt=salt)),
                                   default_delegation=lambda name: OBSERVE)
    return root, plugin


async def run_prompt(root_agent, prompt: str, *, salt: str, model: str, max_input_tokens: int, max_llm_calls: int, timeout_s: float, app_name: str):
    from google.adk.agents.run_config import RunConfig
    from google.adk.apps.app import App
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.genai import types
    root, dg = make_plugin(salt, root_agent.name); budget = BudgetPlugin(max_input_tokens)
    app = App(name=app_name, root_agent=root_agent, plugins=[dg, budget])
    sessions = InMemorySessionService(); runner = Runner(app=app, session_service=sessions)
    session = await sessions.create_session(app_name=app_name, user_id="attenu")
    status = "ok"; final_text = ""

    async def _consume():
        nonlocal final_text
        async for ev in runner.run_async(user_id="attenu", session_id=session.id, run_config=RunConfig(max_llm_calls=max_llm_calls),
                                         new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)])):
            for part in (getattr(getattr(ev, "content", None), "parts", None) or []):
                if getattr(part, "text", None) and getattr(ev, "is_final_response", lambda: False)():
                    final_text = part.text[:200]
    try:
        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        status = f"error: Timeout: prompt exceeded {timeout_s:.0f}s wall clock"
    except Exception as exc:                                        # noqa: BLE001 — keep sampling; record the failure
        cause = exc.__cause__ if isinstance(exc.__cause__, BudgetExceeded) else exc
        status = f"error: {type(cause).__name__}: {str(cause)[:120]}"
    usage = {"input_tokens": budget.used, "output_tokens": budget.output, "cached_input_tokens": budget.cached, "llm_calls": budget.calls,
             "by_agent": dict(budget.by_agent), "est_cost_usd": round(estimate_cost(model, budget.used, budget.output, cache_read=budget.cached), 6),
             "est_cost_usd_list": round(estimate_cost(model, budget.used, budget.output), 6)}
    return root, dg, status, usage, budget.aborted, final_text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="path to the ADK app PACKAGE dir (contains agent.py with root_agent)")
    ap.add_argument("--prompts", required=True, help="file with one user request per line (the app's own kind of workload)")
    ap.add_argument("--project", default=None); ap.add_argument("--out", default="data")
    ap.add_argument("--model", default="anthropic/claude-haiku-4-5-20251001", help="gemini-* keeps Gemini; anything else goes through LiteLlm")
    ap.add_argument("--limit", type=int, default=None); ap.add_argument("--max-input-tokens", type=int, default=100_000)
    ap.add_argument("--max-llm-calls", type=int, default=30); ap.add_argument("--timeout-s", type=float, default=300.0)
    ap.add_argument("--max-usd", type=float, default=None, help="HARD USD ceiling for the whole run (cache-aware estimate)")
    args = ap.parse_args(argv)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    if not args.model.startswith("gemini") and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set", file=sys.stderr); return 2

    app_dir = Path(args.app); project = args.project or f"adk-{app_dir.name.replace('_', '-')}"
    root_agent = load_root_agent(app_dir)
    changed = override_models(root_agent, args.model)
    tools_by_agent, subs_by_agent = declared_suites(root_agent)
    prompts = [l.strip() for l in Path(args.prompts).read_text().splitlines() if l.strip() and not l.startswith("#")]
    if args.limit: prompts = prompts[: args.limit]
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3); salt = secrets.token_hex(16)
    out = Path(args.out)
    for d in ("corpus", "mirror", f"runs/{run_id}"): (out / d).mkdir(parents=True, exist_ok=True)
    run_meta = {"project": project, "framework": "google-adk", "app": str(app_dir.name), "model": args.model, "seed": 0, "salt": salt,
                "versions": {"attenu-derive": AD_VERSION, "attenu-guard": DG_VERSION, "python": platform.python_version()}}
    corpus_rows, mirror_rows, per_task = [], [], []; stopped_by = None
    worst = estimate_cost(args.model, args.max_input_tokens, 4_000)
    for i, prompt in enumerate(prompts):
        spent = sum(p["usage"]["est_cost_usd"] for p in per_task)
        if args.max_usd is not None and spent + worst > args.max_usd:
            stopped_by = f"max_usd: spent {spent:.3f} + worst {worst:.3f} > {args.max_usd}"; print(f"[batch] stop: {stopped_by}"); break
        t0 = time.time()
        root, dg, status, usage, aborted, final = asyncio.run(run_prompt(root_agent, prompt, salt=salt, model=args.model, max_input_tokens=args.max_input_tokens,
                                                                          max_llm_calls=args.max_llm_calls, timeout_s=args.timeout_s, app_name=f"attenu-{project}"))
        entries = root.audit_log().entries
        (out / "runs" / run_id / f"task{i}-audit.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")
        run_i = dict(run_meta, task_index=i)
        rows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="hash"); mrows = audit_to_corpus_rows(entries, run=run_i, task_text_mode="keep")
        for r, mr in zip(rows, mrows):
            r["tools_available"] = mr["tools_available"] = list(tools_by_agent.get(r["agent"], []))          # DECLARED suite from the real tree
            if r["agent"] in subs_by_agent:
                r["subagent_tools"] = mr["subagent_tools"] = {k: list(v) for k, v in subs_by_agent[r["agent"]].items()}
                r["declared_subagents"] = mr["declared_subagents"] = sorted(subs_by_agent[r["agent"]])        # T21: declared roster from the real tree
            elif r["parent_node"] is None:
                r["declared_subagents"] = mr["declared_subagents"] = []
            if r["parent_node"] is None:
                r["task_hash"] = hashlib.sha256(f"{salt}\x1f{prompt}".encode()).hexdigest()[:16]; r["task_features"] = _task_features(prompt); mr["task"] = prompt
        corpus_rows += rows; mirror_rows += mrows
        (out / "corpus" / f"{project}-adk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in corpus_rows) + "\n")
        (out / "mirror" / f"{project}-adk-{run_id}.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror_rows) + "\n")
        n_calls = sum(len(r["child_calls"]) for r in rows); n_deleg = sum(1 for r in rows if r["parent_node"] is not None)
        per_task.append({"task_index": i, "status": status, "seconds": round(time.time() - t0, 1), "aborted": aborted, "delegations": n_deleg,
                         "tool_calls": n_calls, "usage": usage, "audit_events": len(entries), "final_text": final})
        print(f"[prompt {i}] {status} | delegations={n_deleg} tool_calls={n_calls} tokens={usage['input_tokens']}+{usage['output_tokens']} "
              f"llm_calls={usage['llm_calls']} est=${usage['est_cost_usd']} in {per_task[-1]['seconds']}s", flush=True)
    manifest = {"run_id": run_id, **{k: v for k, v in run_meta.items() if k != "salt"}, "billing": "api",
                "price_basis": price_basis(args.model), "model_override": changed, "declared_tools": tools_by_agent, "declared_subagent_tools": subs_by_agent,
                "guardrails": {"max_input_tokens": args.max_input_tokens, "max_llm_calls": args.max_llm_calls, "timeout_s": args.timeout_s, "max_usd": args.max_usd},
                "stopped_by": stopped_by, "tasks": len(prompts), "tasks_run": len(per_task), "task_texts": prompts, "results": per_task,
                "totals": {"delegation_events": sum(1 for r in corpus_rows if r["parent_node"] is not None), "rows": len(corpus_rows),
                           "tool_calls": sum(len(r["child_calls"]) for r in corpus_rows),
                           "input_tokens": sum(p["usage"]["input_tokens"] for p in per_task), "output_tokens": sum(p["usage"]["output_tokens"] for p in per_task),
                           "est_cost_usd": round(sum(p["usage"]["est_cost_usd"] for p in per_task), 4),
                           "est_cost_usd_list": round(sum(p["usage"]["est_cost_usd_list"] for p in per_task), 4)}}
    (out / "runs" / run_id / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
