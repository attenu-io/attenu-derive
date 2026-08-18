"""
Deriver v0 — task + tools + parent authority -> proposed Authority + DerivationRecord.
Layers: L1 templates (deterministic) -> L2 catalog resolver (deterministic) -> L4 fail-closed.
(L3, the constrained model proposal, is deliberately absent in v0 — design §2, PM slice T2.)
The GRANT is always parent.meet(proposal): nothing here can escalate.

    python -m attenu_derive.derive.propose data/corpus/*.jsonl      # propose for every corpus row; print layer mix + latency
"""
from __future__ import annotations

import glob
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from delegation_guard import Authority, CallLimit, EgressRank, RowLimit

from attenu_derive.catalog.coverage import load_catalog, resolve
from attenu_derive.derive import templates

__all__ = ["DelegationEvent", "DerivationRecord", "Deriver", "spec_to_authority"]

# tools_available per framework, when the corpus row does not carry it (harness configs; recorded in manifests going forward)
FRAMEWORK_TOOLS = {
    "langchain/deepagents": ["ls", "glob", "grep", "read_file", "write_file", "edit_file", "write_todos", "task"],
    "claude-agent-sdk": ["Read", "Grep", "Glob", "Write", "Agent", "SendMessage"],   # SendMessage is a built-in the CLI always exposes
}


@dataclass
class DelegationEvent:
    task: str
    role: str                                # "root" | "child"
    agent: str
    tools_available: list[str]
    parent_authority: Authority | None       # None for a root with no parent (then no meet)
    declared_subagents: list[str] = field(default_factory=list)


@dataclass
class DerivationRecord:
    layer: str                                # L1 | L2 | L4
    template: str | None
    spec: dict                                # {"scopes": [...], "ceilings": [...], "ttl": n}
    confidence: float
    evidence: dict
    latency_ms: float
    granted: dict                             # the meet result, wire form


def _ceiling(c: dict):
    t = c["type"]
    if t == "RowLimit":
        return RowLimit(int(c["max"]))
    if t == "EgressRank":
        return EgressRank(str(c["level"]))
    if t == "CallLimit":
        try:
            return CallLimit(int(c["max"]), applies_to=c.get("applies_to"))    # after shim T4 (scoped ceilings)
        except TypeError:
            return CallLimit(int(c["max"]))                                    # pre-T4 shim: unscoped
    raise ValueError(f"unknown ceiling type {t}")


def spec_to_authority(spec: dict) -> Authority:
    return Authority(set(spec["scopes"]), [_ceiling(c) for c in spec.get("ceilings", [])], ttl=spec.get("ttl"))


