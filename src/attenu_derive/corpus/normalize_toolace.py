"""
Source B, dataset #3 — normalize Team-ACE/ToolACE (Apache-2.0; Hugging Face; 11,300 dialogs over ~26k
synthetic APIs) into the corpus schema. Tool-CALLING data, not authority ground truth (design §5a):
`label_provenance: dataset` + `licence`, under data/corpus/datasets/, EXCLUDED from G1.

    python -m attenu_derive.corpus.normalize_toolace --download --out data

Format: `system` = instructions + "Here is a list of functions in JSON format that you can invoke: [...]";
`conversations` = user / assistant / tool turns; an assistant tool-call turn is `[Name(arg="v", n=1), Other(...)]`
(names may contain spaces). One row per dialog = one single-agent delegation event: `tools_available` = the
system's function names, `child_calls` = every parsed assistant call (args -> redacted features), task = the
user turns (hashed; mirror keeps text). Unparseable call turns are counted and skipped, never guessed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import urllib.request
from collections import Counter
from pathlib import Path

from attenu_derive.corpus.export import _task_features, observed_envelope
from attenu_derive.sample.features import extract_features

RAW = "https://huggingface.co/datasets/Team-ACE/ToolACE/resolve/main/data.json"
LICENCE = "Apache-2.0 (Team-ACE/ToolACE)"
DATASET = "ToolACE"
SALT = "toolace"
_NAME = r"[A-Za-z_][A-Za-z0-9_ .\-/]*?"
_ITEM = re.compile(rf"({_NAME})\((.*?)\)(?=\s*,\s*{_NAME}\(|\s*\]\s*$)", re.S)


def download(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not (dst / "data.json").exists():
        urllib.request.urlretrieve(RAW, dst / "data.json")


def parse_functions(system: str) -> list[dict]:
    """The balanced JSON list that follows 'you can invoke:' in the system prompt."""
    i = system.find("[", system.find("invoke"))
    if i < 0:
        return []
    depth = 0; in_str = False; esc = False
    for j in range(i, len(system)):
        c = system[j]
        if in_str:
            esc = (c == "\\") if not esc else False
            if c == '"' and not esc: in_str = False
            continue
        if c == '"': in_str = True
        elif c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    fns = json.loads(system[i:j + 1])
                except json.JSONDecodeError:
                    return []
                return [f for f in fns if isinstance(f, dict) and f.get("name")]
    return []


def _split_args(s: str) -> list[str]:
    parts, buf, depth, q = [], [], 0, None
    for c in s:
        if q:
            buf.append(c)
            if c == q and (len(buf) < 2 or buf[-2] != "\\"): q = None
            continue
        if c in "\"'": q = c; buf.append(c); continue
        if c in "([{": depth += 1
        elif c in ")]}": depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(buf)); buf = []; continue
        buf.append(c)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts


def parse_calls(text: str) -> list[tuple[str, dict]] | None:
    """`[Name(a="x", b=1), Other()]` -> [(name, {args})]; None when the turn is not a call list (plain text)."""
    t = (text or "").strip()
    if not (t.startswith("[") and t.endswith("]") and "(" in t):
        return None
    out = []
    for m in _ITEM.finditer(t[1:-1].strip() + "]"):     # keep the lookahead's closing bracket
        name = m.group(1).strip(); args = {}
        for part in _split_args(m.group(2)):
            if "=" not in part:
                continue
            k, v = part.split("=", 1); k = k.strip(); v = v.strip()
            try: args[k] = ast.literal_eval(v)
            except (ValueError, SyntaxError): args[k] = v.strip("\"'")
        out.append((name, args))
    return out or None


def row_from_dialog(entry: dict, idx: int) -> tuple[dict, dict] | None:
    tools = parse_functions(entry.get("system") or "")
    conv = entry.get("conversations") or []
    task = " ".join(m.get("value", "") for m in conv if m.get("from") == "user").strip()
    calls = []
    for m in conv:
        if m.get("from") == "assistant":
            c = parse_calls(m.get("value", ""))
            if c: calls += c
    if not tools or not calls:
        return None
    child_calls = [{"tool": name, "scope": None, "outcome": "allow", **extract_features(args, salt=SALT)} for name, args in calls]
    event_id = f"toolace:{idx}"
    base = {
        "event_id": event_id, "source": "dataset", "dataset": DATASET, "category": None, "licence": LICENCE,
        "project": "toolace", "framework": "toolace", "run": {},
        "node": event_id, "parent_node": None, "agent": "toolace-agent", "role": "single-agent",
        "task_hash": hashlib.sha256(f"{SALT}\x1f{task}".encode()).hexdigest()[:16], "task_features": _task_features(task),
        "tools_available": [t["name"] for t in tools],
        "tool_schemas_fingerprint": hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()[:16],
        "parent_authority": None, "requested_authority": None,
        "child_calls": child_calls, "negatives": [], "delegated_to": [], "label_provenance": "dataset",
        "turns": sum(1 for m in conv if m.get("from") == "user"),
    }
    base["observed_envelope"] = observed_envelope(base)
    mirror = dict(base, task=task, tool_descriptions={t["name"]: (t.get("description") or "")[:200] for t in tools})
    return base, mirror


def normalize(src: Path) -> tuple[list[dict], list[dict], int]:
    rows, mirror, skipped = [], [], 0
    for i, e in enumerate(json.loads((src / "data.json").read_text())):
        r = row_from_dialog(e, i)
        if r is None:
            skipped += 1; continue
        rows.append(r[0]); mirror.append(r[1])
    return rows, mirror, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--src", default=None); ap.add_argument("--out", default="data"); ap.add_argument("--download", action="store_true")
    args = ap.parse_args(argv)
    src = Path(args.src) if args.src else Path(args.out) / "raw" / "toolace"
    if args.download or not (src / "data.json").exists():
        download(src)
    rows, mirror, skipped = normalize(src)
    out = Path(args.out); (out / "corpus" / "datasets").mkdir(parents=True, exist_ok=True); (out / "mirror" / "datasets").mkdir(parents=True, exist_ok=True)
    (out / "corpus" / "datasets" / "toolace.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    (out / "mirror" / "datasets" / "toolace.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror) + "\n")
    print(json.dumps({"rows": len(rows), "skipped_no_tools_or_calls": skipped, "calls": sum(len(r["child_calls"]) for r in rows), "licence": LICENCE,
                      "distinct_tools_available": len({t for r in rows for t in r["tools_available"]})}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
