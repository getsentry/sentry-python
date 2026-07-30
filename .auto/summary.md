# Autoresearch final summary: fewer common-suite tests, same coverage

**Branch**: `autoresearch/less-tests-common-20260730`
**Date**: 2026-07-30
**Result**: 2720 → **2289 tests** (−431, −15.8%) with coverage **exactly flat**
(covered_lines 9779, covered_branches 2781 — deterministic across all runs)
and suite runtime 152s → ~120s (−21%).

## Method

1. Baseline: full `py3.14-common` suite (pytest + branch coverage), 2 runs to
   confirm determinism. Guard: `covered_lines` AND `covered_branches` >= baseline
   (`.auto/checks.sh`), plus `ruff check tests/`.
2. Attribution map: one suite run with `--cov-context=test` (excluding
   `tests/profiler/test_continuous_profiler.py` and 16 context-sensitive tests,
   which made the map conservative), analyzed via the coverage sqlite DB
   (`.auto/analyze.py`) → greedy maximal deletable set of 2011 tests whose
   removal keeps every attributed line/arc covered.
3. Iterations: one idea per run, full suite + guard each time, keep/discard
   via git. 13 runs: 11 keeps, 0 discards, 1 checks_failed (caught a dropped
   fall-through branch in spotlight precedence; fixed and re-kept).

## What was removed (by pattern)

| Pattern | Where | Tests |
|---|---|---|
| Permanently-skipped dead tests | test_basics, test_client | −6 |
| Cross-product matrix → curated subset | test_transport_works (192→24), _async (96→12) | −252 |
| Case-permutation equivalence class | test_env_to_bool (64→22) | −38 |
| http2 multiplier → representatives | test_proxy (42→24), test_socks_proxy (18→10) | −26 |
| Precedence tables (option×env), one row per arm | test_debug_option (30→8), test_spotlight_option (12→9) | −25 |
| Grid → boundary cases (sample_rand < sample_rate) | 4 tests × (20→6) | −56 |
| Wrong-type equivalence class | invalid sampler tables (9→5 ×2), warns_on_invalid_sample_rate (9→5) | −12 |
| Grids → corner set | safe_repr_non_printable (12→4) | −8 |
| Attribute-irrelevant duplicate rows | IGNORE_SPANS_CASES (−4 ×2 tests) | −8 |

In every matrix reduction, all VALUES of every dimension are still exercised
and all assertions are preserved; the compression/precedence-relevant
dimensions stay fully crossed.

## What was deliberately kept

Tests whose rows/cases are each a distinct behavioral spec: parser tables
(parse_version, sanitize_url, rate limits), config-resolution tables,
matcher tables (ignore_spans, should_propagate_trace), API unit tests
(test_scope.py), deprecation pins, async/sync twins (contextvars under
asyncio), `_span_streaming` twins (different pipeline), new_scopes_compat
(legacy API contracts). Deleting these would keep line/branch coverage but
remove the fine-grained spec — user decision: keep.

## Artifacts

- `.auto/prompt.md` — playbook incl. reusable reduction patterns
- `.auto/log.jsonl` — all 13 runs with metrics + ASI
- `.auto/ideas.md` — deferred work (integrations scope, petty prunes)
- `.auto/measure.sh` / `.auto/checks.sh` / `.auto/analyze.py` — rerunnable
- `.auto/baseline_coverage.json` — the coverage guard baseline

## Resume / next steps

- Integrations scope (`tests/integrations/**`): same matrix opportunities
  exist (e.g. wsgi tests); needs per-integration tox envs.
- To re-verify: `./.auto/measure.sh && ./.auto/checks.sh`.
