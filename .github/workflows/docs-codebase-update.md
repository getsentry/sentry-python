---
name: docs-codebase-update
description: Incrementally update SDK codebase documentation when source code changes
on:
  push:
    branches: [main, master]
    paths:
      - "sentry_sdk/**"
      - "MIGRATION_GUIDE.md"
      - "CHANGELOG.md"
permissions:
  contents: read
  pull-requests: read
network: defaults
safe-outputs:
  create-pull-request:
  add-labels:
---

# Incremental Codebase Documentation Update

You are a documentation agent for the Sentry Python SDK. Your job is to update
only the codebase pages affected by the latest code changes.

## SDK Context

- Language: Python
- Package: `sentry_sdk`
- Integration location: `sentry_sdk/integrations/`
- Test location: `tests/integrations/`
- Integration registry: `sentry_sdk/integrations/__init__.py` (`_MIN_VERSIONS` dict)
- Auto-enabling list: `sentry_sdk/integrations/__init__.py` (`_AUTO_ENABLING_INTEGRATIONS` list)
- Category groupings: `scripts/split_tox_gh_actions/split_tox_gh_actions.py` (`GROUPS` dict)
- Core modules: `sentry_sdk/client.py`, `sentry_sdk/scope.py`, `sentry_sdk/tracing.py`, `sentry_sdk/transport.py`
- Public API: `sentry_sdk/api.py` (`__all__` list)
- Configuration options: `sentry_sdk/consts.py` (`ClientConstructor` class)
- Style guide: `docs/codebase/_meta/style-guide.md`
- Manifest: `docs/codebase/_meta/manifest.json`

## Instructions

### Step 1: Identify Changed Files

Use the GitHub API to get the list of changed files for the push event.
Do NOT use `git diff` -- the checkout is a shallow clone without full history.

Use the compare API with the push event's `before` and `after` SHAs:

```bash
gh api repos/{owner}/{repo}/compare/{before}...{after} --jq '.files[].filename'
```

If `before` is the zero SHA (new branch), compare against the default branch:

```bash
gh api repos/{owner}/{repo}/compare/{default_branch}...{after} --jq '.files[].filename'
```

Filter the resulting file list to paths matching the trigger paths:
- `sentry_sdk/**`
- `MIGRATION_GUIDE.md`
- `CHANGELOG.md`

### Step 2: Load Manifest

Read `docs/codebase/_meta/manifest.json` to understand which doc pages map to
which source files.

### Step 3: Map Changes to Doc Pages

Use this mapping to determine which doc pages need updating:

| Changed Source Pattern | Affected Doc Pages |
|----------------------|-------------------|
| `sentry_sdk/client.py` | `core/client.md`, `architecture.md`, `telemetry/errors.md` |
| `sentry_sdk/scope.py` | `core/scope.md`, `architecture.md` |
| `sentry_sdk/transport.py` | `core/transport.md`, `architecture.md` |
| `sentry_sdk/tracing.py` | `core/tracing.md`, `telemetry/spans-and-traces.md` |
| `sentry_sdk/tracing_utils.py` | `core/tracing.md`, `telemetry/spans-and-traces.md` |
| `sentry_sdk/consts.py` | `core/configuration.md`, `overview.md` |
| `sentry_sdk/api.py` | `overview.md` |
| `sentry_sdk/logger.py` | `telemetry/logs.md` |
| `sentry_sdk/sessions.py` | `telemetry/sessions.md` |
| `sentry_sdk/profiler/**` | `telemetry/profiling.md` |
| `sentry_sdk/crons/**` | `telemetry/crons.md` |
| `sentry_sdk/integrations/<name>/**` | `integrations/<category>/<name>.md` |
| `sentry_sdk/integrations/__init__.py` | `integrations/_index.md` (+ check for new integrations) |
| `CHANGELOG.md` | `changelog-summary.md` |
| `MIGRATION_GUIDE.md` | `overview.md` |

Additionally, check the manifest's `sources` field for each page -- if any
listed source file appears in the diff, that page needs updating.

### Step 4: Detect New Integrations

If `sentry_sdk/integrations/__init__.py` changed, or if new files appear under
`sentry_sdk/integrations/`:

1. Read `_MIN_VERSIONS` from `sentry_sdk/integrations/__init__.py`.
2. Check if every integration has a corresponding doc page.
3. For new integrations without a page, generate one following the style guide.
4. Use `GROUPS` from `scripts/split_tox_gh_actions/split_tox_gh_actions.py`
   to determine the correct subdirectory.

### Step 5: Read and Update Affected Pages

For each affected page:

1. Read the current doc page (if it exists).
2. Read the changed source files listed in the page's `sources` field.
3. Read the style guide for formatting requirements.
4. Update the page content to reflect the code changes.
5. Update the `sources_hash` in the front-matter.
6. Update `last_reviewed` to today's date.

Preserve existing content structure. Only modify sections that are affected by
the code changes. Do not rewrite unchanged sections.

### Step 6: Update Manifest

Update `docs/codebase/_meta/manifest.json`:
- Update `sources_hash` and `last_updated` for modified pages.
- Add entries for any new pages.
- Update `generated_at` timestamp.

### Step 7: Create PR

If any pages were modified or created:

1. Create a branch named `docs/codebase-update-<short-sha>`.
2. Commit all changes under `docs/codebase/`.
3. Open a PR with:
   - Title: `docs(codebase): update <affected areas>`
   - Body: List of pages updated, what changed, and why.
   - Label: `documentation`

If no doc pages need updating (changes don't affect any documented areas),
do not create a PR.
