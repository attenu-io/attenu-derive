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
import os
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
    if getattr(args, "pubkey", None):
        from delegation_guard.wire import Ed25519Verifier
        signer = Ed25519Verifier(bytes.fromhex(args.pubkey), kid=args.kid)           # public key only: an auditor needs no secret
    elif args.hs256_key:
        signer = HS256TestSigner(secret=bytes.fromhex(args.hs256_key), kid=args.kid)
    else:
        print("verify needs --pubkey <hex> (the product's anchor public key) or --hs256-key <hex> (test signer)", file=sys.stderr); return 2
    rep = evidence.verify_bundle(bundle, signer)
    print(json.dumps(rep, indent=2))
    return 0 if rep["ok"] else 1


def cmd_init(args) -> int:
    from attenu_derive import product
    meta = product.init_product(Path(args.dir), args.product, args.env, anchor=args.anchor, kms_key_id=args.kms_key_id, kms_region=args.kms_region)
    print(json.dumps({"product_dir": str(Path(args.dir).resolve()),
                      **{k: meta.get(k) for k in ("product_id", "name", "environment", "anchor_kind", "anchor_kid")}}, indent=2))
    return 0


def cmd_products(args) -> int:
    from attenu_derive import product
    rows = product.registry_list()
    if not rows:
        print("no products on this machine yet — run: attenu init --product <name> [--env dev] [--dir .]")
    for r in rows:
        print(f"{r['product_id']}  {r['name']}  [{r['environment']}]  {r['dir']}")
    return 0


def cmd_demo(args) -> int:
    from attenu_derive.sample.demo_local import run_demo
    rep = run_demo(Path(args.dir), slow=args.slow, scenario=args.scenario)
    print(json.dumps({k: rep[k] for k in ("scenario", "chain_id", "agents", "tools", "ledger_path", "bundle_path", "anchor_kid", "grants",
                                           "narrower_than_root", "denials_view")}, indent=2))
    return 0


def cmd_ui(args) -> int:
    try:
        from attenu_console.cli import serve_main
    except ImportError:
        print("attenu ui needs the console package: pip install attenu-console", file=sys.stderr); return 2
    return serve_main(["--dir", args.dir, "--port", str(args.port)] + (["--open"] if args.open else []))


def cmd_link(args) -> int:
    from attenu_derive.cloud import link
    out = link(Path(args.dir), args.token, base_url=args.url, environment=args.env)
    print(json.dumps({k: out[k] for k in ("product_id", "environment", "base_url")}, indent=2)); return 0


def cmd_sync(args) -> int:
    import time as _t
    from attenu_derive.cloud import sync
    while True:
        rep = sync(Path(args.dir)); print(json.dumps(rep))
        if not args.watch:
            return 0 if rep["skipped_reason"] is None else 1
        _t.sleep(args.every)


def cmd_policy(args) -> int:
    from attenu_derive import product
    d = Path(args.dir)
    if args.unknown_tools:
        product.set_policy(d, "unknown_tools", args.unknown_tools)
    print(json.dumps({"product_dir": str(d.resolve()), "policy": product.get_policy(d),
                      "choices": {k: list(v) for k, v in product.POLICY_CHOICES.items()}}, indent=2))
    return 0


def cmd_config(args) -> int:
    from attenu_derive import config as cfg
    log = cfg.log(Path(args.dir))
    out = [{"rev": r["rev"], "by": r["by"], "created": r["created"], "signer_kid": r["signer_kid"], "verified": cfg.verify_revision(Path(args.dir), r),
            "grants": r["grants"], "declared_tools": sorted(r["declared_tools"]), "policy": r["policy"],
            **({"diff": cfg.diff(log[i - 1], r)} if i > 0 else {})} for i, r in enumerate(log)]
    print(json.dumps({"head": log[-1]["rev"], "ceiling": cfg.get_ceiling(Path(args.dir)), "revisions": out}, indent=2)); return 0


def cmd_ceiling(args) -> int:
    from attenu_derive import config as cfg
    if args.set is not None:
        cfg.set_ceiling(Path(args.dir), args.set)
    print(json.dumps({"ceiling": cfg.get_ceiling(Path(args.dir))}, indent=2)); return 0


def cmd_grant(args) -> int:
    """Operator grants from the terminal — same signed revisions as the console. `--env` scopes to one environment."""
    from attenu_derive import config as cfg
    from attenu_derive.product import add_grant, remove_grant
    d = Path(args.dir)
    try:
        if args.remove:
            remove_grant(d, args.scope, env=args.env, by=args.by)
        else:
            add_grant(d, args.scope, env=args.env, by=args.by)
    except cfg.RevisionError as exc:
        print(f"refused: {exc}", file=sys.stderr); return 2
    head = cfg.head(d)
    print(json.dumps({"rev": head["rev"], "grants": head["grants"]}, indent=2)); return 0


