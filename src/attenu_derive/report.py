"""Evidence report — a printable HTML page an auditor, a board or a customer's security lead can read, rendered
from the bundle ALONE plus its offline verification (a fold; no engine, nothing derived). Print → PDF in any browser.

    attenu report [--dir .] [--chain <id>]     -> .attenu/evidence/<boot>/<chain>.report.html (+ product summary)

Sections: verification (the three checks, the anchor key, how to re-verify yourself); the delegation chain as a
table (agent, parent, authority, allowed, denials) and as an indented tree; what was denied and why, in the user's
words (request held · unresolved — declare it · out of authority — stopped · revoked); the decisions on
record; the ledger tail. Static, no scripts.
"""
from __future__ import annotations

import html as _h
import json
from datetime import datetime, timezone
from pathlib import Path

from attenu_guard import evidence

__all__ = ["render_chain_report", "render_product_report", "write_chain_report"]

_LABEL = {"held_pending_grant": "request held", "withheld_tier2": "request held (tier-2)", "unresolved": "unresolved — declare it",
          "out_of_authority": "out of authority — stopped", "revoked": "revoked — authority withdrawn", "ceiling_exceeded": "over ceiling — stopped"}
_CSS = """
body{font:14px/1.45 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#0f172a;max-width:60rem;margin:2rem auto;padding:0 1rem}
h1{font-size:1.5rem;margin:.2rem 0} h2{font-size:1.05rem;margin:1.6rem 0 .5rem;border-bottom:1px solid #e2e8f0;padding-bottom:.2rem}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em;background:#f1f5f9;padding:0 .25rem;border-radius:3px}
table{border-collapse:collapse;width:100%;font-size:.9em} th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid #e2e8f0;vertical-align:top}
th{font-size:.75em;text-transform:uppercase;letter-spacing:.04em;color:#64748b}
.ok{color:#047857;font-weight:600} .bad{color:#b91c1c;font-weight:600} .pill{display:inline-block;border:1px solid #cbd5e1;border-radius:999px;padding:0 .5rem;font-size:.8em}
.held{background:#fef3c7;border-color:#fcd34d} .stopped{background:#fee2e2;border-color:#fca5a5} .unres{background:#f1f5f9} .muted{color:#64748b}
.tree li{list-style:none} .tree ul{padding-left:1.2rem;border-left:1px dashed #cbd5e1;margin-left:.3rem}
@media print{body{max-width:none;margin:0} h2{break-after:avoid} table{break-inside:auto} tr{break-inside:avoid}}
"""


def _e(x) -> str:
    return _h.escape(str(x if x is not None else "—"))


def _pill(d: str | None) -> str:
    cls = "held" if d == "held_pending_grant" else "stopped" if d in ("out_of_authority", "revoked", "ceiling_exceeded") else "unres"
    return f'<span class="pill {cls}">{_e(_LABEL.get(d or "", d or "denied"))}</span>'


def _tree(graph: dict) -> str:
    children: dict[str, list[str]] = {}
    for e in graph["edges"]:
        children.setdefault(e["parent"], []).append(e["child"])
    roots = [n for n, m in graph["nodes"].items() if not m.get("parent")]

    def node(n: str) -> str:
        m = graph["nodes"][n]
        flags = (" · revoked" if m.get("revoked") else "") + (" · done" if m.get("complete") else "")
        den = ", ".join(f"{v} {_e(_LABEL.get(k, k))}" for k, v in (m.get("denials_by_disposition") or {}).items()) or "no denials"
        kids = "".join(node(c) for c in children.get(n, []))
        return (f'<li><strong>{_e(m.get("agent"))}</strong> <span class="muted">({len(m.get("scopes", []))} scopes{flags})</span> — '
                f'{m.get("allows", 0)} allowed, {den}' + (f"<ul>{kids}</ul>" if kids else "") + "</li>")
    return '<ul class="tree">' + "".join(node(r) for r in roots) + "</ul>"


