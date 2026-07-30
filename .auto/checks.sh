#!/bin/bash
# Autoresearch backpressure checks: coverage guard + ruff.
# Runs after a PASSING benchmark. Output kept minimal (errors only).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Coverage guard: totals must not decrease vs baseline -------------------
if [ -f .auto/baseline_coverage.json ] && [ -f .auto/coverage.json ]; then
    python3 - <<'EOF'
import json, sys

with open(".auto/baseline_coverage.json") as f:
    base = json.load(f)["totals"]
with open(".auto/coverage.json") as f:
    cur = json.load(f)["totals"]

problems = []
for key in ("covered_lines", "covered_branches"):
    if cur[key] < base[key]:
        problems.append(f"{key}: {cur[key]} < baseline {base[key]}")

if problems:
    print("COVERAGE GUARD FAILED:")
    for p in problems:
        print("  " + p)
    # Show which files lost coverage to help the agent
    with open(".auto/baseline_coverage.json") as f:
        bfiles = json.load(f)["files"]
    with open(".auto/coverage.json") as f:
        cfiles = json.load(f)["files"]
    dips = []
    for fn, b in bfiles.items():
        c = cfiles.get(fn)
        if c is None:
            dips.append((fn, b["summary"]["covered_lines"], 0))
            continue
        bl = b["summary"]["covered_lines"] + b["summary"].get("covered_branches", 0)
        cl = c["summary"]["covered_lines"] + c["summary"].get("covered_branches", 0)
        if cl < bl:
            dips.append((fn, bl, cl))
    dips.sort(key=lambda d: d[1] - d[2], reverse=True)
    for fn, bl, cl in dips[:15]:
        print(f"    {fn}: {bl} -> {cl}")
    sys.exit(1)

print("coverage guard OK "
      f"(lines {cur['covered_lines']}>={base['covered_lines']}, "
      f"branches {cur['covered_branches']}>={base['covered_branches']})")
EOF
else
    echo "no baseline coverage yet — guard skipped"
fi

# --- Ruff on tests ----------------------------------------------------------
uv run ruff check tests/ 2>&1 | tail -20
echo "checks OK"
