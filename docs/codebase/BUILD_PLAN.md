# BUILD_PLAN: Internal Documentation via GitHub Agentic Workflows

> **Portable blueprint** -- copy this file to any Sentry SDK repo and follow it.
> Only the SDK-specific values (marked `{{...}}`) need to change.

---

## How This Relates to Other Sentry Docs

Sentry SDKs have four documentation surfaces with distinct audiences:

- **[docs.sentry.io/platforms/\*](https://docs.sentry.io/)** tells users *what to do* -- setup guides, config options, API usage.
- **[develop.sentry.dev/sdk/](https://develop.sentry.dev/sdk/)** tells SDK authors *what to build* -- protocol spec, envelope format, required behaviors.
- **API reference** (Sphinx, TypeDoc, Javadoc, etc.) tells developers *what the API surface looks like* -- auto-generated from docstrings/annotations. Lists signatures, parameters, return types.
- **`docs/codebase/`** (this) explains *what was built and how it works* -- architecture, data flow, how modules connect, and why. Generated from full source analysis, not just docstrings. Aimed at SDK contributors and maintainers.

---

## What This Builds

Two GitHub Agentic Workflows that automatically generate and maintain
developer-facing internal documentation (`docs/codebase/`) for a Sentry SDK:

| Workflow | Trigger | Scope |
|----------|---------|-------|
| `docs-codebase-refresh` | Manual (`workflow_dispatch`) | Full regeneration of every doc page |
| `docs-codebase-update` | Push to main/master (path-filtered) | Incremental update of affected pages only |

Both workflows create PRs (never direct commits) via `safe-outputs: create-pull-request`.

---

## Prerequisites

- `gh` CLI installed
- `gh aw` extension installed: `gh extension install github/gh-aw`
- Repository initialized for agentic workflows: `gh aw init`

---

## SDK Context Block (the only thing that changes per SDK)

Each workflow `.md` file contains an **SDK Context** section at the top of the
agent instructions. This is the **sole portability surface** -- everything else
(style guide, page templates, manifest schema, agent instructions) stays the same.

```markdown
## SDK Context
- Language: {{language}}                          # e.g. Python, JavaScript, Java, Ruby, Go
- Package: {{package}}                            # e.g. sentry_sdk, @sentry/node, io.sentry
- Integration location: {{integration_path}}      # e.g. sentry_sdk/integrations/
- Test location: {{test_path}}                    # e.g. tests/integrations/
- Integration registry: {{registry_file}}         # file + symbol that lists all integrations
- Auto-enabling list: {{auto_enable_file}}        # file + symbol for auto-enabled integrations
- Category groupings: {{groups_file}}             # file + symbol that categorizes integrations
- Core modules: {{core_files}}                    # comma-separated list of core source files
- Public API: {{api_file}}                        # file + symbol that exports the public surface
- Configuration options: {{config_file}}          # file + type/class holding all SDK options
- Style guide: docs/codebase/_meta/style-guide.md   # (constant across SDKs)
- Manifest: docs/codebase/_meta/manifest.json       # (constant across SDKs)
```

### Python SDK values (reference)

```markdown
## SDK Context
- Language: Python
- Package: sentry_sdk
- Integration location: sentry_sdk/integrations/
- Test location: tests/integrations/
- Integration registry: sentry_sdk/integrations/__init__.py (_MIN_VERSIONS dict)
- Auto-enabling list: sentry_sdk/integrations/__init__.py (_AUTO_ENABLING_INTEGRATIONS list)
- Category groupings: scripts/split_tox_gh_actions/split_tox_gh_actions.py (GROUPS dict)
- Core modules: sentry_sdk/client.py, sentry_sdk/scope.py, sentry_sdk/tracing.py, sentry_sdk/transport.py
- Public API: sentry_sdk/api.py (__all__ list)
- Configuration options: sentry_sdk/consts.py (ClientConstructor class)
```

---

## Directory Structure

```
{{repo_root}}/
  .gitattributes                                  # marks docs/codebase/** as linguist-generated
  .github/workflows/
    docs-codebase-refresh.md                          # full refresh workflow (source)
    docs-codebase-refresh.lock.yml                    # compiled Actions workflow (generated)
    docs-codebase-update.md                           # incremental update workflow (source)
    docs-codebase-update.lock.yml                     # compiled Actions workflow (generated)
  docs/codebase/
    BUILD_PLAN.md                                 # this file
    _meta/
      manifest.json                               # page index, source mappings, content hashes
      style-guide.md                              # formatting rules (shared across SDKs)
    overview.md                                   # SDK purpose, installation, init, config summary
    architecture.md                               # internal architecture diagram
    core/
      client.md                                   # client init and event processing
      scope.md                                    # scope management
      transport.md                                # envelope transport, rate limiting
      tracing.md                                  # spans, transactions, sampling, propagation
      data-model.md                               # events, breadcrumbs, contexts
      configuration.md                            # all SDK options
    telemetry/
      errors.md                                   # error capture pipeline
      spans-and-traces.md                         # distributed tracing
      logs.md                                     # structured logs
      profiling.md                                # profiler subsystem
      sessions.md                                 # release health
      crons.md                                    # check-in monitoring
    integrations/
      _index.md                                   # integration system overview
      web/django.md, flask.md, ...                # web framework integrations
      ai/openai.md, anthropic.md, ...             # AI/LLM integrations
      databases/redis.md, sqlalchemy.md, ...      # database integrations
      tasks/celery.md, arq.md, ...                # task queue integrations
      cloud/aws-lambda.md, gcp.md, ...            # cloud/serverless integrations
      graphql/ariadne.md, strawberry.md, ...      # GraphQL integrations
      network/httpx.md, grpc.md, ...              # HTTP/network client integrations
      flags/launchdarkly.md, openfeature.md, ...  # feature flag integrations
      misc/logging.md, loguru.md, ...             # other integrations
    faq.md
    changelog-summary.md
```

---

## Implementation Steps

### Step 1: Create `docs/codebase/_meta/style-guide.md`

Copy from the Python SDK reference. The style guide is **SDK-agnostic** except:
- The front-matter example uses `sdk: "{{sdk_id}}"` and `owners: ["{{owners_team}}"]`
- The category mapping table has an SDK-agnostic convention section (top) and
  an SDK-specific reference section (bottom) -- update the reference section
- The code fence language identifier matches `{{language}}`

### Step 2: Create `docs/codebase/_meta/manifest.json`

```json
{
  "version": 1,
  "sdk": "{{sdk_id}}",
  "generated_at": null,
  "pages": {}
}
```

### Step 3: Create `.github/workflows/docs-codebase-refresh.md`

Frontmatter:

```yaml
---
name: docs-codebase-refresh
description: Full refresh of SDK codebase documentation (manual trigger only)
on:
  workflow_dispatch:
permissions:
  contents: read
  pull-requests: read
network: defaults
safe-outputs:
  create-pull-request:
  add-labels:
---
```

Body contains:
1. **SDK Context** block (fill in `{{...}}` values)
2. **Step-by-step agent instructions:**
   - Discover all sources (read registry, groups, config, API surface)
   - Read the style guide
   - Generate core pages (client, scope, transport, tracing, data-model, configuration)
   - Generate telemetry pages (errors, spans, logs, profiling, sessions, crons)
   - Generate integration pages (one per registered integration, categorized)
   - Generate supplementary pages (FAQ, changelog summary)
   - Update manifest
   - Validate (every integration has a page, all front-matter valid, no TODOs)
   - Create PR if changes exist

### Step 4: Compile refresh workflow

```bash
gh aw compile .github/workflows/docs-codebase-refresh.md
```

Must produce **0 errors, 0 warnings**.

### Step 5: Create `.github/workflows/docs-codebase-update.md`

Frontmatter:

```yaml
---
name: docs-codebase-update
description: Incrementally update SDK codebase documentation when source code changes
on:
  push:
    branches: [main, master]
    paths:
      - "{{package_path}}/**"
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
```

Body contains:
1. **SDK Context** block (same values as refresh)
2. **Step-by-step agent instructions:**
   - GitHub compare API to find all changed files across the push (avoids shallow clone issues)
   - Load manifest to map source files to doc pages
   - Change-to-page mapping table (SDK-specific -- maps each core file to its doc pages)
   - Detect new integrations if registry file changed
   - Read and update only affected pages
   - Update manifest
   - Create PR if changes exist

### Step 6: Compile update workflow

```bash
gh aw compile .github/workflows/docs-codebase-update.md
```

Must produce **0 errors, 0 warnings**.

### Step 7: Create/update `.gitattributes`

Add (the `lock.yml` line is auto-added by `gh aw compile`):

```
docs/codebase/** linguist-generated=true
```

---

## Porting Checklist

When adapting this to a new SDK, replace these values:

| Variable | Description | Example (Python) | Example (JavaScript) |
|----------|-------------|-------------------|----------------------|
| `{{sdk_id}}` | SDK identifier | `python` | `javascript` |
| `{{language}}` | Programming language | `Python` | `JavaScript` |
| `{{package}}` | Package name | `sentry_sdk` | `@sentry/node` |
| `{{package_path}}` | Source path for trigger | `sentry_sdk` | `packages/node/src` |
| `{{integration_path}}` | Integration modules | `sentry_sdk/integrations/` | `packages/node/src/integrations/` |
| `{{test_path}}` | Integration tests | `tests/integrations/` | `packages/node/test/integrations/` |
| `{{registry_file}}` | Integration registry | `sentry_sdk/integrations/__init__.py` | `packages/node/src/integrations/index.ts` |
| `{{auto_enable_file}}` | Auto-enable list | (same as registry) | (same as registry) |
| `{{groups_file}}` | Category groupings | `scripts/split_tox_gh_actions/...` | SDK-specific or `null` |
| `{{core_files}}` | Core module list | `client.py, scope.py, ...` | `client.ts, scope.ts, ...` |
| `{{api_file}}` | Public API surface | `sentry_sdk/api.py` | `packages/node/src/index.ts` |
| `{{config_file}}` | Config options type | `sentry_sdk/consts.py` | `packages/types/src/options.ts` |
| `{{owners_team}}` | GitHub CODEOWNERS team | `@getsentry/owners-python-sdk` | `@getsentry/owners-js-sdk` |

### What stays the same across SDKs

- `docs/codebase/_meta/style-guide.md` (except language-specific reference section)
- `docs/codebase/_meta/manifest.json` schema
- `.gitattributes` pattern
- Workflow frontmatter structure (`safe-outputs`, `permissions`, `network`)
- Agent instruction structure (9 steps for refresh, 7 steps for update)
- Documentation directory layout (`docs/codebase/core/`, `telemetry/`, `integrations/`)
- Page front-matter schema
- Citation format `[S#:L##]`
- Required sections for core vs integration pages

### What changes per SDK

- **SDK Context block** in both workflow `.md` files
- **Trigger paths** in `docs-codebase-update.md` frontmatter
- **Change-to-page mapping table** in `docs-codebase-update.md` (Step 3)
- **Core pages table** in `docs-codebase-refresh.md` (Step 3 -- maps pages to source files)
- **Telemetry pages table** in `docs-codebase-refresh.md` (Step 4 -- not all SDKs have all telemetry)
- **Category mapping reference** in `style-guide.md` (bottom section)

---

## Verification

After setup, verify with:

1. **Compile check**: `gh aw compile` produces 0 errors, 0 warnings for both workflows.
2. **Manual trigger**: `gh aw run docs-codebase-refresh` via `workflow_dispatch` generates initial docs.
3. **Coverage check**: Every integration in the registry has a doc page in the manifest.
4. **Content check**: All pages have valid front-matter and all required sections.
5. **Incremental test**: Push a source change to main/master, verify only affected pages update.
6. **PR review**: Both workflows create PRs (never direct commits) for human review.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Two workflows (refresh + update) | Refresh is manual for initial gen or periodic full regen; update runs automatically on every merge |
| `docs/codebase/` not `docs/` | Avoids conflicts with existing Sphinx/JSDoc/Javadoc in `docs/` |
| `create-pull-request` not direct push | Human review before merging generated content |
| Manifest with `sources_hash` | Enables incremental updates without re-reading all source files |
| Category directories mirror GROUPS | Consistent with existing test infrastructure; easy to discover |
| Style guide as separate file | Agent reads it at runtime; single source of truth for formatting |
| `linguist-generated=true` | Excludes generated docs from GitHub language stats and PR diffs by default |
| `[S#:L##]` citations | Traceability from docs back to source; verifiable by reviewers |
