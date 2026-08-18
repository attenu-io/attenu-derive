"""
Source B, dataset #2 — normalize NousResearch/hermes-function-calling-v1 (Apache-2.0; Hugging Face) into
the corpus schema. Tool-CALLING data, not authority ground truth (design §5a): rows carry
`label_provenance: dataset` + `licence`, live under data/corpus/datasets/ and are EXCLUDED from G1.

    python -m attenu_derive.corpus.normalize_hermes --download --out data

Files used: `func-calling.json` (1,893 multi-turn conversations with tool responses) and
`glaive-function-calling-5k.json` (5,209). `func-calling-singleturn.json` is NOT used — it is the first
turn of `func-calling.json` again (same conversations), so it would double-count events. Rows: one per
conversation = one single-agent delegation event; `tools_available` = the system prompt's tool list;
`child_calls` = every `<tool_call>{json}</tool_call>` the assistant emitted (benign by construction;
args -> redacted features via the recorder's extractor); task text = the human turns, hashed
(features kept); the mirror keeps text + categories.
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

RAW = "https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1/resolve/main"
FILES = ["func-calling.json", "glaive-function-calling-5k.json"]
LICENCE = "Apache-2.0 (NousResearch/hermes-function-calling-v1)"
DATASET = "hermes-function-calling-v1"
SALT = "hermes-fc-v1"
_CALL = re.compile(r"<tool_call>(?:\s|\\n)*(\{.*?\})(?:\s|\\n)*</tool_call>", re.S)     # some rows wrap the JSON in a LITERAL two-char "\n"


def download(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        if not (dst / f).exists():
            urllib.request.urlretrieve(f"{RAW}/{f}", dst / f)


def parse_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Every `<tool_call>{"name":..., "arguments":{...}}</tool_call>` in an assistant turn; malformed JSON is skipped."""
    out = []
    for m in _CALL.finditer(text or ""):
        raw = m.group(1)
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            try:                                   # ~730 func-calling rows carry Python-literal pseudo-JSON (single quotes)
                d = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue
        if not isinstance(d, dict):
            continue
        name = d.get("name"); args = d.get("arguments")
        if isinstance(args, str):
            try: args = json.loads(args)
            except json.JSONDecodeError: args = {"_raw": args}
        if not isinstance(args, dict):
            args = {}
        if not name and "name" in args:               # data quirk (~730 rows): {"arguments": {..., "name": "X"}} — name nested inside arguments
            args = dict(args); name = args.pop("name")
        if name:
            out.append((str(name), args))
    return out


def parse_tools(tools_field) -> list[dict]:
    """The `tools` field is a JSON string (or list) of OpenAI-style {"type":"function","function":{name,description,parameters}}."""
    t = tools_field
    if isinstance(t, str):
        try: t = json.loads(t)
        except json.JSONDecodeError: t = []
    out = []
    for item in t or []:
        fn = item.get("function", item) if isinstance(item, dict) else {}
        if isinstance(fn, dict) and fn.get("name"):
            out.append({"name": fn["name"], "description": fn.get("description", ""), "parameters": fn.get("parameters", {})})
    return out


def row_from_conversation(entry: dict, *, file: str) -> tuple[dict, dict] | None:
    tools = parse_tools(entry.get("tools"))
    conv = entry.get("conversations") or []
    task = " ".join(m.get("value", "") for m in conv if m.get("from") == "human").strip()
    calls = [c for m in conv if m.get("from") == "gpt" for c in parse_tool_calls(m.get("value", ""))]
    if not tools or not calls:
        return None
    child_calls = [{"tool": name, "scope": None, "outcome": "allow", **extract_features(args, salt=SALT)} for name, args in calls]
    event_id = f"hermes:{file.replace('.json', '')}:{entry.get('id')}"
    base = {
        "event_id": event_id, "source": "dataset", "dataset": DATASET, "category": entry.get("category"), "subcategory": entry.get("subcategory"),
        "licence": LICENCE, "project": "hermes-fc-v1", "framework": "hermes", "run": {"file": file},
        "node": event_id, "parent_node": None, "agent": "hermes-agent", "role": "single-agent",
        "task_hash": hashlib.sha256(f"{SALT}\x1f{task}".encode()).hexdigest()[:16], "task_features": _task_features(task),
        "tools_available": [t["name"] for t in tools],
        "tool_schemas_fingerprint": hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()[:16],
        "parent_authority": None, "requested_authority": None,
        "child_calls": child_calls, "negatives": [], "delegated_to": [], "label_provenance": "dataset",
        "turns": sum(1 for m in conv if m.get("from") == "human"),
    }
    base["observed_envelope"] = observed_envelope(base)
    mirror = dict(base, task=task, tool_descriptions={t["name"]: (t.get("description") or "")[:200] for t in tools})
    return base, mirror


def normalize(src: Path) -> tuple[list[dict], list[dict], dict]:
    rows, mirror, skipped = [], [], Counter()
    for f in FILES:
        for e in json.loads((src / f).read_text()):
            r = row_from_conversation(e, file=f)
            if r is None:
                skipped[f] += 1; continue
            rows.append(r[0]); mirror.append(r[1])
    return rows, mirror, dict(skipped)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--src", default=None); ap.add_argument("--out", default="data"); ap.add_argument("--download", action="store_true")
    args = ap.parse_args(argv)
    src = Path(args.src) if args.src else Path(args.out) / "raw" / "hermes-fc-v1"
    if args.download or not all((src / f).exists() for f in FILES):
        download(src)
    rows, mirror, skipped = normalize(src)
    out = Path(args.out); (out / "corpus" / "datasets").mkdir(parents=True, exist_ok=True); (out / "mirror" / "datasets").mkdir(parents=True, exist_ok=True)
    (out / "corpus" / "datasets" / "hermes-fc-v1.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    (out / "mirror" / "datasets" / "hermes-fc-v1.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror) + "\n")
    print(json.dumps({"rows": len(rows), "by_file": dict(Counter(r["run"]["file"] for r in rows)), "skipped_no_tools_or_calls": skipped,
                      "calls": sum(len(r["child_calls"]) for r in rows), "licence": LICENCE,
                      "distinct_tools_available": len({t for r in rows for t in r["tools_available"]}),
                      "categories": len({r["category"] for r in rows})}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
