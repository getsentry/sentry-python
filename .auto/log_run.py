#!/usr/bin/env python3
"""Append a run entry to .auto/log.jsonl in the pi-autoresearch extension format.

Usage:
  python3 .auto/log_run.py --status keep --metric 1234 \
      --metrics '{"runtime_s": 42.1, "covered_lines": 9000}' \
      --description "merged redundant scope tests" \
      --asi '{"file": "tests/test_scope.py", "delta": -12}'
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

LOG = Path(__file__).parent / "log.jsonl"


def next_run_number() -> int:
    n = 0
    if LOG.exists():
        for line in LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry.get("run"), int):
                n = max(n, entry["run"])
    return n + 1


def confidence(metric: float, status: str):
    """Best improvement as a multiple of the session noise floor
    (stdev of kept-run primary metrics)."""
    kept = []
    best = None
    if LOG.exists():
        for line in LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "run" not in e or not isinstance(e.get("metric"), (int, float)):
                continue
            if e.get("status") == "keep":
                kept.append(e["metric"])
                best = e["metric"] if best is None else min(best, e["metric"])
    if best is None or len(kept) < 3:
        return None
    mean = sum(kept) / len(kept)
    var = sum((m - mean) ** 2 for m in kept) / (len(kept) - 1)
    noise = var**0.5
    if noise < 1e-9:
        return None
    improvement = best - metric if status == "keep" else 0.0
    return round(improvement / noise, 2)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--status", required=True, choices=["keep", "discard", "crash", "checks_failed"]
    )
    p.add_argument("--metric", required=True, type=float)
    p.add_argument("--metrics", default="{}")
    p.add_argument("--description", required=True)
    p.add_argument("--asi", default="{}")
    args = p.parse_args()

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    entry = {
        "run": next_run_number(),
        "commit": commit,
        "metric": args.metric,
        "metrics": json.loads(args.metrics),
        "status": args.status,
        "description": args.description,
        "timestamp": int(time.time() * 1000),
        "segment": 0,
        "confidence": confidence(args.metric, args.status),
        "asi": json.loads(args.asi),
    }
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"logged run {entry['run']} status={entry['status']} metric={entry['metric']}")


if __name__ == "__main__":
    main()