class Deriver:
    def __init__(self, catalog: dict | None = None):
        self.catalog = catalog or load_catalog()

    # ---- L2 -----------------------------------------------------------------------------
    HEURISTIC_MAX_TIER = 1      # heuristic (uncurated) classifications may grant tier 0-1 families only;
                                # tier-2 families (payments, mail.send, code.exec, deletes) need a curated entry

    def _l2(self, ev: DelegationEvent) -> tuple[dict, dict] | None:
        scopes: set[str] = set(); ceilings: list[dict] = []; unknown = []; consumed = set(); heuristic_used = []; withheld = []
        for t in ev.tools_available or []:
            e = resolve(self.catalog, t)
            if e is None or str(e.get("scope", "")).startswith("unknown."):
                unknown.append(t); continue
            if e.get("heuristic"):
                if int(e.get("tier", 2)) > self.HEURISTIC_MAX_TIER:
                    withheld.append((t, e["scope"])); continue           # fail closed: too risky to grant on a name heuristic
                heuristic_used.append((t, e["scope"]))
            sc = e["scope"]
            if sc == "agent.delegate":
                scopes |= {f"agent.delegate.{s}" for s in ev.declared_subagents}
            elif sc == "state.write":
                continue                                    # internal scratch, not a resource
            else:
                scopes.add(sc)
            for _arg, dim in (e.get("consumes") or {}).items():
                if dim: consumed.add(dim)
        if not scopes:
            return None
        if "max_rows" in consumed or "fs.read" in scopes:
            ceilings.append({"type": "RowLimit", "max": 1000})
        if "fs.write" in scopes:
            ceilings.append({"type": "CallLimit", "max": 5, "applies_to": "fs.write"})
        egress_needed = any(s in scopes for s in ("web.fetch", "web.search", "mail.send", "crm.export"))
        ceilings.append({"type": "EgressRank", "level": "internal" if egress_needed else "none"})
        return ({"scopes": sorted(scopes), "ceilings": ceilings, "ttl": 900},
                {"unknown_tools": unknown, "consumed": sorted(consumed), "heuristic_grants": heuristic_used, "withheld_heuristic": withheld})

    # ---- the pipeline ------------------------------------------------------------------
    def propose(self, ev: DelegationEvent) -> tuple[Authority, DerivationRecord]:
        t0 = time.perf_counter()
        m = templates.match(ev.role, ev.task, ev.tools_available, ev.declared_subagents)
        if m is not None:
            spec = {"scopes": sorted(m.scopes), "ceilings": m.ceilings, "ttl": m.ttl}
            layer, tname, conf, evidence = "L1", m.name, m.confidence, m.evidence
        else:
            l2 = self._l2(ev)
            if l2 is not None:
                spec, evidence = l2; layer, tname = "L2", None
                conf = 0.4 if evidence.get("heuristic_grants") else 0.6     # uncurated mapping -> lower confidence
            else:
                unknown = [t for t in (ev.tools_available or []) if resolve(self.catalog, t) is None or str(resolve(self.catalog, t).get("scope", "")).startswith("unknown.")]
                spec = {"scopes": [], "ceilings": [], "ttl": 300}
                layer, tname, conf, evidence = "L4", None, 0.0, {"reason": "no template and no catalog-resolvable tools; fail closed", "unknown_tools": unknown}
        proposal = spec_to_authority(spec)
        granted = ev.parent_authority.meet(proposal) if ev.parent_authority is not None else proposal
        rec = DerivationRecord(layer, tname, spec, conf, evidence, round((time.perf_counter() - t0) * 1000, 3), granted.to_wire())
        return granted, rec


def event_from_row(row: dict, task_text: str | None = None) -> DelegationEvent:
    fw = row.get("framework", "")
    tools = FRAMEWORK_TOOLS.get(fw, sorted({c["tool"] for c in row.get("child_calls", [])}))
    parent = Authority({"fs.*", "agent.delegate.*", "agent.message", "web.*", "code.exec"}, [RowLimit(1_000_000), EgressRank("any")], ttl=None)  # observe-mode root
    return DelegationEvent(task=task_text if task_text is not None else (row.get("task") or ""),
                           role="root" if row.get("parent_node") is None else "child", agent=row.get("agent", ""),
                           tools_available=tools, parent_authority=parent, declared_subagents=list(row.get("delegated_to") or ["researcher"]))


def main(argv=None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    files = [p for pat in paths for p in glob.glob(pat)] or paths
    rows = [json.loads(l) for f in files for l in Path(f).read_text().splitlines() if l.strip()]
    # task text: prefer the local mirror when present (corpus rows carry only task_hash by ADR-05)
    mirror = {}
    for f in glob.glob("data/mirror/*.jsonl"):
        for l in Path(f).read_text().splitlines():
            if l.strip():
                r = json.loads(l); mirror[(r["event_id"], r["run"].get("task_index"))] = r.get("task", "")
    d = Deriver(); layers = {}; lat = {"L1": [], "L2": [], "L4": []}; unknown_granted = 0; out = []
    for r in rows:
        ev = event_from_row(r, mirror.get((r["event_id"], r["run"].get("task_index"))))
        auth, rec = d.propose(ev)
        layers[rec.layer] = layers.get(rec.layer, 0) + 1; lat[rec.layer].append(rec.latency_ms)
        unknown_granted += sum(1 for s in auth.scopes if s.startswith("unknown."))
        out.append({"event_id": r["event_id"], "agent": r["agent"], "layer": rec.layer, "template": rec.template, "granted": rec.granted, "confidence": rec.confidence})
    n = len(rows)
    def p95(xs): xs = sorted(xs); return round(xs[int(0.95 * (len(xs) - 1))], 3) if xs else None
    summary = {"rows": n, "proposals": len(out), "layer_mix": layers,
               "l1_l2_share": round((layers.get("L1", 0) + layers.get("L2", 0)) / n, 4) if n else None,
               "unknown_scopes_granted": unknown_granted, "p95_latency_ms": {k: p95(v) for k, v in lat.items()}}
    print(json.dumps(summary, indent=2))
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/proposals-latest.jsonl").write_text("\n".join(json.dumps(o) for o in out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
