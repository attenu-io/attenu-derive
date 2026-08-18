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
from attenu_derive.derive.propose import Deriver, DelegationEvent, FRAMEWORK_TOOLS

GOLD = Path(__file__).resolve().parents[1] / "corpus" / "gold" / "gold-v1.jsonl"
THRESHOLDS = Path(__file__).with_name("thresholds.json")
QUP = {"0": 0, "1": 1, "2-10": 10, "11-100": 100, "101-1k": 1000, "1k-10k": 10_000, "10k-100k": 100_000, "100k-1M": 1_000_000, "1M+": 10_000_000}
OBSERVE_PARENT = Authority({"fs.*", "agent.delegate.*", "agent.message", "web.*", "code.exec"}, [RowLimit(1_000_000), EgressRank("any")], ttl=None)


def _ctx_for(tool: str, entry: dict, qmax: dict) -> dict:
    """Context bag for a benign use of `tool` at the observed maximum quantities (bucket upper bound)."""
    ctx = {}
    for arg, dim in (entry.get("consumes") or {}).items():
        if dim == "max_rows" and arg in qmax:
            ctx["rows"] = max(ctx.get("rows", 0), QUP[qmax[arg]])
    return ctx


def score(rows: list[dict], deriver: Deriver, cat: dict) -> dict:
    n = 0; benign_total = 0; benign_denied = 0; unused_shares = []; over_prov = 0; escalations = 0
    layers = Counter(); per_row = []
    for g in rows:
        role = "root" if g.get("role") == "orchestrator" or g.get("agent") == "orchestrator" else "child"
        tools_available = FRAMEWORK_TOOLS.get(g["framework"], g["observed_envelope"]["tools"])
        ev = DelegationEvent(task=g.get("task") or "", role=role, agent=g["agent"], tools_available=tools_available,
                             parent_authority=OBSERVE_PARENT, declared_subagents=list(g.get("delegated_to") or []))
        granted, rec = deriver.propose(ev)
        layers[rec.layer] += 1; n += 1
        if not granted.is_narrower_than(OBSERVE_PARENT):
            escalations += 1
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
            d = granted.permits(sc, _ctx_for(t, e, env.get("quantities_max") or {}))
            if not d:
                benign_denied += 1; denied_here.append((t, sc, d.reasons[0].code if d.reasons else "?"))
            used_scopes.add(sc)
        for child in (g.get("delegated_to") or []):            # a spawn IS a use of agent.delegate.<child> (deepagents records it as spawn, not a call)
            used_scopes.add(f"agent.delegate.{child}")
        gscopes = set(granted.scopes)
        unused = {s for s in gscopes if not any(_covers(s, u) for u in used_scopes)}
        unused_shares.append(len(unused) / len(gscopes) if gscopes else 0.0)
        gold_scopes = set(g["label"]["scopes"])
        if any(not any(_covers(ls, s) for ls in gold_scopes) for s in gscopes):
            over_prov += 1
        per_row.append({"event_id": g["event_id"], "project": g["project"], "agent": g["agent"], "layer": rec.layer,
                        "template": rec.template, "granted": sorted(gscopes), "gold": sorted(gold_scopes),
                        "denied_benign": denied_here, "unused": sorted(unused)})
    return {"rows": n, "benign_uses": benign_total,
            "benign_deny_rate": round(benign_denied / benign_total, 4) if benign_total else None,
            "unused_scope_share": round(sum(unused_shares) / len(unused_shares), 4) if unused_shares else None,
            "over_provision_rows": over_prov, "escalation_count": escalations, "layer_mix": dict(layers), "per_row": per_row}


def _covers(held: str, req: str) -> bool:
    return held == req or (held.endswith(".*") and req.startswith(held[:-1]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--holdout", default="express"); ap.add_argument("--gold", default=str(GOLD))
    ap.add_argument("--out", default="data/reports"); ap.add_argument("--check", action="store_true", help="fail on regression vs thresholds.json (hold-out)")
    args = ap.parse_args(argv)
    gold = [json.loads(l) for l in Path(args.gold).read_text().splitlines() if l.strip()]
    train = [g for g in gold if g["project"] != args.holdout]; hold = [g for g in gold if g["project"] == args.holdout]
    d = Deriver(); cat = load_catalog()
    res = {"holdout_project": args.holdout, "train": score(train, d, cat), "holdout": score(hold, d, cat)}
    card = {"date": time.strftime("%Y-%m-%d"), "gold": Path(args.gold).name, "rows_train": len(train), "rows_holdout": len(hold),
            "holdout_project": args.holdout,
            "train": {k: v for k, v in res["train"].items() if k != "per_row"},
            "holdout": {k: v for k, v in res["holdout"].items() if k != "per_row"}}
    print(json.dumps(card, indent=2))
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / f"eval-card-{time.strftime('%Y%m%d')}.json").write_text(json.dumps({**card, "per_row_holdout": res["holdout"]["per_row"], "per_row_train": res["train"]["per_row"]}, indent=2))
    md = [f"# Eval card — {card['date']} (gold: {card['gold']}, hold-out: {args.holdout})", "",
          "| split | rows | benign uses | benign_deny_rate | unused_scope_share | over_provision_rows | escalation_count | layers |", "|---|---|---|---|---|---|---|---|"]
    for split in ("train", "holdout"):
        s = card[split]; md.append(f"| {split} | {s['rows']} | {s['benign_uses']} | {s['benign_deny_rate']} | {s['unused_scope_share']} | {s['over_provision_rows']} | {s['escalation_count']} | {s['layer_mix']} |")
    (out / f"eval-card-{time.strftime('%Y%m%d')}.md").write_text("\n".join(md) + "\n")
    if args.check and THRESHOLDS.exists():
        th = json.loads(THRESHOLDS.read_text()); h = card["holdout"]; bad = []
        if h["escalation_count"] != 0: bad.append("escalation_count != 0")
        if h["benign_deny_rate"] is not None and h["benign_deny_rate"] > th["benign_deny_rate_max"]: bad.append(f"benign_deny_rate {h['benign_deny_rate']} > {th['benign_deny_rate_max']}")
        if h["unused_scope_share"] is not None and h["unused_scope_share"] > th["unused_scope_share_max"]: bad.append(f"unused_scope_share {h['unused_scope_share']} > {th['unused_scope_share_max']}")
        if bad:
            print("REGRESSION vs thresholds:", bad, file=sys.stderr); return 1
        print("thresholds OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
