# Ideas backlog

(Promising but deferred optimization ideas go here. Prune when tried/stale.)

- Legacy Hub/scope API tests (test_basics.py, test_scope.py) vs
  tests/new_scopes_compat/ — the latter replays scope behaviors through
  new-style APIs; some assertions may be exact duplicates.
- Look for hand-rolled loops over inputs that could be one parametrized test.
- tests/test_basics.py has many small "processors"/"breadcrumbs" tests that
  may overlap heavily in setup + covered lines.
