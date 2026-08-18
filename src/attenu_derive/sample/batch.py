"""
G3 volume driver (Rafael's go, 2026-08-18): sample MANY unsampled repos with the deepagents fan-out workload
under a hard USD ceiling, tracking events + spend from the manifests, mirroring as it goes.

    python -m attenu_derive.sample.batch --repos <file: one org/repo per line> --max-usd 120 --shard 0/2

Breadth by construction (PM decision: G3 volume is bought as breadth): every repo is new, ten task variants per
repo, per-repo cap. Stops when the ceiling would be breached, the target real-event count is reached, or the
list is exhausted. Progress: data/reports/g3-progress-<shard>.json (+ stdout lines).
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import time
from pathlib import Path

from attenu_derive.sample import run_deepagents


def real_events(out: Path) -> int:
    n = 0
    for p in glob.glob(str(out / "corpus" / "*.jsonl")):
        for l in open(p):
            if l.strip() and json.loads(l).get("parent_node") is not None:
                n += 1
    return n


def clone(repo: str, work: Path) -> Path | None:
    dst = work / repo.replace("/", "__")
    if dst.exists():
        return dst
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", f"https://github.com/{repo}.git", str(dst)], capture_output=True, text=True)
    return dst if r.returncode == 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True); ap.add_argument("--work", required=True, help="clone dir (scratch)"); ap.add_argument("--out", default="data")
    ap.add_argument("--max-usd", type=float, required=True, help="HARD ceiling for this driver (cache-aware estimate)")
    ap.add_argument("--per-repo-max-usd", type=float, default=6.0); ap.add_argument("--max-input-tokens", type=int, default=200_000)
    ap.add_argument("--target-events", type=int, default=2000); ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--shard", default="0/1", help="i/n: take every n-th repo starting at i"); ap.add_argument("--mirror-every", type=int, default=3)
    args = ap.parse_args(argv)
    i, n = (int(x) for x in args.shard.split("/"))
    repos = [r.strip() for r in Path(args.repos).read_text().splitlines() if r.strip() and not r.startswith("#")][i::n]
    work = Path(args.work); work.mkdir(parents=True, exist_ok=True); out = Path(args.out)
    prog = out / "reports" / f"g3-progress-{i}of{n}.json"; prog.parent.mkdir(parents=True, exist_ok=True)
    spent = 0.0; done = []; t0 = time.time()
    for k, repo in enumerate(repos):
        events_now = real_events(out)
        if events_now >= args.target_events:
            print(f"[batch] target reached: {events_now} real events"); break
        if spent + min(args.per_repo_max_usd, 1.0) > args.max_usd:
            print(f"[batch] ceiling: spent {spent:.2f} of {args.max_usd}"); break
        path = clone(repo, work)
        if path is None:
            print(f"[batch] clone failed: {repo}"); continue
        repo_cap = min(args.per_repo_max_usd, args.max_usd - spent)
        print(f"[batch] {k + 1}/{len(repos)} {repo} | spent {spent:.2f} | events {events_now}", flush=True)
        try:
            run_deepagents.main(["--repo", str(path), "--project", repo.split("/")[-1], "--out", str(out), "--model", args.model,
                                 "--volume", "--max-input-tokens", str(args.max_input_tokens), "--max-usd", f"{repo_cap:.3f}"])
        except Exception as exc:                # noqa: BLE001 — one repo must not kill the batch
            print(f"[batch] {repo} failed: {exc!r}")
        mf = sorted(glob.glob(str(out / "runs" / "*" / "manifest.json")), key=lambda p: Path(p).stat().st_mtime)
        m = json.loads(Path(mf[-1]).read_text()) if mf else {}
        cost = float(m.get("totals", {}).get("est_cost_usd") or 0); ev = int(m.get("totals", {}).get("delegation_events") or 0)
        spent += cost; done.append({"repo": repo, "run_id": m.get("run_id"), "events": ev, "usd": round(cost, 3), "stopped_by": m.get("stopped_by")})
        prog.write_text(json.dumps({"shard": args.shard, "spent_usd": round(spent, 3), "max_usd": args.max_usd, "repos_done": len(done),
                                    "real_events_total": real_events(out), "elapsed_min": round((time.time() - t0) / 60, 1), "repos": done}, indent=2))
        print(f"[batch] {repo}: {ev} events, USD {cost:.3f} | total spent {spent:.2f}", flush=True)
        if args.mirror_every and (k + 1) % args.mirror_every == 0:
            subprocess.run(["./scripts/mirror.sh", "push"], capture_output=True)
    subprocess.run(["./scripts/mirror.sh", "push"], capture_output=True)
    print(json.dumps({"spent_usd": round(spent, 3), "repos_done": len(done), "real_events_total": real_events(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
