"""
G1 eval — score the Deriver against gold labels with a PROJECT-level hold-out (design §5a; ADR-04).

    python -m attenu_derive.eval.g1 --holdout express          # metrics for train (other projects) and hold-out
    python -m attenu_derive.eval.g1 --holdout express --check  # exit 1 if the hold-out regresses past eval/thresholds.json

Runs from the COMMITTED gold file (works in CI without the corpus): each gold row carries the task
text, role, framework, delegated_to, observed_envelope (tools + quantity maxima) and negatives.
Metrics (hold-out only counts for the Gate):
  benign_deny_rate   share of benign observed (tool, max quantity) uses the proposal would DENY
                     — benign = in the observed envelope and not a gold negative (existential metric)
  unused_scope_share mean over rows of granted scopes never used by benign calls (over-provisioning)
  over_provision     rows where the proposal grants a scope the gold label does not
  escalation_count   proposals wider than the parent (must be 0 by construction — meet)
  layer_mix          how many rows resolved at L1/L2/L4

Parent mode (T13 item 5, 2026-08-18): the GATE scores on the REAL chain — the root is derived against the
observe parent, every child against its PARENT'S DERIVED authority (`meet`), exactly as enforce mode and
shadow do. `parent="synthetic"` (every row against a wide parent) is kept as per-template unit scoring and
reported alongside for one transition: it read 0.0034 benign-deny while the chain sat at 0.83.
Transitive use (rubric v1.2): a scope a node holds to pass down and that a descendant actually uses counts
as USED by the node; a scope no one in the subtree uses stays UNUSED (pinned by tests — a metric
correction, not metric laundering). Denials carry their cause: `proposal` | `parent-chain`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from delegation_guard import Authority, EgressRank, RowLimit

from attenu_derive.catalog.coverage import load_catalog, resolve
from attenu_derive.derive.propose import Deriver, DelegationEvent, FRAMEWORK_TOOLS, spec_to_authority, subagent_tools_for, tools_for

GOLD = Path(__file__).resolve().parents[1] / "corpus" / "gold" / "gold-v1.2.jsonl"
THRESHOLDS = Path(__file__).with_name("thresholds.json")
QUP = {"0": 0, "1": 1, "2-10": 10, "11-100": 100, "101-1k": 1000, "1k-10k": 10_000, "10k-100k": 100_000, "100k-1M": 1_000_000, "1M+": 10_000_000}
OBSERVE_PARENT = Authority({"fs.*", "data.*", "compute.pure", "device.actuate", "agent.delegate.*", "agent.message", "web.*", "code.exec"}, [RowLimit(1_000_000), EgressRank("any")], ttl=None)   # what a real observe root held (observe.*): every family the workload used


def _ctx_for(tool: str, entry: dict, qmax: dict) -> dict:
    """Context bag for a benign use of `tool` at the observed maximum quantities (bucket upper bound)."""
    ctx = {}
    for arg, dim in (entry.get("consumes") or {}).items():
        if dim == "max_rows" and arg in qmax:
            ctx["rows"] = max(ctx.get("rows", 0), QUP[qmax[arg]])
    return ctx


def _run_key(g: dict) -> str:
    return str(g.get("run_key") or g.get("event_id") or "")


def score(rows: list[dict], deriver: Deriver, cat: dict, parent: str = "chain") -> dict:
    """parent="chain" (the Gate): children derived against their parent's DERIVED authority; "synthetic": every row
    against OBSERVE_PARENT (per-template unit scoring). Rows are processed in file order (spawn order)."""
    if parent not in ("chain", "synthetic"):
        raise ValueError("parent must be 'chain' or 'synthetic'")
    n = 0; benign_total = 0; benign_denied = 0; unused_shares = []; over_prov = 0; escalations = 0
    layers = Counter(); per_row = []; derived: dict[tuple, Authority] = {}
    used_by_node: dict[tuple, set] = {}; node_key: dict[int, tuple] = {}; parent_of: dict[tuple, tuple] = {}
    scored = []
    for idx, g in enumerate(rows):
        role = "root" if g.get("role") == "orchestrator" or g.get("agent") == "orchestrator" else "child"
        tools_available = list(g["tools_available"]) if g.get("tools_available") else tools_for(g["framework"], g["agent"], g["observed_envelope"]["tools"])
        sub_tools = subagent_tools_for(g["framework"], list(g.get("delegated_to") or []), g.get("subagent_tools"))
        key = (_run_key(g), g.get("node") or g["event_id"]); pkey = (_run_key(g), g.get("parent_node")) if g.get("parent_node") else None
        node_key[idx] = key
        if parent == "chain" and pkey is not None and pkey in derived:
            parent_auth = derived[pkey]; parent_of[key] = pkey
        else:
            parent_auth = OBSERVE_PARENT
        ev = DelegationEvent(task=g.get("task") or "", role=role, agent=g["agent"], tools_available=tools_available,
                             parent_authority=parent_auth, declared_subagents=list(g.get("delegated_to") or []), subagent_tools=sub_tools)
        granted, rec = deriver.propose(ev)
        derived[key] = granted; layers[rec.layer] += 1; n += 1
        if not granted.is_narrower_than(parent_auth):
            escalations += 1
        proposal = spec_to_authority(rec.spec)
        env = g["observed_envelope"]; negatives = set(g.get("negatives") or [])
        used_scopes = set(); denied_here = []
        for t in env["tools"]:
            if t in negatives:
                continue                                    # not benign by the label
            e = resolve(cat, t) or {}
            sc = e.get("scope", f"unknown.{t}")
            if sc == "agent.delegate":
                sc = f"agent.delegate.{(g.get('delegated_to') or ['researcher'])[0]}"
            if sc == "state.write":
                continue
            benign_total += 1
            ctx = _ctx_for(t, e, env.get("quantities_max") or {})
            d = granted.permits(sc, ctx)
            if not d:
                cause = "parent-chain" if proposal.permits(sc, ctx) else "proposal"
                benign_denied += 1; denied_here.append((t, sc, d.reasons[0].code if d.reasons else "?", cause))
            used_scopes.add(sc)
        for child in (g.get("delegated_to") or []):            # a spawn IS a use of agent.delegate.<child> (deepagents records it as spawn, not a call)
            used_scopes.add(f"agent.delegate.{child}")
        used_by_node[key] = used_scopes
        scored.append((idx, g, key, granted, rec, used_scopes, denied_here))
    # transitive use: a descendant's use counts as the ancestor's use (a scope held to pass down and actually passed down)
    down_use: dict[tuple, set] = defaultdict(set)
    for key, used in used_by_node.items():
        p = parent_of.get(key); seen = set()
        while p is not None and p not in seen:
            down_use[p] |= used; seen.add(p); p = parent_of.get(p)
    held_total = 0; held_used_total = 0
    for idx, g, key, granted, rec, used_scopes, denied_here in scored:
        gscopes = set(granted.scopes)
        eff_used = used_scopes | down_use.get(key, set())
        unused = {s for s in gscopes if not any(_covers(s, u) for u in eff_used)}
        gold_scopes = set(g["label"]["scopes"])
        held = sorted(rec.spec.get("held_for_delegation") or [])
        held_used = sorted(h for h in held if any(_covers(h, u) for u in down_use.get(key, set())))
        held_total += len(held); held_used_total += len(held_used)
        if not g.get("truncated") and not g.get("degenerate"):   # cut-short or do-nothing runs under-represent need: never evidence of over-provisioning
            unused_shares.append(len(unused) / len(gscopes) if gscopes else 0.0)
            if any(not any(_covers(ls, s) for ls in gold_scopes) for s in gscopes):
                over_prov += 1
        per_row.append({"event_id": g["event_id"], "project": g["project"], "agent": g["agent"], "layer": rec.layer,
                        "template": rec.template, "granted": sorted(gscopes), "gold": sorted(gold_scopes),
                        "denied_benign": denied_here, "unused": sorted(unused),
                        "held_for_delegation": held, "held_used_downstream": held_used})
    n_trunc = sum(1 for g in rows if g.get("truncated")); n_degen = sum(1 for g in rows if g.get("degenerate"))
    n_clean = sum(1 for g in rows if not g.get("truncated") and not g.get("degenerate"))   # the over-provision evidence base
    return {"parent_mode": parent, "rows": n, "rows_truncated": n_trunc, "rows_degenerate": n_degen, "rows_clean": n_clean, "benign_uses": benign_total,
            "benign_deny_rate": round(benign_denied / benign_total, 4) if benign_total else None,
            "benign_denied_by_cause": dict(Counter(x[3] for r in per_row for x in r["denied_benign"])),
            "unused_scope_share": round(sum(unused_shares) / len(unused_shares), 4) if unused_shares else None,
            "over_provision_rows": over_prov, "escalation_count": escalations, "layer_mix": dict(layers),
            "held_for_delegation_total": held_total, "held_used_downstream_total": held_used_total, "per_row": per_row}


def _covers(held: str, req: str) -> bool:
    return held == req or (held.endswith(".*") and req.startswith(held[:-1]))


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "per_row"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--holdout", default="express"); ap.add_argument("--gold", default=str(GOLD))
    ap.add_argument("--out", default="data/reports"); ap.add_argument("--check", action="store_true", help="fail on regression vs thresholds.json (hold-out, CHAIN scoring)")
    args = ap.parse_args(argv)
    gold = [json.loads(l) for l in Path(args.gold).read_text().splitlines() if l.strip()]
    train = [g for g in gold if g["project"] != args.holdout]; hold = [g for g in gold if g["project"] == args.holdout]
    d = Deriver(); cat = load_catalog()
    res = {"holdout_project": args.holdout,
           "train": score(train, d, cat, parent="chain"), "holdout": score(hold, d, cat, parent="chain"),                       # the GATE: real chain
           "train_synthetic": score(train, d, cat, parent="synthetic"), "holdout_synthetic": score(hold, d, cat, parent="synthetic")}   # per-template unit scoring (transition)
    card = {"date": time.strftime("%Y-%m-%d"), "gold": Path(args.gold).name, "rows_train": len(train), "rows_holdout": len(hold),
            "holdout_project": args.holdout, "gate_parent_mode": "chain",
            **{k: _strip(v) for k, v in res.items() if k != "holdout_project"}}
    print(json.dumps(card, indent=2))
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / f"eval-card-{time.strftime('%Y%m%d')}.json").write_text(json.dumps({**card, "per_row_holdout": res["holdout"]["per_row"], "per_row_train": res["train"]["per_row"]}, indent=2))
    md = [f"# Eval card — {card['date']} (gold: {card['gold']}, hold-out: {args.holdout}; GATE = chain scoring)", "",
          "| split | parent | rows | clean | benign uses | benign_deny_rate | by cause | unused_scope_share | over_provision_rows | escalation_count | held / used downstream | layers |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for split in ("train", "holdout", "train_synthetic", "holdout_synthetic"):
        x = card[split]
        md.append(f"| {split.replace('_synthetic', '')} | {x['parent_mode']} | {x['rows']} | {x['rows_clean']} | {x['benign_uses']} | {x['benign_deny_rate']} | {x['benign_denied_by_cause']} | "
                  f"{x['unused_scope_share']} | {x['over_provision_rows']} | {x['escalation_count']} | {x['held_for_delegation_total']} / {x['held_used_downstream_total']} | {x['layer_mix']} |")
    (out / f"eval-card-{time.strftime('%Y%m%d')}.md").write_text("\n".join(md) + "\n")
    if args.check and THRESHOLDS.exists():
        th = json.loads(THRESHOLDS.read_text()); h = card["holdout"]; bad = []
        if h["escalation_count"] != 0: bad.append("escalation_count != 0")
        if h["benign_deny_rate"] is not None and h["benign_deny_rate"] > th["benign_deny_rate_max"]: bad.append(f"benign_deny_rate {h['benign_deny_rate']} > {th['benign_deny_rate_max']}")
        if h["unused_scope_share"] is not None and h["unused_scope_share"] > th["unused_scope_share_max"]: bad.append(f"unused_scope_share {h['unused_scope_share']} > {th['unused_scope_share_max']}")
        if bad:
            print("REGRESSION vs thresholds (chain scoring):", bad, file=sys.stderr); return 1
        print("thresholds OK (chain scoring)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
