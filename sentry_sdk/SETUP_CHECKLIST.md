# Sentry Python SDK: setup checklist

This is Sentry's official setup guidance, shipped in the `sentry-sdk` package and maintained by Sentry with the SDK. It matches the agent setup guide at https://docs.sentry.io/ai/agent-setup/get-started.md.

## Required

- **Errors.** This is Sentry's core product. Set it up unless the user asked you not to. It needs a DSN: if the user hasn't given you one and you can't get one yourself, ask for it. Read it from an environment variable (`SENTRY_DSN`) and don't commit it. Before you finish, send a test error and check that it arrives.

## Recommended

Turn these on unless the user asked you not to. Sentry links errors, traces and logs, so an error shows the request that caused it and the logs around it. Traces and logs count against the project's quota, and logs can hold user data, so tell the user what you turned on.

- **Tracing.** Shows which request caused an error and where the time went. Set `traces_sample_rate` by environment: `1.0` locally, so you can see a trace when you verify, and a low rate in production (`0.1` is a common start) unless the project already has one. Don't default it to `0`: a rate of `0` sends no traces at all.
- **Logs.** There is no `enable_logs` option in this version. Logs reach Sentry when something sends them. Add `LoggingIntegration()` from `sentry_sdk.integrations.logging` to `integrations` to send what the app logs with the standard `logging` module, at `INFO` and above. If the app logs a lot, or its info logs can hold user data, send only warnings and errors with `LoggingIntegration(level=logging.WARNING)`. If the app logs with Loguru, add `LoguruIntegration()` from `sentry_sdk.integrations.loguru` instead. To change or drop a log before it is sent, set `before_send_log` in `sentry_sdk.init`: it gets each log and a hint, and returns the log, or `None` to drop it. You can also call `sentry_sdk.logger.info(...)` directly. The app's logs then show up next to the errors they belong to.
- **Environment and release.** Set `environment` so production issues are separate from local ones. The SDK reads the release from `SENTRY_RELEASE` or the git commit. If the app runs without `.git` (for example in a Docker image), set `SENTRY_RELEASE` at build time or pass the app's version as `release`, so you can see which deploy caused an issue.

To tell production from local runs, use a check the app already has, such as its debug flag or its environment setting. Don't add a new variable that nothing sets.

```python
import os
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from myapp import settings

is_production = not settings.DEBUG

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    environment="production" if is_production else "development",
    traces_sample_rate=0.1 if is_production else 1.0,
    integrations=[LoggingIntegration()],
)
```

## Situational

Do the Required and Recommended items first. Then look at the ones below. Set them up only when the project matches.

- **Crons**, if the app schedules jobs with Celery beat, RQ (rq-scheduler), arq or Huey. Sentry alerts when a job fails or doesn't run, and every scheduled job gets a monitor, including ones added later. For Celery beat, pass `CeleryIntegration(monitor_beat_tasks=True)`. For RQ or arq, pass `monitor_cron_jobs=True` to `RqIntegration` or `ArqIntegration`. For Huey, pass `HueyIntegration(monitor_periodic_tasks=True)`.
- **AI monitoring**, if the app calls an LLM. Most AI libraries are traced automatically. For LiteLLM or Mistral, add `LiteLLMIntegration()` or `MistralIntegration()`. Prompts and responses are recorded by default in this version, so tell the user, and set `data_collection={"gen_ai": {"inputs": False, "outputs": False}}` if they don't want that.
- **Caches**, if the app uses Redis as a cache. Pass the key prefixes of the cache to `RedisIntegration`, as in `cache_prefixes=["mycache:"]`. The Django cache is traced automatically.
- **Profiling**, if the user cares about the performance of specific code paths. Set `profile_session_sample_rate` and `profile_lifecycle="trace"`.
- **Metrics**, if the user wants to track counts or values over time (signups, queue depth). Call `sentry_sdk.metrics.count(...)`, `gauge(...)` or `distribution(...)` where they happen.

## Before you finish

Tell the user what you set up, the trace sample rate you chose for each environment, which logs you send, and which situational items you included and why.

Then pass `_experiments={"agent_setup_complete": True}` to `sentry_sdk.init`, or add the key if the call already has `_experiments`. It tells the SDK the setup has been checked, so it stops pointing coding agents to this file.
