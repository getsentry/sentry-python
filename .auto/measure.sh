#!/bin/bash
# Autoresearch benchmark: run the common test suite, emit METRIC lines.
# Primary metric: test_count (lower is better).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TOX_ENV_DIR=".tox/py3.14-common"
PY="$TOX_ENV_DIR/bin/python"

# --- Ensure the tox env exists ---------------------------------------------
if [ ! -x "$PY" ]; then
    echo "Provisioning tox env py3.14-common (one-time)..." >&2
    uv run tox -e py3.14-common --notest >&2
fi

# --- Fast pre-check: syntax of all test files (<1s after first run) ---------
python3 -m compileall -q tests/ >/dev/null

# --- Run the suite ----------------------------------------------------------
# Mirrors CI: tox py3.14-common runs `python -m pytest tests` with
# PYTEST_ADDOPTS="--ignore=tests/test_shadowed_module.py" and
# -W error::pytest.PytestUnraisableExceptionWarning.
START=$(python3 -c 'import time; print(time.time())')
set +e
"$PY" -m pytest tests \
    -W error::pytest.PytestUnraisableExceptionWarning \
    --ignore=tests/test_shadowed_module.py \
    --cov-report=json:.auto/coverage.json \
    --junitxml=.auto/junit.xml -o junit_suite_name=common \
    > .auto/last_run.log 2>&1
PYTEST_EXIT=$?
set -e
END=$(python3 -c 'import time; print(time.time())')

echo "=== pytest tail (exit=$PYTEST_EXIT) ==="
tail -n 12 .auto/last_run.log

# --- Metrics ----------------------------------------------------------------
TEST_COUNT=$(grep -o '<testcase ' .auto/junit.xml | wc -l | tr -d ' ')
FAILED=$(grep -o 'failures="[0-9]*"' .auto/junit.xml | head -1 | grep -o '[0-9]*' || echo 0)
ERRORS=$(grep -o 'errors="[0-9]*"' .auto/junit.xml | head -1 | grep -o '[0-9]*' || echo 0)
SKIPPED=$(grep -o 'skipped="[0-9]*"' .auto/junit.xml | head -1 | grep -o '[0-9]*' || echo 0)
RUNTIME=$(python3 -c "print(round($END - $START, 2))")

COV=$(python3 - <<'EOF'
import json
try:
    with open(".auto/coverage.json") as f:
        t = json.load(f)["totals"]
    print(f'{t["covered_lines"]} {t["covered_branches"]} {round(t["percent_covered"], 3)}')
except Exception:
    print("0 0 0.0")
EOF
)
read -r COV_LINES COV_BRANCHES COV_PCT <<< "$COV"

echo "METRIC test_count=$TEST_COUNT"
echo "METRIC runtime_s=$RUNTIME"
echo "METRIC covered_lines=$COV_LINES"
echo "METRIC covered_branches=$COV_BRANCHES"
echo "METRIC coverage_pct=$COV_PCT"
echo "METRIC failed=$FAILED"
echo "METRIC errors=$ERRORS"
echo "METRIC skipped=$SKIPPED"

# Nonzero pytest exit => experiment crashed (caller treats as crash/discard)
exit "$PYTEST_EXIT"
