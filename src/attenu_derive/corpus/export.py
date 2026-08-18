"""
Export the delegation-guard audit log of a sampled run into corpus rows.

One row per DELEGATION EVENT (the root and every `spawn`), carrying that node's
recorded tool calls (`allow`/`deny` events on the node) as `child_calls`, the
mechanical *observed envelope* (the smallest set of tools/quantities that would
have admitted every ALLOWED call — a provisional label, see design §4/§5a), and
the denied calls as `negatives`. Derived features only: contexts are already
redacted by the recorder; task text is hashed unless `task_text_mode="keep"`
(local mirror for gold review only — never in a shipped corpus).
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Mapping

from attenu_derive.sample.features import length_bucket

__all__ = ["audit_to_corpus_rows", "observed_envelope"]

_QORDER = ["0", "1", "2-10", "11-100", "101-1k", "1k-10k", "10k-100k", "100k-1M", "1M+"]


def _qmax(a: str | None, b: str) -> str:
    if a is None:
        return b
    return a if _QORDER.index(a) >= _QORDER.index(b) else b


def _task_features(task: str) -> dict:
    t = task or ""
    return {
        "len_bucket": length_bucket(t),
        "has_url": ("http://" in t or "https://" in t),
        "has_email": "@" in t,
        "has_path": ("/" in t or "\\" in t),
        "words_bucket": length_bucket(" ".join(t.split())) if t else "0",
    }


def observed_envelope(row: Mapping) -> dict:
    """The benign minimum this node actually needed: tools it was ALLOWED to
    call and, per quantity dimension, the max bucket observed."""
    tools: set[str] = set()
    qmax: dict[str, str] = {}
    for c in row.get("child_calls", []):
        if c.get("outcome") != "allow":
            continue
        tools.add(c["tool"])
        for dim, bucket in (c.get("quantities") or {}).items():
            qmax[dim] = _qmax(qmax.get(dim), bucket)
    return {"tools": sorted(tools), "quantities_max": qmax}


def audit_to_corpus_rows(entries: Iterable[Mapping], *, run: Mapping, task_text_mode: str = "hash") -> list[dict]:
    """`entries` = the shim's audit log (list of dicts, ordered by seq).
    `run` = {project, framework, model, seed, salt, ...} — stamped on every row.
    `task_text_mode` = "hash" (default; shipped corpora) | "keep" (local mirror only)."""
    if task_text_mode not in ("hash", "keep"):
        raise ValueError("task_text_mode must be 'hash' or 'keep'")
    entries = sorted(entries, key=lambda e: e.get("seq", 0))
    nodes: dict[str, dict] = {}
    order: list[str] = []
    salt = str(run.get("salt", ""))

    def new_row(node: str, agent: str, parent: str | None, task: str | None, requested=None, granted=None) -> dict:
        row = {
            "event_id": f"{run.get('project','?')}:{run.get('framework','?')}:{node}",
            "source": "observed",
            "project": run.get("project"), "framework": run.get("framework"),
            "run": {k: run[k] for k in ("model", "seed", "versions") if k in run},
            "node": node, "parent_node": parent, "agent": agent,
            "task_hash": hashlib.sha256(f"{salt}\x1f{task or ''}".encode()).hexdigest()[:16] if task is not None else None,
            "task_features": _task_features(task) if task is not None else None,
            "parent_authority": granted if parent is not None else None,   # what THIS node was granted (observe: wildcard)
            "requested_authority": requested,
            "child_calls": [], "negatives": [], "delegated_to": [],
            "label_provenance": "observed",
        }
        if task_text_mode == "keep" and task is not None:
            row["task"] = task
        return row

    for e in entries:
        ev = e.get("event")
        if ev == "root":
            row = new_row(e["node"], e.get("agent", "root"), None, e.get("task"))
            nodes[e["node"]] = row; order.append(e["node"])
        elif ev == "spawn":
            row = new_row(e["node"], e.get("agent"), e.get("parent"), e.get("task"), e.get("requested"), e.get("granted"))
            nodes[e["node"]] = row; order.append(e["node"])
            parent = nodes.get(e.get("parent"))
            if parent is not None:
                parent["delegated_to"].append(e.get("agent"))
        elif ev in ("allow", "deny"):
            row = nodes.get(e.get("node"))
            if row is None:
                continue
            ctx = e.get("context") or {}
            call = {
                "tool": e.get("tool"), "scope": e.get("scope"), "outcome": ev,
                "arg_shape": ctx.get("arg_shape", {}), "quantities": ctx.get("quantities", {}),
                "str_len_buckets": ctx.get("str_len_buckets", {}), "arg_hashes": ctx.get("arg_hashes", {}),
            }
            if ev == "deny":
                call["reason"] = e.get("reason")
                row["negatives"].append(e.get("tool"))
            row["child_calls"].append(call)
        # kill / spawn_denied are structural; not corpus rows.

    rows = [nodes[n] for n in order]
    for r in rows:
        r["observed_envelope"] = observed_envelope(r)
    return rows
