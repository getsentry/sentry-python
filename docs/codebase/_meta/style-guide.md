# Documentation Style Guide

This style guide governs all pages under `docs/codebase/`. It is designed to be
portable across Sentry SDKs -- only the **SDK Context** block in each workflow
and the SDK-specific values marked with `{{variable}}` below change per language.

---

## Page Front-matter

Every page starts with YAML front-matter:

```yaml
---
title: "<Page Title>"
description: "<One-line summary for search / LLM retrieval>"
sdk: "{{sdk_id}}"                # e.g. python, javascript, java, ruby, go, rust, dotnet
category: core | telemetry | integration | overview
# Integration pages only:
subcategory: "{{from GROUPS mapping}}"   # e.g. web, ai, databases, tasks, cloud, etc.
owners:
  - "{{owners_team}}"            # e.g. @getsentry/owners-python-sdk
last_reviewed: "2026-03-12"
sources:
  - "{{path/to/source/file}}"
sources_hash: "<SHA-256 of concatenated source contents>"
# Integration pages only:
min_version: "1.8"
auto_enabling: true
---
```

### Field Definitions

| Field | Required | Description |
|-------|----------|-------------|
| `title` | yes | Human-readable page title |
| `description` | yes | One-line summary |
| `sdk` | yes | SDK identifier (e.g. `python`, `javascript`, `java`) |
| `category` | yes | One of: `core`, `telemetry`, `integration`, `overview` |
| `subcategory` | integration only | Category from the SDK's GROUPS / category mapping |
| `owners` | yes | GitHub team(s) responsible |
| `last_reviewed` | yes | ISO date of last generation/review |
| `sources` | yes | List of source files this page documents |
| `sources_hash` | yes | SHA-256 hash for change detection |
| `min_version` | integration only | Minimum supported library version |
| `auto_enabling` | integration only | Whether the integration auto-enables on import |

---

## Required Sections

### Core and Telemetry Pages

1. **Overview** -- What this module does and why it exists.
2. **How It Works** -- Internal mechanics, data flow, key classes/functions.
3. **Configuration** -- Table of relevant options (name, type, default, description).
4. **Usage Examples** -- Minimal code fences showing common usage.
5. **Internals** -- Implementation details: monkey-patching, signal handlers, hooks.
6. **References** -- Source citations and links.

### Integration Pages

1. **Overview** -- What the integration captures and why.
2. **How It Works** -- Instrumentation approach (middleware, signals, monkey-patching).
3. **Setup** -- Installation, `sentry_sdk.init()` configuration, auto-enabling behavior.
4. **Configuration** -- Integration-specific options table.
5. **What Gets Captured** -- Errors, spans, breadcrumbs, and their attributes.
6. **Usage Examples** -- Minimal code showing the integration in action.
7. **Internals** -- Key functions, classes, and how they hook into the library.
8. **Known Limitations** -- Version constraints, unsupported features.
9. **References** -- Source citations and links.

---

## Formatting Rules

### Citations

Use bracketed source references that map to the `sources` list:

```markdown
The client reads DSN from the options dict [S1:L45] and validates it
before constructing the transport [S2:L120].
```

Format: `[S<index>:L<line>]` where index is 1-based into the `sources` list.

### Code Blocks

- Use the SDK's language identifier for code fences (e.g. `python`, `javascript`, `java`).
- Keep examples minimal and runnable.
- Do not include boilerplate imports unless they are non-obvious.

### Tables

Use pipe tables for configuration options:

```markdown
| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `dsn` | `str` | `None` | Sentry DSN for this project |
```

### Prose

- Active voice, present tense.
- No marketing language ("powerful", "seamless", "enterprise-grade").
- Technical precision over brevity -- but no unnecessary padding.
- Link to `https://develop.sentry.dev/sdk/` pages where they provide
  additional specification context.

### Headings

- Page title is H1 (from front-matter `title`, not repeated in body).
- Sections use H2 (`##`).
- Subsections use H3 (`###`).
- Do not skip heading levels.

---

## Integration Category Mapping

Integration pages are organized into subdirectories. Each SDK defines its own
category groupings (see **SDK Context** in the workflows). The agent reads the
SDK's category source and maps group keys to directories using this convention:

| Category Pattern | Directory |
|-----------------|-----------|
| Web / HTTP frameworks | `integrations/web/` |
| AI / LLM providers | `integrations/ai/` |
| AI Workflow / Agents / MCP | `integrations/ai/` |
| Databases / Data stores | `integrations/databases/` |
| Task queues / Workers | `integrations/tasks/` |
| Cloud providers / Serverless | `integrations/cloud/` |
| GraphQL | `integrations/graphql/` |
| Network / HTTP clients | `integrations/network/` |
| Feature flags | `integrations/flags/` |
| Everything else | `integrations/misc/` |

### Python SDK Categories (reference)

Derived from `GROUPS` in `scripts/split_tox_gh_actions/split_tox_gh_actions.py`:

| GROUPS Key | Directory | Integrations |
|------------|-----------|-------------|
| Web 1 | `integrations/web/` | django, flask, starlette, fastapi |
| Web 2 | `integrations/web/` | aiohttp, bottle, falcon, litestar, pyramid, quart, sanic, starlite, tornado |
| AI | `integrations/ai/` | anthropic, cohere, google_genai, huggingface_hub, litellm, openai |
| AI Workflow | `integrations/ai/` | langchain, langgraph |
| Agents | `integrations/ai/` | openai_agents, pydantic_ai |
| MCP | `integrations/ai/` | mcp, fastmcp |
| DBs | `integrations/databases/` | asyncpg, clickhouse_driver, pymongo, redis, sqlalchemy |
| Tasks | `integrations/tasks/` | arq, beam, celery, dramatiq, huey, ray, rq, spark |
| Cloud | `integrations/cloud/` | aws_lambda, boto3, chalice, gcp |
| GraphQL | `integrations/graphql/` | ariadne, gql, graphene, strawberry |
| Network | `integrations/network/` | grpc, httpx, requests |
| Flags | `integrations/flags/` | launchdarkly, openfeature, statsig, unleash |
| Misc | `integrations/misc/` | loguru, opentelemetry, pure_eval, trytond, typer |

---

## File Naming

- Use lowercase kebab-case: `django.md`, `aws-lambda.md`, `data-model.md`.
- Integration file names match the SDK's module/package name, with underscores
  or dots converted to hyphens.
- Index files use `_index.md`.

---

## Manifest Updates

After modifying any page, update `docs/codebase/_meta/manifest.json`:

```json
{
  "pages": {
    "core/client.md": {
      "title": "Client",
      "sources": ["sentry_sdk/client.py"],
      "sources_hash": "<sha256>",
      "last_updated": "2026-03-12"
    }
  }
}
```

The manifest is the source of truth for change detection in incremental updates.
