"""
`attenu` — the customer-facing CLI (Phase A packaging). The engine's day-0 kit and onboarding flow, runnable
from an installed console script rather than `python -m`. Subcommands:

    attenu coverage  <corpus.jsonl ...> [--domain NAME]    # what the kit resolves for these tool calls
    attenu onboard   <mirror.jsonl ...> [--domain NAME]    # day-0 report + a DRAFT domain pack for the gaps
    attenu verify    <bundle.json> --hs256-key <hex>       # offline-verify an exported evidence bundle
    attenu --version

`onboard` is the ≤1h path: it prints the day-0 coverage (shipped kit only), and — for every tool the base
catalog does not curate — emits a starter pack entry (heuristic guess + a TODO, tier-2 marked requires_grant),
so the operator edits a draft rather than authoring from scratch.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

from attenu_derive import __version__
from attenu_derive.catalog.coverage import _classify, coverage, load_catalog, load_domain, resolve


def _rows(paths):
    return [json.loads(l) for p in paths for f in glob.glob(p) for l in open(f) if l.strip()]


def _tools(rows):
    c = Counter()
    for r in rows:
        for call in r.get("child_calls", []):
            c[call["tool"]] += 1
    return c


def cmd_coverage(args) -> int:
    rows = _rows(args.files)
    if not rows:
        print("no rows matched", file=sys.stderr); return 2
    cat = load_catalog(); ov = load_domain(args.domain) if args.domain else None
    cov = coverage(rows, cat, overlay=ov)
    print(json.dumps({k: cov[k] for k in ("calls", "calls_grantable_share", "calls_curated_share",
                                          "calls_requires_grant_share", "calls_withheld_share", "calls_unresolved_share",
                                          "uncovered_tools", "withheld_tools_top") if k in cov}, indent=2))
    return 0


def scaffold_pack(rows, domain_name: str) -> dict:
    """A DRAFT domain pack for tools the base catalog does not already curate: heuristic guess + a review flag;
    tier-2 families marked requires_grant. The operator edits this, they do not author from zero."""
    cat = load_catalog(); tools = _tools(rows); entries = {}
    for tool in sorted(tools):
        base = _classify(cat, tool)
        if base == "curated":
            continue                                   # already resolved by the shipped kit; no pack entry needed
        e = resolve(cat, tool) or {}
        scope = e.get("scope") or "unknown"
        tier = int(e.get("tier", 2))
        entry = {"scope": scope if scope != "unknown" else "REVIEW: no confident resolution", "tier": tier,
                 "_review": f"day-0={base}; calls={tools[tool]}; confirm scope"}
        if tier >= 2:
            entry["requires_grant"] = True             # money/mail/delete/exec: held pending an operator grant
        entries[tool] = entry
    return {"domain": domain_name, "version": 1, "_generated": "attenu onboard — DRAFT, review every entry", "tools": entries}


def cmd_onboard(args) -> int:
    rows = _rows(args.files)
    if not rows:
        print("no rows matched", file=sys.stderr); return 2
    cat = load_catalog()
    day0 = coverage(rows, cat)
    print("== day-0 (shipped kit only) ==")
    print(json.dumps({k: day0[k] for k in ("calls", "calls_curated_share", "calls_grantable_share",
                                            "calls_withheld_share", "calls_unresolved_share")}, indent=2))
    if args.domain:
        ov = load_domain(args.domain)
        after = coverage(rows, cat, overlay=ov)
        print(f"== with pack '{args.domain}' ==")
        print(json.dumps({k: after[k] for k in ("calls_curated_share", "calls_requires_grant_share", "calls_unresolved_share")}, indent=2))
    draft = scaffold_pack(rows, args.domain or "my-app")
    out = Path(args.scaffold) if args.scaffold else None
    if out:
        import yaml
        out.write_text(yaml.safe_dump(draft, sort_keys=False))
        print(f"== draft pack written to {out} ({len(draft['tools'])} tools to review) ==")
    else:
        print(f"== draft pack ({len(draft['tools'])} tools to review; --scaffold PATH to write) ==")
        print(json.dumps(draft, indent=2))
    return 0


def cmd_verify(args) -> int:
    from delegation_guard import evidence
    from delegation_guard.wire import HS256TestSigner
    bundle = json.loads(Path(args.bundle).read_text())
    if not args.hs256_key:
        print("verify needs --hs256-key <hex> (the anchor signing key)", file=sys.stderr); return 2
    signer = HS256TestSigner(secret=bytes.fromhex(args.hs256_key), kid=args.kid)
    rep = evidence.verify_bundle(bundle, signer)
    print(json.dumps(rep, indent=2))
    return 0 if rep["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="attenu", description="Attenu day-0 kit + onboarding + offline evidence verify")
    ap.add_argument("--version", action="version", version=f"attenu {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("coverage", help="what the kit resolves for these tool calls")
    c.add_argument("files", nargs="+"); c.add_argument("--domain", default=None); c.set_defaults(fn=cmd_coverage)
    o = sub.add_parser("onboard", help="day-0 report + a draft domain pack for the gaps")
    o.add_argument("files", nargs="+"); o.add_argument("--domain", default=None); o.add_argument("--scaffold", default=None); o.set_defaults(fn=cmd_onboard)
    v = sub.add_parser("verify", help="offline-verify an exported evidence bundle")
    v.add_argument("bundle"); v.add_argument("--hs256-key", default=None); v.add_argument("--kid", default="k1"); v.set_defaults(fn=cmd_verify)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
