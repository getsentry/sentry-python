---
name: sdk-review
description: "Review sentry-python changes for violations of Sentry SDK contracts: host-app safety, data-collection gates, supported versions, public API, and Python-specific rules."
allowed-tools: Read Grep Glob
---
Review only the changed code. Report a finding only if you can point to the exact line and show how it breaks a user's app, build or data. Most hunks break no SDK contract, so no findings is the normal result. Generic logic bugs belong to `code-review`; this skill only covers SDK contracts.

## Core SDK rules (same in every SDK repo)

1. **Never break the host app** (high). SDK code that can raise, block, or change the user's behavior: exceptions, return values, signatures, job or request flow.
2. **Data collected without its gate** (high). Bodies, headers, cookies, user info, `gen_ai` inputs/outputs or DB results recorded without the PII / data-collection option. Header and cookie names match case-insensitively.
3. **Supported versions** (high). Language, runtime, target or dependency APIs newer than the repo's declared minimum, used without a guard.
4. **Public API** (medium). Breaking public changes without a deprecation path, or internals exposed as public API.

## Do not report (same in every SDK repo)

- Style, naming, refactors, or anything the repo's linters, type checkers or analyzers catch.
- Pre-existing code the diff does not change.
- Behavior that matches the spec, Relay or other SDKs, or a trade-off the PR or its threads explain.
- Follow-ups the PR says are handled in a stacked PR or the major branch.
- Tests, samples and examples, unless they can't fail or they teach wrong SDK usage.
- Edge cases that need misuse or very unlikely timing.

## Python rules

5. **Missing internal-exception guard** (high). New patch or hook code outside `capture_internal_exceptions()`, or code that reads framework objects that can be missing or `None` (for example an unmatched route).
6. **Brittle monkeypatch** (medium). A patch spells out arguments instead of `*args, **kwargs`, or swallows or re-raises the user's exception differently.
7. **Span or check-in not finished on every path** (medium). Exceptions, early returns, and streams closed early or never consumed.
8. **Version floor** (high). Minimums come from `python_requires` and `_MIN_VERSIONS` in `sentry_sdk/integrations/__init__.py`.
