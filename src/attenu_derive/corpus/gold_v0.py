"""Generate gold labels from the local mirror. RUBRIC_VERSION=1.2 (default) applies the v1 rulings
(prompt contradiction -> negatives; agent.message admitted; CallLimit(5) on orchestrator fs.write;
researcher RowLimit(1000) role default) plus ruling 7 (v1.2, T13): a parent HOLDS the read families its
descendants used, marked `held_for_delegation`. RUBRIC_VERSION=1 / 0 reproduce the historical labels.
    python -m attenu_derive.corpus.gold_v0                    # writes corpus/gold/gold-v1.2.jsonl, prints a table
    RUBRIC_VERSION=1 python -m attenu_derive.corpus.gold_v0     # writes gold-v1.jsonl (frozen history)
Every gold row carries node / parent_node / run_key so the G1 gate can score the REAL chain.
"""
from __future__ import annotations
import glob, json, os, re, sys
from pathlib import Path
from attenu_derive.catalog.coverage import load_catalog, resolve

QUP = {"0": 0, "1": 1, "2-10": 10, "11-100": 100, "101-1k": 1000, "1k-10k": 10_000, "10k-100k": 100_000, "100k-1M": 1_000_000, "1M+": None}
_RV = os.environ.get("RUBRIC_VERSION", "1.2")
RUBRIC_VERSION = float(_RV)
GOLD = Path(__file__).with_name("gold") / f"gold-v{_RV}.jsonl"
HELD_FAMILIES = ("fs.read", "data.read")          # the tier-0 read families a parent holds for its explorers (ruling 7)
_DELEGATE_ALL_READING = re.compile(r"(use the researcher (sub-?agent )?for all reading|delegate (the|all) reading|only write the final file yourself|delegate .*reading|do not read)", re.I)