def render_chain_report(bundle: dict, verify: dict | None, product_meta: dict | None, *, decisions: list[dict] | None = None) -> str:
    graph = evidence.delegation_graph(bundle); denials = evidence.denials(bundle)
    entries = bundle.get("entries") or []; anchor = bundle.get("anchor") or {}
    meta = product_meta or {}
    ok = bool(verify and verify.get("ok")); checks = (verify or {}).get("checks", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for n, m in graph["nodes"].items():
        parent = graph["nodes"].get(m.get("parent") or "", {}).get("agent") if m.get("parent") else "—"
        den = " ".join(f"{v}× {_pill(k)}" for k, v in (m.get("denials_by_disposition") or {}).items()) or '<span class="muted">none</span>'
        rows.append(f"<tr><td><strong>{_e(m.get('agent'))}</strong>{' <span class=muted>(root)</span>' if not m.get('parent') else ''}"
                    f"{' <span class=bad>revoked</span>' if m.get('revoked') else ''}</td><td>{_e(parent)}</td>"
                    f"<td>{' '.join(f'<code>{_e(s)}</code>' for s in m.get('scopes', [])) or '—'}</td><td>{m.get('allows', 0)}</td><td>{den}</td></tr>")
    den_rows = "".join(f"<tr><td><strong>{_e(r['agent'])}</strong> → <code>{_e(r['tool'])}</code></td><td><code>{_e(r['scope'])}</code></td>"
                       f"<td>{_pill(r.get('disposition') or r.get('reason'))}</td><td>{r['count']}×</td></tr>" for r in denials) or \
               '<tr><td colspan=4 class=muted>Nothing was denied in this chain.</td></tr>'
    dec_rows = "".join(f"<tr><td>{_e(d.get('question'))}</td><td>{_e(d.get('status'))}</td><td>{_e(d.get('answered_at') or '')}</td></tr>" for d in (decisions or [])) \
               if decisions else ""
    tail = "".join(f"<tr><td class=mono>{e.get('seq')}</td><td>{_e(e.get('event'))}</td><td>{_e(e.get('agent') or '')}</td><td><code>{_e(e.get('tool') or e.get('scope') or '')}</code></td>"
                   f"<td>{_e(e.get('disposition') or e.get('reason') or '')}</td><td class=mono>{_e(str(e.get('hash', ''))[:12])}…</td></tr>" for e in entries[-25:])
    verdict = '<span class="ok">verified ✓</span>' if ok else '<span class="bad">verification failed ✗</span>'
    check_list = "".join(f'<li class="{"ok" if v else "bad"}">{"✓" if v else "✗"} {_e(k)}</li>' for k, v in checks.items()) or '<li class="muted">not verified (no anchor or no key)</li>'
    failures = "".join(f"<li class=bad>{_e(f)}</li>" for f in (verify or {}).get("failures", []))
    pub = meta.get("anchor_pub", "")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><title>Attenu evidence — {_e(bundle.get('chain_id'))}</title><style>{_CSS}</style></head><body>
<div class=muted>Attenu · evidence report · generated {now}</div>
<h1>Delegation chain <code>{_e(bundle.get('chain_id'))}</code> — {verdict}</h1>
<div class=muted>Product: <strong>{_e(meta.get('name', '—'))}</strong> ({_e(meta.get('environment', '—'))}) · product id <code>{_e(meta.get('product_id', '—'))}</code> · {len(entries)} ledger entries · {len(graph['nodes'])} agents</div>

<h2>1. Verification (from the bundle alone — no engine, no Attenu)</h2>
<ul>{check_list}{failures}</ul>
<p>Anchor: head <code>{_e(anchor.get('head', '—'))}</code> at seq {_e(anchor.get('seq'))}, signed with key <code>{_e(anchor.get('kid', meta.get('anchor_kid', '—')))}</code> (the product's own key; this report checked it with the <em>public</em> half only).</p>
<p>Re-verify yourself, offline: <code>attenu verify {_e(bundle.get('chain_id'))}.bundle.json --pubkey {_e(pub)} --kid {_e(meta.get('anchor_kid', ''))}</code> — integrity (the hash chain reproduces and matches the signed anchor), monotonicity (every delegated authority ⊂ its parent's), containment (every allowed action was inside the acting agent's authority).</p>

<h2>2. Who handed work to whom, and what each could do</h2>
{_tree(graph)}
<table><thead><tr><th>Agent</th><th>Delegated by</th><th>Authority (scopes)</th><th>Allowed</th><th>Denied</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class=muted>Every delegated authority is ⊂ its parent's (monotonic attenuation) — checked above, not assumed.</p>

<h2>3. What was denied, and why</h2>
<table><thead><tr><th>Agent → tool</th><th>Scope</th><th>Why</th><th>Times</th></tr></thead><tbody>{den_rows}</tbody></table>
<p class=muted><em>held</em> = the agent waited for an operator grant · <em>unresolved</em> = the tool is unknown to the catalog (declare it) · <em>out of authority</em> = the agent reached for something it was never given · <em>revoked</em> = its authority was withdrawn (strike policy or operator).</p>
{f'<h2>4. Decisions on record</h2><table><thead><tr><th>Question</th><th>Decision</th><th>When</th></tr></thead><tbody>{dec_rows}</tbody></table>' if dec_rows else ''}

<h2>{'5' if dec_rows else '4'}. Ledger (last {min(25, len(entries))} of {len(entries)} entries)</h2>
<table><thead><tr><th>seq</th><th>event</th><th>agent</th><th>tool / scope</th><th>why</th><th>hash</th></tr></thead><tbody>{tail}</tbody></table>
<p class=muted>Raw prompts and tool arguments never enter the ledger (redacted at capture; an allow-list is enforced at export). This page derives nothing — it is a fold over the bundle.</p>
</body></html>"""


def write_chain_report(product_dir: Path, bundle_path: Path) -> Path:
    from attenu_derive.product import load_anchor_verifier, load_product_json
    bundle = json.loads(Path(bundle_path).read_text())
    try:
        verify = evidence.verify_bundle(bundle, load_anchor_verifier(product_dir))
    except Exception:  # noqa: BLE001
        verify = None
    decisions = None
    dpath = Path(product_dir) / ".attenu" / "decisions.json"
    if dpath.exists():
        try:
            decisions = [{"question": f"{v.get('agent')} → {v.get('tool')} ({v.get('scope')})", "status": v.get("status"), "answered_at": v.get("at")}
                         for v in json.loads(dpath.read_text()).values()]
        except Exception:  # noqa: BLE001
            decisions = None
    html = render_chain_report(bundle, verify, load_product_json(product_dir), decisions=decisions)
    out = Path(bundle_path).with_name(Path(bundle_path).name.replace(".bundle.json", ".report.html"))
    out.write_text(html)
    return out


def render_product_report(product_dir: Path) -> str:
    """One page per product: every chain with its verdict and denial counts, linking to the chain reports."""
    from attenu_derive.product import load_anchor_verifier, load_product_json
    meta = load_product_json(product_dir); ev = Path(product_dir) / ".attenu" / "evidence"
    rows = []
    for bp in sorted(ev.glob("*/*.bundle.json"), key=lambda p: p.stat().st_mtime, reverse=True) if ev.exists() else []:
        bundle = json.loads(bp.read_text())
        try:
            ok = evidence.verify_bundle(bundle, load_anchor_verifier(product_dir))["ok"]
        except Exception:  # noqa: BLE001
            ok = None
        g = evidence.delegation_graph(bundle); den = evidence.denials(bundle)
        by = {}
        for r in den:
            k = r.get("disposition") or r.get("reason") or "denied"; by[k] = by.get(k, 0) + r["count"]
        rel = f"{bp.parent.name}/{bp.name.replace('.bundle.json', '.report.html')}"
        rows.append(f"<tr><td><a href='{_e(rel)}'><code>{_e(bundle.get('chain_id'))}</code></a></td><td>{len(g['nodes'])}</td>"
                    f"<td>{sum(n['allows'] for n in g['nodes'].values())}</td><td>{' '.join(f'{v}× {_pill(k)}' for k, v in by.items()) or '<span class=muted>none</span>'}</td>"
                    f"<td>{'<span class=ok>verified ✓</span>' if ok else ('<span class=bad>failed ✗</span>' if ok is False else '<span class=muted>not anchored</span>')}</td></tr>")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><title>Attenu evidence — {_e(meta.get('name'))}</title><style>{_CSS}</style></head><body>
<div class=muted>Attenu · product evidence summary · generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}</div>
<h1>{_e(meta.get('name'))} <span class=muted>({_e(meta.get('environment'))})</span></h1>
<p class=muted>Product id <code>{_e(meta.get('product_id'))}</code> · anchor key <code>{_e(meta.get('anchor_kid'))}</code> · {len(rows)} chains</p>
<table><thead><tr><th>Chain</th><th>Agents</th><th>Allowed</th><th>Denied</th><th>Trail</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan=5 class=muted>no chains yet</td></tr>'}</tbody></table>
</body></html>"""