def cmd_report(args) -> int:
    from attenu_derive import report
    d = Path(args.dir); ev = d / ".attenu" / "evidence"; written = []
    for bp in sorted(ev.glob("*/*.bundle.json")) if ev.exists() else []:
        if args.chain and bp.stem.replace(".bundle", "") != args.chain:
            continue
        written.append(str(report.write_chain_report(d, bp)))
    summary = ev / "index.html"; ev.mkdir(parents=True, exist_ok=True); summary.write_text(report.render_product_report(d)); written.append(str(summary))
    print(json.dumps({"reports": written}, indent=2)); return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="attenu", description="Attenu day-0 kit + onboarding + offline evidence verify")
    ap.add_argument("--version", action="version", version=f"attenu {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("coverage", help="what the kit resolves for these tool calls")
    c.add_argument("files", nargs="+"); c.add_argument("--domain", default=None); c.set_defaults(fn=cmd_coverage)
    o = sub.add_parser("onboard", help="day-0 report + a draft domain pack for the gaps")
    o.add_argument("files", nargs="+"); o.add_argument("--domain", default=None); o.add_argument("--scaffold", default=None); o.set_defaults(fn=cmd_onboard)
    v = sub.add_parser("verify", help="offline-verify an exported evidence bundle")
    v.add_argument("bundle"); v.add_argument("--hs256-key", default=None); v.add_argument("--kid", default="k1")
    v.add_argument("--pubkey", default=None, help="the product's Ed25519 anchor public key (hex) — from .attenu/product.json anchor_pub")
    v.set_defaults(fn=cmd_verify)
    i = sub.add_parser("init", help="give this directory a product identity + a local anchor key (no cloud, no token)")
    i.add_argument("--product", required=True); i.add_argument("--env", default="dev"); i.add_argument("--dir", default=".")
    i.add_argument("--anchor", choices=["local", "kms"], default="local", help="anchor key custody: local Ed25519 key file (default) or a cloud KMS key (never leaves the HSM)")
    i.add_argument("--kms-key-id", default=None); i.add_argument("--kms-region", default=None)
    i.set_defaults(fn=cmd_init)
    pr = sub.add_parser("products", help="products known on this machine"); pr.set_defaults(fn=cmd_products)
    d = sub.add_parser("demo", help="USD-0 scripted travel-booking run that writes a REAL ledger into this product (no model, no key)")
    d.add_argument("--dir", default="."); d.add_argument("--slow", type=float, default=0.0, metavar="SECONDS", help="pause SECONDS between steps so a watching UI animates (e.g. --slow 1); default 0 = instant")
    d.add_argument("--scenario", choices=["basic", "fanout"], default="basic", help="basic: planner -> booker (2 agents); fanout: 9 agents / 18 tools, every disposition, a strike-policy revocation")
    d.set_defaults(fn=cmd_demo)
    po = sub.add_parser("policy", help="show / set this product's defaults for what the catalog cannot resolve (unknown tools: deny | heuristic)")
    po.add_argument("--dir", default="."); po.add_argument("--unknown-tools", choices=["deny", "heuristic"], default=None)
    po.set_defaults(fn=cmd_policy)
    cf = sub.add_parser("config", help="the product's signed config revisions (grants, declared tools, policy): log + diffs")
    cf.add_argument("--dir", default="."); cf.set_defaults(fn=cmd_config)
    g = sub.add_parser("grant", help="grant / revoke an operator scope as a signed revision; --env scopes it to one environment")
    g.add_argument("scope"); g.add_argument("--env", default=None); g.add_argument("--remove", action="store_true")
    g.add_argument("--by", default=None); g.add_argument("--dir", default="."); g.set_defaults(fn=cmd_grant)
    ce = sub.add_parser("ceiling", help="show / set the ceiling: the scopes this product may EVER be granted (a revision beyond it is refused)")
    ce.add_argument("--dir", default="."); ce.add_argument("--set", nargs="*", default=None, metavar="SCOPE"); ce.set_defaults(fn=cmd_ceiling)
    rp = sub.add_parser("report", help="write printable evidence reports (HTML) for this product's chains + a product summary (print -> PDF)")
    rp.add_argument("--dir", default="."); rp.add_argument("--chain", default=None); rp.set_defaults(fn=cmd_report)
    lk = sub.add_parser("link", help="connect this product to the Attenu cloud with a self-serve token (writes .attenu/token, cloud.json, telemetry=on)")
    lk.add_argument("--token", required=True); lk.add_argument("--dir", default="."); lk.add_argument("--env", default=None)
    lk.add_argument("--url", default=os.environ.get("ATTENU_CLOUD_URL", "https://app.attenu.io")); lk.set_defaults(fn=cmd_link)
    sy = sub.add_parser("sync", help="drain the spool + anchors to the cloud, heartbeat, pull grants (a separate process — never in the deny path)")
    sy.add_argument("--dir", default="."); sy.add_argument("--watch", action="store_true"); sy.add_argument("--every", type=float, default=10.0)
    sy.set_defaults(fn=cmd_sync)
    u = sub.add_parser("ui", help="open the local console over this machine's products (needs attenu-console)")
    u.add_argument("--dir", default="."); u.add_argument("--port", type=int, default=8787); u.add_argument("--open", action="store_true")
    u.set_defaults(fn=cmd_ui)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