def label_row(r: dict, cat: dict) -> dict:
    env = r["observed_envelope"]; scopes = set()
    for t in env["tools"]:
        sc = (resolve(cat, t) or {}).get("scope", f"unknown.{t}")
        if sc != "agent.delegate":
            scopes.add(sc)
    for child in r.get("delegated_to", []):
        scopes.add(f"agent.delegate.{child}")
    rows_max = None
    for t in env["tools"]:
        for arg, dim in ((resolve(cat, t) or {}).get("consumes") or {}).items():
            if dim == "max_rows" and arg in env["quantities_max"]:
                up = QUP[env["quantities_max"][arg]]; rows_max = max(rows_max or 0, up or 10**9)
    reads = sum(1 for c in r["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.read")
    writes = sum(1 for c in r["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.write")
    over = []; negatives = list(r.get("negatives", []))
    root = r["parent_node"] is None
    if not root and reads > 40: over.append(f"repeated broad reads ({reads})")
    if root and writes > 5: over.append(f"write loop ({writes} writes)")
    if root and reads: over.append(f"read directly instead of delegating ({reads})")
    if RUBRIC_VERSION >= 1 and (r.get("role_constraints") or {}).get("no_write") and writes:
        # ruling 1 applied to the ROLE prompt (2026-08-18): a specialist defined "Do NOT write files" that writes anyway is
        # over-reach -> its writes are negatives, fs.write not admitted (the explorer template is right to withhold it)
        write_tools = [c["tool"] for c in r["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.write"]
        negatives += write_tools; scopes.discard("fs.write")
    if RUBRIC_VERSION >= 1:
        # ruling 1: explicit prompt contradiction -> the contradicting reads are negatives, fs.read not admitted
        if root and reads and _DELEGATE_ALL_READING.search(r.get("task") or ""):
            scopes.discard("fs.read")
            negatives += [c["tool"] for c in r["child_calls"] if (resolve(cat, c["tool"]) or {}).get("scope") == "fs.read"]
        # ruling 4: role default for sub-agent read ceilings
        if not root and rows_max:
            rows_max = 1000
    ceilings = ([{"type": "RowLimit", "max": rows_max}] if rows_max and "fs.read" in scopes else []) + \
               ([{"type": "EgressRank", "level": "none"}] if "fs.read" in scopes and not scopes & {"web.fetch", "mail.send", "crm.export"} else [])
    if RUBRIC_VERSION >= 1 and root and "fs.write" in scopes:
        ceilings.append({"type": "CallLimit", "max": 5, "applies_to": "fs.write"})   # ruling 3 (adapter meters writes into `calls`)
    # degenerate: a sub-agent that made no calls, or an orchestrator that never wrote the deliverable its task
    # asked for — the run carries no evidence about what the role NEEDS (model laziness / early stop), so it
    # must not count as over-provisioning evidence (it still counts for benign-deny).
    asks_write = bool(re.search(r"\b(write|save)\b.*\.md\b|\b[A-Z_]+\.md\b", r.get("task") or ""))
    degenerate = (not root and not r["child_calls"]) or (root and asks_write and writes == 0)
    return {"event_id": r["event_id"], "project": r["project"], "framework": r["framework"], "agent": r["agent"], "role": "orchestrator" if root else "subagent",
            "node": r.get("node"), "parent_node": r.get("parent_node"), "degenerate": degenerate,
            "tools_available": r.get("tools_available"), "subagent_tools": r.get("subagent_tools"),      # declared suites when the harness recorded them (ADK/CrewAI)
            "task": r.get("task", ""), "observed_envelope": env, "delegated_to": r.get("delegated_to", []),
            "label": {"scopes": sorted(scopes), "ceilings": ceilings, "ttl_bucket_s": 900},
            "negatives": sorted(set(negatives)), "over_exploration": over,
            "confidence": "high" if not root else "med",
            "rationale": ("subagent: read-only exploration; RowLimit from observed read limits; egress none" if not root
                          else "orchestrator: the requested write + the delegation it was asked to make"),
            "reviewer": "session-2026-08-18 + gemini-via-rafael" if RUBRIC_VERSION >= 1 else "session-2026-08-18",
            "rubric_version": RUBRIC_VERSION}


def apply_held_for_delegation(gold_rows: list[dict]) -> None:
    """Ruling 7 (v1.2): within one run/task, every ancestor's label gains the HELD_FAMILIES its descendants' labels
    contain, marked `held_for_delegation` (in place). A parent cannot delegate what it does not hold."""
    by_run: dict[str, list[dict]] = {}
    for g in gold_rows:
        by_run.setdefault(str(g.get("run_key")), []).append(g)
    for rows in by_run.values():
        by_node = {g["node"]: g for g in rows if g.get("node")}
        for g in rows:
            need = set(); stack = [c for c in rows if c.get("parent_node") == g.get("node")]; seen = set()
            while stack:                                          # all descendants
                c = stack.pop()
                if c["node"] in seen: continue
                seen.add(c["node"]); need |= set(c["label"]["scopes"]) & set(HELD_FAMILIES)
                stack += [x for x in rows if x.get("parent_node") == c.get("node")]
            g["label"]["held_for_delegation"] = sorted(need)
            g["label"]["scopes"] = sorted(set(g["label"]["scopes"]) | need)


def _truncation_index() -> dict:
    """(run_id, task_index) -> True when that task ended by error/timeout/budget (manifests)."""
    idx = {}
    for mf in glob.glob("data/runs/*/manifest.json"):
        m = json.loads(Path(mf).read_text())
        for r in m.get("results", []):
            st = str(r.get("status", ""))
            idx[(m["run_id"], r.get("task_index"))] = st.startswith("error") or bool(r.get("aborted"))
    return idx


def main(argv=None) -> int:
    cat = load_catalog(); gold = []; trunc = _truncation_index()
    for f in sorted(glob.glob("data/mirror/*.jsonl")):
        run_id = Path(f).stem.split("-")[-2] + "-" + Path(f).stem.split("-")[-1]   # <project>-<tag>-<YYYYMMDDTHHMMSS>-<hex>
        for line in Path(f).read_text().splitlines():
            if line.strip():
                r = json.loads(line); g = label_row(r, cat)
                g["truncated"] = bool(trunc.get((run_id, r.get("run", {}).get("task_index")), False))
                g["run_key"] = f"{run_id}:{r.get('run', {}).get('task_index')}"
                gold.append(g)
    if RUBRIC_VERSION >= 1.2:
        apply_held_for_delegation(gold)
    GOLD.write_text("\n".join(json.dumps(g, sort_keys=True) for g in gold) + "\n")
    print("| # | project / fw | agent | task (truncated) | observed tools · quantities | label scopes | ceilings | over-exploration |")
    print("|---|---|---|---|---|---|---|---|")
    for i, g in enumerate(gold, 1):
        fw = g["framework"].replace("langchain/", "").replace("claude-agent-sdk", "claude-sdk")
        task = (g.get("task") or "").replace("|", "/").replace("\n", " "); task = task[:55] + ("…" if len(task) > 55 else "")
        env = g["observed_envelope"]; q = env["quantities_max"]
        ceils = "; ".join(f"{c['type']}({c.get('max', c.get('level'))})" for c in g["label"]["ceilings"]) or "—"
        print(f"| {i} | {g['project']} / {fw} | {g['agent']} | {task} | {', '.join(env['tools'])}{(' · q=' + json.dumps(q)) if q else ''} | {', '.join(g['label']['scopes'])} | {ceils} | {', '.join(g['over_exploration']) or '—'} |")
    print(f"\ngold items: {len(gold)} (truncated runs: {sum(1 for g in gold if g.get('truncated'))}) -> {GOLD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
