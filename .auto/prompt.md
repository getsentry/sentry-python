# Autoresearch: fewer common-suite tests, same coverage

## Objective

Reduce the number of collected tests in the sentry-python **common test suite**
(`tests/`, excluding `tests/integrations/`) **without reducing code coverage**
of `sentry_sdk/` and without losing meaningful assertions.

The value is CI time and maintenance burden. The guardrails are:
1. All remaining tests pass.
2. Coverage totals do not decrease: `covered_lines` AND `covered_branches`
   (branch coverage, of `sentry_sdk/`) must stay >= baseline.
3. `ruff check tests/` is clean.

Reductions must come from **true redundancy**, e.g.:
- Tests that are exact/near duplicates of another test (same code paths, same
  assertions, no new branch coverage).
- Tests superseded by a broader test that covers the same paths plus more.
- N near-identical tests merged into one `@pytest.mark.parametrize` case
  (ALL original assertions preserved).
- Tests of trivial behavior already exercised as a side effect of broader tests
  (only if deleting them does not drop any covered line/branch).

Do NOT:
- Weaken or delete assertions just to make merging easier.
- Delete tests whose value is not visible in coverage (e.g. asserting the
  ABSENCE of events/spans, exact payload values, ordering, warning text)
  unless an equivalent assertion exists elsewhere.
- Merge tests that test conceptually different behaviors into an unreadable
  mega-test. Clarity counts.

## Metrics

- **Primary**: `test_count` (count, lower is better) — collected+executed test
  cases (parametrized cases count individually), from JUnit XML.
- **Secondary**: `runtime_s` (suite wall time), `covered_lines`,
  `covered_branches`, `coverage_pct`, `failed`, `skipped`.

## How to Run

- Benchmark: `./.auto/measure.sh` — runs the common suite with coverage,
  prints `METRIC name=value` lines, saves full output to `.auto/last_run.log`,
  JUnit to `.auto/junit.xml`, coverage JSON to `.auto/coverage.json`.
  Exits nonzero if pytest fails.
- Checks: `./.auto/checks.sh` — coverage guard vs `.auto/baseline_coverage.json`
  + `ruff check tests/`. Exits nonzero on failure.

### Emulated tool loop (extension tools not loaded in this session)

The pi-autoresearch extension is not active, so the loop is driven manually:

1. Make a focused change to test files (one idea per iteration).
2. `./.auto/measure.sh` — if it exits nonzero → status `crash`.
3. Otherwise `./.auto/checks.sh` — if it exits nonzero → status `checks_failed`.
4. Compare `test_count` to best kept value:
   - lower → `keep`: `git add tests .auto/prompt.md .auto/ideas.md && git commit`
   - equal/higher → `discard`: `git restore --source=HEAD --worktree --staged tests && git clean -fd tests`
5. Log EVERY run: `python3 .auto/log_run.py --status <keep|discard|crash|checks_failed> --metric <test_count> --metrics '{"runtime_s":..,"covered_lines":..,"covered_branches":..}' --description "..." --asi '{"key":"value"}'`
6. Update "What's Been Tried" in this file after notable outcomes.

Baseline runs: run measure.sh twice before accepting the baseline to gauge
flakiness of runtime and coverage totals.

## Files in Scope

- `tests/*.py` (top-level test modules; biggest: test_ai_monitoring.py 2083 LOC,
  test_client.py 1873, test_basics.py 1243, test_scope.py 1112,
  test_utils.py 1095, test_transport.py 1041)
- `tests/tracing/`, `tests/utils/`, `tests/profiler/`, `tests/new_scopes_compat/`
  (note: new_scopes_compat tests the SAME scope behaviors through new APIs —
  some overlap with legacy-API tests may be intentional API-compat coverage;
  only merge/delete if truly redundant)
- `tests/conftest.py` — CAUTION: shared with the `gevent` tox env (also runs
  `tests/`). Fixture changes must not break gevent. Prefer not touching it.

## Off Limits

- `sentry_sdk/**` — the SDK source. Never modify.
- `tests/integrations/**` — out of scope for now (separate tox envs).
- `tests/test_shadowed_module.py` — excluded from common; run by its own env.
- `tests/test_ai_integration_deactivation.py` — run by its own env too
  (integration_deactivation). Leave alone unless it affects common counts
  (it is collected by common as well — verify from baseline JUnit).
- `pyproject.toml` (pytest addopts, coverage config), `tox.ini`, `scripts/`,
  `.github/`, `tests/test.key`, `tests/test.pem`.

## Constraints

- Remaining tests must pass: `pytest` exit code 0.
- Coverage guard: `covered_lines` and `covered_branches` in
  `.auto/coverage.json` must both be >= the baseline values.
- `ruff check tests/` must pass.
- No new dependencies. No changes to pytest/coverage configuration.
- Deleting a test is only justified if its covered lines+branches are covered
  by other tests AND its assertions are either redundant or preserved elsewhere.

## Flakiness notes

- If a run fails checks due to a small coverage dip in an UNRELATED file,
  re-run measure.sh once before discarding — some tests are timing-sensitive.
- `runtime_s` is noisy; it is informational only, never a keep/discard reason.

## What's Been Tried

(nothing yet — baseline pending)
