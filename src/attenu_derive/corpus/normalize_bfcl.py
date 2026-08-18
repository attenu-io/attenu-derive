"""
Source B — normalize BFCL v4 (Berkeley Function Calling Leaderboard, Gorilla; Apache-2.0) into the
corpus schema. Tool-CALLING data, not authority ground truth (design §5a): rows are marked
`label_provenance: dataset`, carry `licence`, live under data/corpus/datasets/ and are EXCLUDED
from G1 gate metrics. They give volume + tool/schema diversity for the catalog and retrieval.

    python -m attenu_derive.corpus.normalize_bfcl --src <dir with BFCL_v4_*.json + possible_answer/ + multi_turn_func_doc/> --out data
    python -m attenu_derive.corpus.normalize_bfcl --download --out data      # fetch from GitHub raw (Apache-2.0)

Mapping: one entry (single-turn categories) or one TURN (multi_turn_base) = one delegation-event
row: `tools_available` = the entry's function names (multi-turn: the involved classes' function
docs), `child_calls` = the ground-truth calls (benign by definition; args -> redacted features via
the same extractor as the recorder), `observed_envelope` = tools + quantity maxima. Task text
hashed (features kept); the mirror keeps text.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from attenu_derive.corpus.export import _task_features, observed_envelope
from attenu_derive.sample.features import extract_features

RAW = "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/bfcl_eval/data"
SINGLE_TURN = ["BFCL_v4_simple_python.json", "BFCL_v4_multiple.json", "BFCL_v4_parallel.json", "BFCL_v4_parallel_multiple.json",
               "BFCL_v4_live_simple.json", "BFCL_v4_live_multiple.json"]
MULTI_TURN = ["BFCL_v4_multi_turn_base.json"]
CLASS_DOC = {"GorillaFileSystem": "gorilla_file_system.json", "MathAPI": "math_api.json", "MessageAPI": "message_api.json",
             "TwitterAPI": "posting_api.json", "TicketAPI": "ticket_api.json", "TradingBot": "trading_bot.json",
             "TravelAPI": "travel_booking.json", "VehicleControlAPI": "vehicle_control.json", "WebSearchAPI": "web_search.json"}
LICENCE = "Apache-2.0 (gorilla/berkeley-function-call-leaderboard)"
SALT = "bfcl-v4"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def download(dst: Path) -> None:
    (dst / "possible_answer").mkdir(parents=True, exist_ok=True); (dst / "multi_turn_func_doc").mkdir(exist_ok=True)
    for f in SINGLE_TURN + MULTI_TURN:
        for sub in ("", "possible_answer/"):
            urllib.request.urlretrieve(f"{RAW}/{sub}{f}", dst / sub / f)
    for f in CLASS_DOC.values():
        urllib.request.urlretrieve(f"{RAW}/multi_turn_func_doc/{f}", dst / "multi_turn_func_doc" / f)


def _canonical_args(gt_args: dict) -> dict:
    """BFCL ground truth lists candidate values per param; take the first non-empty candidate."""
    out = {}
    for k, cands in (gt_args or {}).items():
        if isinstance(cands, list):
            v = next((c for c in cands if c not in ("", None)), cands[0] if cands else None)
        else:
            v = cands
        out[k] = v
    return out


def _parse_call(s: str) -> tuple[str, dict]:
    """'mv(source='a.pdf', destination='temp')' -> ('mv', {...}); tolerant of odd literals."""
    m = re.match(r"\s*([\w\.]+)\((.*)\)\s*$", s, re.S)
    if not m:
        return s.strip(), {}
    name, body = m.group(1), m.group(2)
    try:
        node = ast.parse(f"f({body})", mode="eval").body
        args = {kw.arg: ast.literal_eval(kw.value) if isinstance(kw.value, (ast.Constant, ast.List, ast.Dict, ast.Tuple)) else "<expr>" for kw in node.keywords}
        for i, a in enumerate(node.args):
            args[f"_pos{i}"] = ast.literal_eval(a) if isinstance(a, ast.Constant) else "<expr>"
        return name, args
    except Exception:
        return name, {"_raw_len_bucket": "?"}


def _row(event_id: str, category: str, task: str, tools: list[dict], calls: list[tuple[str, dict]], turn: int | None = None) -> tuple[dict, dict]:
    child_calls = []
    for name, args in calls:
        f = extract_features(args, salt=SALT)
        child_calls.append({"tool": name, "scope": None, "outcome": "allow", **f})
    base = {
        "event_id": event_id, "source": "dataset", "dataset": "BFCL_v4", "category": category, "licence": LICENCE,
        "project": "bfcl-v4", "framework": "bfcl", "run": {"turn": turn} if turn is not None else {},
        "node": event_id, "parent_node": None, "agent": "bfcl-agent", "role": "single-agent",
        "task_hash": hashlib.sha256(f"{SALT}\x1f{task}".encode()).hexdigest()[:16], "task_features": _task_features(task),
        "tools_available": [t.get("name") for t in tools], "tool_schemas_fingerprint": hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()[:16],
        "parent_authority": None, "requested_authority": None,
        "child_calls": child_calls, "negatives": [], "delegated_to": [], "label_provenance": "dataset",
    }
    base["observed_envelope"] = observed_envelope(base)
    mirror = dict(base, task=task, tool_descriptions={t.get("name"): (t.get("description") or "")[:200] for t in tools})
    return base, mirror


def normalize(src: Path) -> tuple[list[dict], list[dict]]:
    rows, mirror = [], []
    for f in SINGLE_TURN:
        entries = _jsonl(src / f); answers = {a["id"]: a for a in _jsonl(src / "possible_answer" / f)}
        cat = f.replace("BFCL_v4_", "").replace(".json", "")
        for e in entries:
            a = answers.get(e["id"]);
            if not a: continue
            task = " ".join(m.get("content", "") for turn in e["question"] for m in turn if m.get("role") == "user")
            calls = []
            for gt in a.get("ground_truth", []):
                for name, args in (gt.items() if isinstance(gt, dict) else []):
                    calls.append((name, _canonical_args(args)))
            r, m = _row(f"bfcl:{e['id']}", cat, task, e.get("function", []), calls); rows.append(r); mirror.append(m)
    docs = {cls: _jsonl(src / "multi_turn_func_doc" / fn) for cls, fn in CLASS_DOC.items() if (src / "multi_turn_func_doc" / fn).exists()}
    for f in MULTI_TURN:
        entries = _jsonl(src / f); answers = {a["id"]: a for a in _jsonl(src / "possible_answer" / f)}
        cat = f.replace("BFCL_v4_", "").replace(".json", "")
        for e in entries:
            a = answers.get(e["id"]);
            if not a: continue
            tools = [t for cls in e.get("involved_classes", []) for t in docs.get(cls, [])]
            for ti, (turn, gt_calls) in enumerate(zip(e["question"], a.get("ground_truth", []))):
                task = " ".join(m.get("content", "") for m in turn if m.get("role") == "user")
                calls = [_parse_call(s) for s in gt_calls]
                r, m = _row(f"bfcl:{e['id']}:t{ti}", cat, task, tools, calls, turn=ti); rows.append(r); mirror.append(m)
    return rows, mirror


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--src", default=None); ap.add_argument("--out", default="data"); ap.add_argument("--download", action="store_true")
    args = ap.parse_args(argv)
    src = Path(args.src) if args.src else Path(args.out) / "raw" / "bfcl-v4"
    if args.download or not src.exists():
        download(src)
    rows, mirror = normalize(src)
    out = Path(args.out); (out / "corpus" / "datasets").mkdir(parents=True, exist_ok=True); (out / "mirror" / "datasets").mkdir(parents=True, exist_ok=True)
    (out / "corpus" / "datasets" / "bfcl-v4.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    (out / "mirror" / "datasets" / "bfcl-v4.jsonl").write_text("\n".join(json.dumps(r, sort_keys=True) for r in mirror) + "\n")
    from collections import Counter
    print(json.dumps({"rows": len(rows), "by_category": dict(Counter(r["category"] for r in rows)),
                      "calls": sum(len(r["child_calls"]) for r in rows), "licence": LICENCE,
                      "distinct_tools_available": len({t for r in rows for t in r["tools_available"]})}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
