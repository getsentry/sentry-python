# Sentry Python SDK: setup checklist

This is Sentry's official setup guidance, shipped in the `sentry-sdk` package and maintained by Sentry with the SDK. It matches the agent setup guide at https://docs.sentry.io/ai/agent-setup/get-started.md.

## Required

- **Errors.** This is Sentry's core product. Set it up unless the user asked you not to. It needs a DSN: if the user hasn't given you one and you can't get one yourself, ask for it. Read it from an environment variable (`SENTRY_DSN`) and don't commit it. Before you finish, send a test error and check that it arrives.

## Recommended

Turn these on unless the user asked you not to. Sentry links errors, traces and logs, so an error shows the request that caused it and the logs around it.

- **Tracing.** Shows which request caused an error and where the time went. Set `traces_sample_rate` by environment: `1.0` locally, so you can see a trace when you verify, and a low rate in production (`0.1` is a common start) unless the project already has one. Don't default it to `0`: a rate of `0` sends no traces at all.
- **Logs.** Set `enable_logs=True`. The app's logs show up next to the errors they belong to.
- **Environment and release.** Set `environment` so production issues are separate from local ones. The SDK reads the release from `SENTRY_RELEASE` or the git commit. If the app runs without `.git` (for example in a Docker image), set `SENTRY_RELEASE` at build time or pass the app's version as `release`, so you can see which deploy caused an issue.

To tell production from local runs, use a check the app already has, such as its debug flag or its environment setting. Don't add a new variable that nothing sets.

```python
import os
import sentry_sdk

from myapp import settings

is_production = not settings.DEBUG

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    environment="production" if is_production else "development",
    traces_sample_rate=0.1 if is_production else 1.0,
    enable_logs=True,
)
```

## Situational

Do the Required and Recommended items first. Then look at the ones below. Set them up only when the project matches.

- **Crons**, if the app schedules jobs with Celery beat, RQ (rq-scheduler), arq or Huey. Sentry alerts when a job fails or doesn't run, and every scheduled job gets a monitor, including ones added later. For Celery beat, pass `CeleryIntegration(monitor_beat_tasks=True)`. For RQ or arq, pass `monitor_cron_jobs=True` to `RqIntegration` or `ArqIntegration`. For Huey, pass `HueyIntegration(monitor_periodic_tasks=True)`.
- **AI monitoring**, if the app calls an LLM. Most AI libraries are traced automatically. For LiteLLM or Mistral, add `LiteLLMIntegration()` or `MistralIntegration()`. Prompts and responses are only recorded with `send_default_pii=True`, so ask the user before you set it.
- **Caches**, if the app uses the Django cache or Redis as a cache. For Django, pass `cache_spans=True` to `DjangoIntegration`. For Redis, pass the key prefixes of the cache to `RedisIntegration`, as in `cache_prefixes=["mycache:"]`.
- **Profiling**, if the user cares about the performance of specific code paths. Set `profile_session_sample_rate` and `profile_lifecycle="trace"`.
- **Metrics**, if the user wants to track counts or values over time (signups, queue depth). Call `sentry_sdk.metrics.count(...)`, `gauge(...)` or `distribution(...)` where they happen.

## Before you finish

Tell the user what you set up, the trace sample rate you chose for each environment, whether logs are on, and which situational items you included and why.

Then set `agent_setup_complete=True` in `sentry_sdk.init`. That tells the SDK the setup has been checked, so it stops pointing coding agents to this file.
