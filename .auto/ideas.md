# Ideas backlog

Status after 13 runs: 2720 -> 2289 (-15.8%), coverage flat (9779/2781).

## Exhausted
- Cross-product matrix curation (transport sync/async, proxy, sample_rand grids)
- Equivalence-class row pruning (env_to_bool, invalid sampler tables, safe_repr)
- Exact-duplicate test bodies (only 3 groups found, all legit twins)
- new_scopes_compat / feature_flags / span_streaming twins — reviewed, kept

## Remaining (needs a requirements decision)
- ~1600 tests in the greedy deletable set are "incidentally covered spec tests":
  deleting them keeps line/branch coverage but removes the fine-grained
  behavioral spec (failures localize worse, refactors lose safety net).
  Examples: test_scope.py API unit tests, parser tables, config-resolution
  tables. NOT pursued under the current assertion-preservation discipline.
- tests/integrations/** (out of scope this session): 381 deletable testcases
  in the py3.14-attributed subset; wsgi/transport-style matrices exist there
  too (wsgi test file alone ~14 big redundant cases).

## Petty (skipped, ~1-3 tests each)
- test_transport_num_pools: (2,2) row duplicates default-value branch
- test_should_propagate_trace: escaped-regex row is a literal duplicate of
  the unescaped one; one localhost substring row redundant
