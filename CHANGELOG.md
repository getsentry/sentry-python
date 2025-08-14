# Changelog

## 2.35.0

### Various fixes & improvements

- [Langchain Integration](https://docs.sentry.io/platforms/python/integrations/langchain/) now supports the Sentry [AI dashboard](https://docs.sentry.io/product/insights/ai/agents/dashboard/). (#4678) by @shellmayr
- [Anthropic Integration](https://docs.sentry.io/platforms/python/integrations/anthropic/) now supports the Sentry [AI dashboard](https://docs.sentry.io/product/insights/ai/agents/dashboard/). (#4674) by @constantinius
- AI Agents templates for `@trace` decorator (#4676) by @antonpirker
- Sentry Logs: Add `enable_logs`, `before_send_log` as top-level `sentry_sdk.init()` options (#4644) by @sentrivana
- Tracing: Improve `@trace` decorator. Allows to set `span.op`, `span.name`, and `span.attributes` (#4648) by @antonpirker
- Tracing: Add convenience function `sentry_sdk.update_current_span`. (#4673) by @antonpirker
- Tracing: Add `Span.update_data()` to update multiple `span.data` items at once. (#4666) by @antonpirker
- GNU-integration: make path optional (#4688) by @MeredithAnya
- Clickhouse: Don't eat the generator data (#4669) by @szokeasaurusrex
- Clickhouse: List `send_data` parameters (#4667) by @szokeasaurusrex
- Update `gen_ai.*` and `ai.*` attributes (#4665) by @antonpirker
- Better checking for empty tools list (#4647) by @antonpirker
- Remove performance paper cuts (#4675) by @sentrivana
- Help for debugging Cron problems (#4686) by @antonpirker
- Fix Redis CI (#4691) by @sentrivana
- Fix plugins key codecov (#4655) by @sl0thentr0py
- Fix Mypy (#4649) by @sentrivana
- Update tox.ini (#4689) by @sentrivana
- build(deps): bump actions/create-github-app-token from 2.0.6 to 2.1.0 (#4684) by @dependabot

## 2.34.1

### Various fixes & improvements

- Fix: Make sure Span data in AI instrumentations is always a primitive data type (#4643) by @antonpirker
- Fix: Typo in CHANGELOG.md (#4640) by @jgillard

## 2.34.0

### Various fixes & improvements

- Considerably raise `DEFAULT_MAX_VALUE_LENGTH` (#4632) by @sentrivana

  We have increased the string trimming limit considerably, allowing you to see more data
  without it being truncated. Note that this might, in rare cases, result in issue regrouping,
  for example if you're capturing message events with very long messages (longer than the
  default 1024 characters/bytes).

  If you want to adjust the limit, you can set a
  [`max_value_length`](https://docs.sentry.io/platforms/python/configuration/options/#max_value_length)
  in your `sentry_sdk.init()`.

- `OpenAI` integration update (#4612) by @antonpirker

  The `OpenAIIntegration` now supports [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses).

  The data captured will also show up in the new [AI Agents Dashboard](https://docs.sentry.io/product/insights/agents/dashboard/).

  This works out of the box, nothing to do on your side.

- Expose `set_transaction_name` (#4634) by @sl0thentr0py
- Fix(Celery): Latency should be in milliseconds, not seconds (#4637) by @sentrivana
- Fix(Django): Treat `django.template.context.BasicContext` as sequence in serializer (#4621) by @sl0thentr0py
- Fix(Huggingface): Fix `huggingface_hub` CI tests. (#4619) by @antonpirker
- Fix: Ignore deliberate thread exception warnings (#4611) by @sl0thentr0py
- Fix: Socket tests to not use example.com (#4627) by @sl0thentr0py
- Fix: Threading run patch (#4610) by @sl0thentr0py
- Tests: Simplify celery double patching test (#4626) by @sl0thentr0py
- Tests: Remove remote example.com calls (#4622) by @sl0thentr0py
- Tests: tox.ini update (#4635) by @sentrivana
- Tests: Update tox (#4609) by @sentrivana

## 2.33.2

### Various fixes & improvements

- ref(spotlight): Do not import `sentry_sdk.spotlight` unless enabled (#4607) by @sentrivana
- ref(gnu-integration): update clickhouse stacktrace parsing (#4598) by @MeredithAnya

## 2.33.1

### Various fixes & improvements

- fix(integrations): allow explicit op parameter in `ai_track` (#4597) by @mshavliuk
- fix: Fix `abs_path` bug in `serialize_frame` (#4599) by @szokeasaurusrex
- Remove pyrsistent from test dependencies (#4588) by @musicinmybrain
- Remove explicit `__del__`'s in threaded classes (#4590) by @sl0thentr0py
- Remove forked from test_transport, separate gevent tests and generalize capturing_server to be module level (#4577) by @sl0thentr0py
- Improve token usage recording (#4566) by @antonpirker

## 2.33.0

### Various fixes & improvements

- feat(langchain): Support `BaseCallbackManager` (#4486) by @szokeasaurusrex
- Use `span.data` instead of `measurements` for token usage (#4567) by @antonpirker
- Fix custom model name (#4569) by @antonpirker
- fix: shut down "session flusher" more promptly (#4561) by @bukzor
- chore: Remove Lambda urllib3 pin on Python 3.10+ (#4549) by @sentrivana

## 2.32.0

### Various fixes & improvements

- feat(sessions): Add top-level start- and end session methods (#4474) by @szokeasaurusrex
- feat(openai-agents): Set tool span to failed if an error is raised in the tool (#4527) by @antonpirker
- fix(integrations/ray): Correctly pass keyword arguments to ray.remote function (#4430) by @svartalf
- fix(langchain): Make `span_map` an instance variable (#4476) by @szokeasaurusrex
- fix(langchain): Ensure no duplicate `SentryLangchainCallback` (#4485) by @szokeasaurusrex
- fix(Litestar): Apply `failed_request_status_codes` to exceptions raised in middleware (#4074) by @vrslev

## 2.31.0

### Various fixes & improvements

- **New Integration (BETA):** Add support for `openai-agents` (#4437) by @antonpirker

  We can now instrument AI agents that are created with the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) out of the box.

```python
import sentry_sdk
from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration

# Add the OpenAIAgentsIntegration to your sentry_sdk.init call:
sentry_sdk.init(
    dsn="...",
    integrations=[
        OpenAIAgentsIntegration(),
    ]
)
```

For more information see the [OpenAI Agents integrations documentation](https://docs.sentry.io/platforms/python/integrations/openai-agents/).

- Logs: Add support for `dict` arguments (#4478) by @AbhiPrasad
- Add Cursor generated rules (#4493) by @sl0thentr0py
- Greatly simplify Langchain integrations `_wrap_configure` (#4479) by @szokeasaurusrex
- Fix(ci): Remove tracerite pin (almost) (#4504) by @sentrivana
- Fix(profiling): Ensure profiler thread exits when needed (#4497) by @Zylphrex
- Fix(ci): Do not install newest `tracerite` (#4494) by @sentrivana
- Fix(scope): Handle token reset `LookupError`s gracefully (#4481) by @sentrivana
- Tests: Tox update (#4509) by @sentrivana
- Tests: Upper bound on fakeredis on old Python versions (#4482) by @sentrivana
- Tests: Regenerate tox (#4457) by @sentrivana

## 2.30.0

### Various fixes & improvements

- **New beta feature:** Sentry logs for Loguru (#4445) by @sentrivana

  We can now capture Loguru logs and send them to Sentry.

```python
import sentry_sdk
from sentry_sdk.integrations.loguru import LoguruIntegration

# Setup Sentry SDK to send Loguru log messages with a level of "error" or higher to Sentry
sentry_sdk.init(
    _experiments={
        "enable_logs": True,
    },
    integrations=[
        LoguruIntegration(sentry_logs_level=logging.ERROR),
    ]
)
```

- fix(logs): Don't gate user behind `send_default_pii` (#4453) by @AbhiPrasad
- fix(logging): Strip log `record.name` for more robust matching (#4411) by @romaingd-spi
- Migrate to modern threading interface (#4452) by @emmanuel-ferdman
- ref: Remove `_capture_experimental_log` `scope` parameter (#4424) by @szokeasaurusrex
- feat(logs): Add user attributes to logs (#4423) by @szokeasaurusrex
- fix: fix ARQ integration error (#4427) (#4428) by @ninoseki
- fix(grpc): Fix AttributeError when instrumenting with OTel (#4405) by @sentrivana
- fix(redis): Use `command_queue` instead of `command_stack` if available (#4404) by @sentrivana
- fix: Handle invalid `SENTRY_DEBUG` values properly (#4400) by @szokeasaurusrex
- Increase test coverage (#4393) by @mgaligniana
- tests(logs): avoid failures when running with integrations enabled (#4388) by @rominf
- Fix CI, adapt to new redis-py release (#4431) by @sentrivana
- tests: Regenerate toxgen (#4403) by @sentrivana
- tests: Regenerate tox.ini & fix CI (#4435) by @sentrivana
- build(deps): bump codecov/codecov-action from 5.4.2 to 5.4.3 (#4397) by @dependabot

## 2.29.1

### Various fixes & improvements

- fix(logs): send `severity_text`: `warn` instead of `warning` (#4396) by @lcian

## 2.29.0

### Various fixes & improvements

- fix(loguru): Move integration setup from `__init__` to `setup_once` (#4399) by @sentrivana
- feat: Allow configuring `keep_alive` via environment variable (#4366) by @szokeasaurusrex
- fix(celery): Do not send extra check-in (#4395) by @sentrivana
- fix(typing): Add before_send_log to Experiments (#4383) by @sentrivana
- ci: Fix pyspark test suite (#4382) by @sentrivana
- fix(logs): Make `sentry.message.parameters` singular as per spec (#4387) by @AbhiPrasad
- apidocs: Remove snowballstemmer pin (#4379) by @sentrivana

## 2.28.0

### Various fixes & improvements

- fix(logs): Forward `extra` from logger as attributes (#4374) by @AbhiPrasad
- fix(logs): Canonicalize paths from the logger integration (#4336) by @colin-sentry
- fix(logs): Use new transport (#4317) by @colin-sentry
- fix: Deprecate `set_measurement()` API. (#3934) by @antonpirker
- fix: Put feature flags on isolation scope (#4363) by @antonpirker
- fix: Make use of `SPANDATA` consistent (#4373) by @antonpirker
- fix: Discord link (#4371) by @sentrivana
- tests: Pin snowballstemmer for now (#4372) by @sentrivana
- tests: Regular tox update (#4367) by @sentrivana
- tests: Bump test timeout for recursion stacktrace extract to 2s (#4351) by @booxter
- tests: Fix test_stacktrace_big_recursion failure due to argv (#4346) by @booxter
- tests: Move anthropic under toxgen (#4348) by @sentrivana
- tests: Update tox.ini (#4347) by @sentrivana
- chore: Update GH issue templates for Linear compatibility (#4328) by @stephanie-anderson
- chore: Bump actions/create-github-app-token from 2.0.2 to 2.0.6 (#4358) by @dependabot

## 2.27.0

### Various fixes & improvements

- fix: Make sure to use the default decimal context in our code (#4231) by @antonpirker
- fix(integrations): ASGI integration not capture transactions in Websocket (#4293) by @guodong000
- feat(typing): Make all relevant types public (#4315) by @antonpirker
- feat(spans): Record flag evaluations as span attributes (#4280) by @cmanallen
- test(logs): Avoid failure when running with integrations enabled (#4316) by @rominf
- tests: Remove unused code and rerun (#4313) by @sentrivana
- tests: Add cohere to toxgen (#4304) by @sentrivana
- tests: Migrate fastapi to toxgen (#4302) by @sentrivana
- tests: Add huggingface_hub to toxgen (#4299) by @sentrivana
- tests: Add huey to toxgen (#4298) by @sentrivana
- tests: Update tox.ini (#4297) by @sentrivana
- tests: Move aiohttp under toxgen (#4319) by @sentrivana
- tests: Fix version picking in toxgen (#4323) by @sentrivana
- build(deps): bump codecov/codecov-action from 5.4.0 to 5.4.2 (#4318) by @dependabot

## 2.26.1

### Various fixes & improvements

- fix(threading): Data leak in ThreadingIntegration between threads (#4281) by @antonpirker
- fix(logging): Clarify separate warnings case is for Python <3.11 (#4296) by @szokeasaurusrex
- fix(logging): Add formatted message to log events (#4292) by @szokeasaurusrex
- fix(logging): Send raw logging parameters (#4291) by @szokeasaurusrex
- fix: Revert "chore: Deprecate `same_process_as_parent` (#4244)" (#4290) by @sentrivana

## 2.26.0

### Various fixes & improvements

- fix(debug): Do not consider parent loggers for debug logging (#4286) by @szokeasaurusrex
- test(tracing): Simplify static/classmethod tracing tests (#4278) by @szokeasaurusrex
- feat(transport): Add a timeout (#4252) by @sentrivana
- meta: Change CODEOWNERS back to Python SDK owners (#4269) by @sentrivana
- feat(logs): Add sdk name and version as log attributes (#4262) by @AbhiPrasad
- feat(logs): Add server.address to logs (#4257) by @AbhiPrasad
- chore: Deprecate `same_process_as_parent` (#4244) by @sentrivana
- feat(logs): Add sentry.origin attribute for log handler (#4250) by @AbhiPrasad
- feat(tests): Add optional cutoff to toxgen (#4243) by @sentrivana
- toxgen: Retry & fail if we fail to fetch PyPI data (#4251) by @sentrivana
- build(deps): bump actions/create-github-app-token from 1.12.0 to 2.0.2 (#4248) by @dependabot
- Trying to prevent the grpc setup from being flaky (#4233) by @antonpirker
- feat(breadcrumbs): add `_meta` information for truncation of breadcrumbs (#4007) by @shellmayr
- tests: Move django under toxgen (#4238) by @sentrivana
- fix: Handle JSONDecodeError gracefully in StarletteRequestExtractor (#4226) by @moodix
- fix(asyncio): Remove shutdown handler (#4237) by @sentrivana

## 2.25.1

### Various fixes & improvements

- fix(logs): Add a class which batches groups of logs together. (#4229) by @colin-sentry
- fix(logs): Use repr instead of json for message and arguments (#4227) by @colin-sentry
- fix(logs): Debug output from Sentry logs should always be `debug` level. (#4224) by @antonpirker
- fix(ai): Do not consume anthropic streaming stop (#4232) by @colin-sentry
- fix(spotlight): Do not spam sentry_sdk.warnings logger w/ Spotlight (#4219) by @BYK
- fix(docs): fixed code snippet (#4218) by @antonpirker
- build(deps): bump actions/create-github-app-token from 1.11.7 to 1.12.0 (#4214) by @dependabot

## 2.25.0

### Various fixes & improvements

- **New Beta Feature** Enable Sentry logs in `logging` Integration (#4143) by @colin-sentry

  You can now send existing log messages to the new Sentry Logs feature.

  For more information see: https://github.com/getsentry/sentry/discussions/86804

  This is how you can use it (Sentry Logs is in beta right now so the API can still change):

  ```python
  import logging

  import sentry_sdk
  from sentry_sdk.integrations.logging import LoggingIntegration

  # Setup Sentry SDK to send log messages with a level of "error" or higher to Sentry.
  sentry_sdk.init(
    dsn="...",
    _experiments={
        "enable_logs": True
    }
    integrations=[
      LoggingIntegration(sentry_logs_level=logging.ERROR),
    ]
  )

  # Your existing logging setup
  some_logger = logging.Logger("some-logger")

  some_logger.info('In this example info events will not be sent to Sentry logs. my_value=%s', my_value)
  some_logger.error('But error events will be sent to Sentry logs. my_value=%s', my_value)
  ```

- Spotlight: Sample everything 100% w/ Spotlight & no DSN set (#4207) by @BYK
- Dramatiq: use set_transaction_name (#4175) by @timdrijvers
- toxgen: Make it clearer which suites can be migrated (#4196) by @sentrivana
- Move Litestar under toxgen (#4197) by @sentrivana
- Added flake8 plugings to pre-commit call of flake8 (#4190) by @antonpirker
- Deprecate Scope.user (#4194) by @sentrivana
- Fix hanging when capturing long stacktrace (#4191) by @szokeasaurusrex
- Fix GraphQL failures (#4208) by @sentrivana
- Fix flaky test (#4198) by @sentrivana
- Update Ubuntu in Github test runners (#4204) by @antonpirker

## 2.24.1

### Various fixes & improvements

- Always set `_spotlight_url` (#4186) by @BYK
- Broader except in Django `parsed_body` (#4189) by @orhanhenrik
- Add platform header to the `chunk` item-type in the envelope (#4178) by @viglia
- Move `mypy` config into `pyproject.toml` (#4181) by @antonpirker
- Move `flake8` config into `pyproject.toml` (#4185) by @antonpirker
- Move `pytest` config into `pyproject.toml` (#4184) by @antonpirker
- Bump `actions/create-github-app-token` from `1.11.6` to `1.11.7` (#4188) by @dependabot
- Add `CODEOWNERS` (#4182) by @sentrivana

## 2.24.0

### Various fixes & improvements

- fix(tracing): Fix `InvalidOperation` (#4179) by @szokeasaurusrex
- Fix memory leak by not piling up breadcrumbs forever in Spark workers.  (#4167) by @antonpirker
- Update scripts sources (#4166) by @emmanuel-ferdman
- Fixed flaky test (#4165) by @antonpirker
- chore(profiler): Add deprecation warning for session functions (#4171) by @sentrivana
- feat(profiling): reverse profile_session start/stop methods deprecation (#4162) by @viglia
- Reset `DedupeIntegration`'s `last-seen` if `before_send` dropped the event (#4142) by @sentrivana
- style(integrations): Fix captured typo (#4161) by @pimuzzo
- Handle loguru msg levels that are not supported by Sentry (#4147) by @antonpirker
- feat(tests): Update tox.ini (#4146) by @sentrivana
- Support Starlette/FastAPI `app.host` (#4157) by @sentrivana

## 2.23.1

### Various fixes & improvements

- Fix import problem in release 2.23.0 (#4140) by @antonpirker

## 2.23.0

### Various fixes & improvements

- Feat(profiling): Add new functions to start/stop continuous profiler (#4056) by @Zylphrex
- Feat(profiling): Export start/stop profile session (#4079) by @Zylphrex
- Feat(tracing): Backfill missing `sample_rand` on `PropagationContext` (#4038) by @szokeasaurusrex
- Feat(logs): Add alpha version of Sentry logs (#4126) by @colin-sentry
- Security(gha): fix potential for shell injection (#4099) by @mdtro
- Docs: Add `init()` parameters to ApiDocs. (#4100) by @antonpirker
- Docs: Document that caller must check `mutable` (#4010) by @szokeasaurusrex
- Fix(Anthropic): Add partial json support to streams (#3674)
- Fix(ASGI): Fix KeyError if transaction does not exist (#4095) by @kevinji
- Fix(asyncio): Improve asyncio integration error handling. (#4129) by @antonpirker
- Fix(AWS Lambda): Fix capturing errors during AWS Lambda INIT phase (#3943)
- Fix(Bottle): Prevent internal error on 404 (#4131) by @sentrivana
- Fix(CI): Fix API doc failure in CI (#4075) by @sentrivana
- Fix(ClickHouse) ClickHouse in test suite (#4087) by @antonpirker
- Fix(cloudresourcecontext): Added timeout to HTTP requests in CloudResourceContextIntegration (#4120) by @antonpirker
- Fix(crons): Fixed bug when `cron_jobs` is set to `None` in arq integration (#4115) by @antonpirker
- Fix(debug): Take into account parent handlers for debug logger (#4133) by @sentrivana
- Fix(FastAPI/Starlette):  Fix middleware with positional arguments.  (#4118) by @antonpirker
- Fix(featureflags): add LRU update/dedupe test coverage (#4082)
- Fix(logging): Coerce None values into strings in logentry params. (#4121) by @antonpirker
- Fix(pyspark): Grab `attemptId` more defensively (#4130) by @sentrivana
- Fix(Quart): Support `quart_flask_patch` (#4132) by @sentrivana
- Fix(tests): A way to locally run AWS Lambda functions (#4128) by @antonpirker
- Fix(tests): Add concurrency testcase for arq (#4125) by @sentrivana
- Fix(tests): Add fail_on_changes to toxgen by @sentrivana
- Fix(tests): Run AWS Lambda tests locally (#3988) by @antonpirker
- Fix(tests): Test relevant prereleases and allow to ignore releases
- Fix(tracing): Move `TRANSACTION_SOURCE_*` constants to `Enum` (#3889) by @mgaligniana
- Fix(typing): Add more typing info to Scope.update_from_kwargs's "contexts" (#4080)
- Fix(typing): Set correct type for `set_context` everywhere (#4123) by @sentrivana
- Chore(tests): Regenerate tox.ini (#4108) by @sentrivana
- Build(deps): bump actions/create-github-app-token from 1.11.5 to 1.11.6 (#4113) by @dependabot
- Build(deps): bump codecov/codecov-action from 5.3.1 to 5.4.0 (#4112) by @dependabot

## 2.22.0

### Various fixes & improvements

- **New integration:** Add [Statsig](https://statsig.com/) integration (#4022) by @aliu39

  For more information, see the documentation for the [StatsigIntegration](https://docs.sentry.io/platforms/python/integrations/statsig/).

- Profiling: Continuous profiling lifecycle (#4017) by @Zylphrex
- Fix: Revert "feat(tracing): Add `propagate_traces` deprecation warning (#3899)" (#4055) by @cmanallen
- Tests: Generate Web 1 group tox entries by toxgen script (#3980) by @sentrivana
- Tests: Generate Web 2 group tox entries by toxgen script (#3981) by @sentrivana
- Tests: Generate Tasks group tox entries by toxgen script (#3976) by @sentrivana
- Tests: Generate AI group tox entries by toxgen script (#3977) by @sentrivana
- Tests: Generate DB group tox entries by toxgen script (#3978) by @sentrivana
- Tests: Generate Misc group tox entries by toxgen script (#3982) by @sentrivana
- Tests: Generate Flags group tox entries by toxgen script (#3974) by @sentrivana
- Tests: Generate gRPC tox entries by toxgen script (#3979) by @sentrivana
- Tests: Remove toxgen cutoff, add statsig (#4048) by @sentrivana
- Tests: Reduce continuous profiling test flakiness (#4052) by @Zylphrex
- Tests: Fix Clickhouse test (#4053) by @sentrivana
- Tests: Fix flaky HTTPS test (#4057) by @Zylphrex
- Update sample rate in DSC (#4018) by @sentrivana
- Move the GraphQL group over to the tox gen script (#3975) by @sentrivana
- Update changelog with `profile_session_sample_rate` (#4046) by @sentrivana

## 2.21.0

### Various fixes & improvements

- Fix incompatibility with new Strawberry version (#4026) by @sentrivana
- Add `failed_request_status_codes` to Litestar (#4021) by @vrslev

  See https://docs.sentry.io/platforms/python/integrations/litestar/ for details.
- Deprecate `enable_tracing` option (#3935) by @antonpirker

  The `enable_tracing` option is now deprecated. Please use `traces_sample_rate` instead. See https://docs.sentry.io/platforms/python/configuration/options/#traces_sample_rate for more information.
- Explicitly use `None` default when checking metadata (#4039) by @mpurnell1
- Fix bug where concurrent accesses to the flags property could raise a `RuntimeError` (#4034) by @cmanallen
- Add more min versions of frameworks (#3973) by @sentrivana
- Set level based on status code for HTTP client breadcrumbs (#4004) by @sentrivana
- Don't set transaction status to error on `sys.exit(0)` (#4025) by @sentrivana
- Continuous profiling sample rate (#4002) by @Zylphrex

  Set `profile_session_sample_rate=1.0` in your `init()` to collect continuous profiles for 100% of profile sessions. See https://docs.sentry.io/platforms/python/profiling/#enable-continuous-profiling for more information.
- Track and report spans that were dropped (#4005) by @constantinius
- Change continuous profile buffer size (#3987) by @Zylphrex
- Handle `MultiPartParserError` to avoid internal sentry crash (#4001) by @orhanhenrik
- Handle `None` lineno in `get_source_context` (#3925) by @sentrivana
- Add support for Python 3.12 and 3.13 to AWS Lambda integration (#3965) by @antonpirker
- Add `propagate_traces` deprecation warning (#3899) by @mgaligniana
- Check that `__module__` is `str` (#3942) by @szokeasaurusrex
- Add `__repr__` to `Baggage` (#4043) by @szokeasaurusrex
- Fix a typo (#3923) by @antonpirker
- Fix various CI errors on master (#4009) by @Zylphrex
- Split gevent tests off (#3964) by @sentrivana
- Add tox generation script, but don't use it yet (#3971) by @sentrivana
- Use `httpx_mock` in `test_httpx` (#3967) by @sl0thentr0py
- Fix typo in test name (#4036) by @szokeasaurusrex
- Fix mypy (#4019) by @sentrivana
- Test Celery's latest RC (#3938) by @sentrivana
- Bump `actions/create-github-app-token` from `1.11.2` to `1.11.3` (#4023) by @dependabot
- Bump `actions/create-github-app-token` from `1.11.1` to `1.11.2` (#4015) by @dependabot
- Bump `codecov/codecov-action` from `5.1.2` to `5.3.1` (#3995) by @dependabot

## 2.20.0

- **New integration:** Add [Typer](https://typer.tiangolo.com/) integration (#3869) by @patrick91

  For more information, see the documentation for the [TyperIntegration](https://docs.sentry.io/platforms/python/integrations/typer/).

- **New integration:** Add [Unleash](https://www.getunleash.io/) feature flagging integration (#3888) by @aliu39

  For more information, see the documentation for the [UnleashIntegration](https://docs.sentry.io/platforms/python/integrations/unleash/).

- Add custom tracking of feature flag evaluations (#3860) by @aliu39
- Feature Flags: Register LD hook in setup instead of init, and don't check for initialization (#3890) by @aliu39
- Feature Flags: Moved adding of `flags` context into Scope (#3917) by @antonpirker
- Create a separate group for feature flag test suites (#3911) by @sentrivana
- Fix flaky LaunchDarkly tests (#3896) by @aliu39
- Fix LRU cache copying (#3883) by @ffelixg
- Fix cache pollution from mutable reference (#3887) by @cmanallen
- Centralize minimum version checking (#3910) by @sentrivana
- Support SparkIntegration activation after SparkContext created (#3411) by @seyoon-lim
- Preserve ARQ enqueue_job __kwdefaults__ after patching (#3903) by @danmr
- Add Github workflow to comment on issues when a fix was released (#3866) by @antonpirker
- Update test matrix for Sanic (#3904) by @antonpirker
- Rename scripts (#3885) by @sentrivana
- Fix CI (#3878) by @sentrivana
- Treat `potel-base` as release branch in CI (#3912) by @sentrivana
- build(deps): bump actions/create-github-app-token from 1.11.0 to 1.11.1 (#3893) by @dependabot
- build(deps): bump codecov/codecov-action from 5.0.7 to 5.1.1 (#3867) by @dependabot
- build(deps): bump codecov/codecov-action from 5.1.1 to 5.1.2 (#3892) by @dependabot

## 2.19.2

### Various fixes & improvements

- Deepcopy and ensure get_all function always terminates (#3861) by @cmanallen
- Cleanup chalice test environment (#3858) by @antonpirker

## 2.19.1

### Various fixes & improvements

- Fix errors when instrumenting Django cache (#3855) by @BYK
- Copy `scope.client` reference as well (#3857) by @sl0thentr0py
- Don't give up on Spotlight on 3 errors (#3856) by @BYK
- Add missing stack frames (#3673) by @antonpirker
- Fix wrong metadata type in async gRPC interceptor (#3205) by @fdellekart
- Rename launch darkly hook to match JS SDK (#3743) by @aliu39
- Script for checking if our instrumented libs are Python 3.13 compatible (#3425) by @antonpirker
- Improve Ray tests (#3846) by @antonpirker
- Test with Celery `5.5.0rc3` (#3842) by @sentrivana
- Fix asyncio testing setup (#3832) by @sl0thentr0py
- Bump `codecov/codecov-action` from `5.0.2` to `5.0.7` (#3821) by @dependabot
- Fix CI (#3834) by @sentrivana
- Use new ClickHouse GH action (#3826) by @antonpirker

## 2.19.0

### Various fixes & improvements

- New: introduce `rust_tracing` integration. See https://docs.sentry.io/platforms/python/integrations/rust_tracing/ (#3717) by @matt-codecov
- Auto enable Litestar integration (#3540) by @provinzkraut
- Deprecate `sentry_sdk.init` context manager (#3729) by @szokeasaurusrex
- feat(spotlight): Send PII to Spotlight when no DSN is set (#3804) by @BYK
- feat(spotlight): Add info logs when Sentry is enabled (#3735) by @BYK
- feat(spotlight): Inject Spotlight button on Django (#3751) by @BYK
- feat(spotlight): Auto enable cache_spans for Spotlight on DEBUG (#3791) by @BYK
- fix(logging): Handle parameter `stack_info` for the `LoggingIntegration` (#3745) by @gmcrocetti
- fix(pure-eval): Make sentry-sdk[pure-eval] installable with pip==24.0 (#3757) by @sentrivana
- fix(rust_tracing): include_tracing_fields arg to control unvetted data in rust_tracing integration (#3780) by @matt-codecov
- fix(aws) Fix aws lambda tests (by reducing event size) (#3770) by @antonpirker
- fix(arq): fix integration with Worker settings as a dict (#3742) by @saber-solooki
- fix(httpx): Prevent Sentry baggage duplication (#3728) by @szokeasaurusrex
- fix(falcon): Don't exhaust request body stream (#3768) by @szokeasaurusrex
- fix(integrations): Check `retries_left` before capturing exception (#3803) by @malkovro
- fix(openai): Use name instead of description (#3807) by @sourceful-rob
- test(gcp): Only run GCP tests when they should (#3721) by @szokeasaurusrex
- chore: Shorten CI workflow names (#3805) by @sentrivana
- chore: Test with pyspark prerelease (#3760) by @sentrivana
- build(deps): bump codecov/codecov-action from 4.6.0 to 5.0.2 (#3792) by @dependabot
- build(deps): bump actions/checkout from 4.2.1 to 4.2.2 (#3691) by @dependabot

## 2.18.0

### Various fixes & improvements

- **New integration:** Add [LaunchDarkly](https://launchdarkly.com/) integration (#3648) by @cmanallen

  For more information, see the documentation for the [LaunchDarklyIntegration](https://docs.sentry.io/platforms/python/integrations/launchdarkly/).

- **New integration:** Add [OpenFeature](https://openfeature.dev/) feature flagging integration (#3648) by @cmanallen

  For more information, see the documentation for the [OpenFeatureIntegration](https://docs.sentry.io/platforms/python/integrations/openfeature/).

- Add LaunchDarkly and OpenFeature integration (#3648) by @cmanallen
- Correct typo in a comment (#3726) by @szokeasaurusrex
- End `http.client` span on timeout (#3723) by @Zylphrex
- Check for `h2` existence in HTTP/2 transport (#3690) by @BYK
- Use `type()` instead when extracting frames (#3716) by @Zylphrex
- Prefer `python_multipart` import over `multipart` (#3710) by @musicinmybrain
- Update active thread for asgi (#3669) by @Zylphrex
- Only enable HTTP2 when DSN is HTTPS (#3678) by @BYK
- Prepare for upstream Strawberry extension removal (#3649) by @DoctorJohn
- Enhance README with improved clarity and developer-friendly examples (#3667) by @UTSAVS26
- Run license compliance action on all PRs (#3699) by @szokeasaurusrex
- Run CodeQL action on all PRs (#3698) by @szokeasaurusrex
- Fix UTC assuming test (#3722) by @BYK
- Exclude fakeredis 2.26.0 on py3.6 and 3.7 (#3695) by @szokeasaurusrex
- Unpin `pytest` for `tornado-latest` tests (#3714) by @szokeasaurusrex
- Install `pytest-asyncio` for `redis` tests (Python 3.12-13) (#3706) by @szokeasaurusrex
- Clarify that only pinned tests are required (#3713) by @szokeasaurusrex
- Remove accidentally-committed print (#3712) by @szokeasaurusrex
- Disable broken RQ test in newly-released RQ 2.0 (#3708) by @szokeasaurusrex
- Unpin `pytest` for `celery` tests (#3701) by @szokeasaurusrex
- Unpin `pytest` on Python 3.8+ `gevent` tests (#3700) by @szokeasaurusrex
- Unpin `pytest` for Python 3.8+ `common` tests (#3697) by @szokeasaurusrex
- Remove `pytest` pin in `requirements-devenv.txt` (#3696) by @szokeasaurusrex
- Test with Falcon 4.0 (#3684) by @sentrivana

## 2.17.0

### Various fixes & improvements

- Add support for async calls in Anthropic and OpenAI integration (#3497) by @vetyy
- Allow custom transaction names in ASGI (#3664) by @sl0thentr0py
- Langchain: Handle case when parent span wasn't traced (#3656) by @rbasoalto
- Fix Anthropic integration when using tool calls (#3615) by @kwnath
- More defensive Django Spotlight middleware injection (#3665) by @BYK
- Remove `ensure_integration_enabled_async` (#3632) by @sentrivana
- Test with newer Falcon version (#3644, #3653, #3662) by @sentrivana
- Fix mypy (#3657) by @sentrivana
- Fix flaky transport test (#3666) by @sentrivana
- Remove pin on `sphinx` (#3650) by @sentrivana
- Bump `actions/checkout` from `4.2.0` to `4.2.1` (#3651) by @dependabot

## 2.16.0

### Integrations

- Bottle: Add `failed_request_status_codes` (#3618) by @szokeasaurusrex

  You can now define a set of integers that will determine which status codes
  should be reported to Sentry.

    ```python
    sentry_sdk.init(
        integrations=[
            BottleIntegration(
                failed_request_status_codes={403, *range(500, 600)},
            )
        ]
    )
    ```

  Examples of valid `failed_request_status_codes`:

  - `{500}` will only send events on HTTP 500.
  - `{400, *range(500, 600)}` will send events on HTTP 400 as well as the 5xx range.
  - `{500, 503}` will send events on HTTP 500 and 503.
  - `set()` (the empty set) will not send events for any HTTP status code.

  The default is `{*range(500, 600)}`, meaning that all 5xx status codes are reported to Sentry.

- Bottle: Delete never-reached code (#3605) by @szokeasaurusrex
- Redis: Remove flaky test (#3626) by @sentrivana
- Django: Improve getting `psycopg3` connection info (#3580) by @nijel
- Django: Add `SpotlightMiddleware` when Spotlight is enabled (#3600) by @BYK
- Django: Open relevant error when `SpotlightMiddleware` is on (#3614) by @BYK
- Django: Support `http_methods_to_capture` in ASGI Django (#3607) by @sentrivana

  ASGI Django now also supports the `http_methods_to_capture` integration option. This is a configurable tuple of HTTP method verbs that should create a transaction in Sentry. The default is `("CONNECT", "DELETE", "GET", "PATCH", "POST", "PUT", "TRACE",)`. `OPTIONS` and `HEAD` are not included by default.

  Here's how to use it:

  ```python
  sentry_sdk.init(
      integrations=[
          DjangoIntegration(
              http_methods_to_capture=("GET", "POST"),
          ),
      ],
  )
  ```

### Miscellaneous

- Add 3.13 to setup.py (#3574) by @sentrivana
- Add 3.13 to basepython (#3589) by @sentrivana
- Fix type of `sample_rate` in DSC (and add explanatory tests) (#3603) by @antonpirker
- Add `httpcore` based `HTTP2Transport` (#3588) by @BYK
- Add opportunistic Brotli compression (#3612) by @BYK
- Add `__notes__` support (#3620) by @szokeasaurusrex
- Remove useless makefile targets (#3604) by @antonpirker
- Simplify tox version spec (#3609) by @sentrivana
- Consolidate contributing docs (#3606) by @antonpirker
- Bump `codecov/codecov-action` from `4.5.0` to `4.6.0` (#3617) by @dependabot

## 2.15.0

### Integrations

- Configure HTTP methods to capture in ASGI/WSGI middleware and frameworks (#3531) by @antonpirker

  We've added a new option to the Django, Flask, Starlette and FastAPI integrations called `http_methods_to_capture`. This is a configurable tuple of HTTP method verbs that should create a transaction in Sentry. The default is `("CONNECT", "DELETE", "GET", "PATCH", "POST", "PUT", "TRACE",)`. `OPTIONS` and `HEAD` are not included by default.

  Here's how to use it (substitute Flask for your framework integration):

  ```python
  sentry_sdk.init(
      integrations=[
        FlaskIntegration(
            http_methods_to_capture=("GET", "POST"),
        ),
    ],
  )
  ```

- Django: Allow ASGI to use `drf_request` in `DjangoRequestExtractor` (#3572) by @PakawiNz
- Django: Don't let `RawPostDataException` bubble up (#3553) by @sentrivana
- Django: Add `sync_capable` to `SentryWrappingMiddleware` (#3510) by @szokeasaurusrex
- AIOHTTP: Add `failed_request_status_codes` (#3551) by @szokeasaurusrex

  You can now define a set of integers that will determine which status codes
  should be reported to Sentry.

    ```python
    sentry_sdk.init(
        integrations=[
            AioHttpIntegration(
                failed_request_status_codes={403, *range(500, 600)},
            )
        ]
    )
    ```

  Examples of valid `failed_request_status_codes`:

  - `{500}` will only send events on HTTP 500.
  - `{400, *range(500, 600)}` will send events on HTTP 400 as well as the 5xx range.
  - `{500, 503}` will send events on HTTP 500 and 503.
  - `set()` (the empty set) will not send events for any HTTP status code.

  The default is `{*range(500, 600)}`, meaning that all 5xx status codes are reported to Sentry.

- AIOHTTP: Delete test which depends on AIOHTTP behavior (#3568) by @szokeasaurusrex
- AIOHTTP: Handle invalid responses (#3554) by @szokeasaurusrex
- FastAPI/Starlette: Support new `failed_request_status_codes` (#3563) by @szokeasaurusrex

  The format of `failed_request_status_codes` has changed from a list
  of integers and containers to a set:

  ```python
  sentry_sdk.init(
      integrations=StarletteIntegration(
          failed_request_status_codes={403, *range(500, 600)},
      ),
  )
  ```

  The old way of defining `failed_request_status_codes` will continue to work
  for the time being. Examples of valid new-style `failed_request_status_codes`:

  - `{500}` will only send events on HTTP 500.
  - `{400, *range(500, 600)}` will send events on HTTP 400 as well as the 5xx range.
  - `{500, 503}` will send events on HTTP 500 and 503.
  - `set()` (the empty set) will not send events for any HTTP status code.

  The default is `{*range(500, 600)}`, meaning that all 5xx status codes are reported to Sentry.

- FastAPI/Starlette: Fix `failed_request_status_codes=[]` (#3561) by @szokeasaurusrex
- FastAPI/Starlette: Remove invalid `failed_request_status_code` tests (#3560) by @szokeasaurusrex
- FastAPI/Starlette: Refactor shared test parametrization (#3562) by @szokeasaurusrex

### Miscellaneous

- Deprecate `sentry_sdk.metrics` (#3512) by @szokeasaurusrex
- Add `name` parameter to `start_span()` and deprecate `description` parameter (#3524 & #3525) by @antonpirker
- Fix `add_query_source` with modules outside of project root (#3313) by @rominf
- Test more integrations on 3.13 (#3578) by @sentrivana
- Fix trailing whitespace (#3579) by @sentrivana
- Improve `get_integration` typing (#3550) by @szokeasaurusrex
- Make import-related tests stable (#3548) by @BYK
- Fix breadcrumb sorting (#3511) by @sentrivana
- Fix breadcrumb timestamp casting and its tests (#3546) by @BYK
- Don't use deprecated `logger.warn` (#3552) by @sentrivana
- Fix Cohere API change (#3549) by @BYK
- Fix deprecation message (#3536) by @antonpirker
- Remove experimental `explain_plan` feature. (#3534) by @antonpirker
- X-fail one of the Lambda tests (#3592) by @antonpirker
- Update Codecov config (#3507) by @antonpirker
- Update `actions/upload-artifact` to `v4` with merge (#3545) by @joshuarli
- Bump `actions/checkout` from `4.1.7` to `4.2.0` (#3585) by @dependabot

## 2.14.0

### Various fixes & improvements

- New `SysExitIntegration` (#3401) by @szokeasaurusrex

  For more information, see the documentation for the [SysExitIntegration](https://docs.sentry.io/platforms/python/integrations/sys_exit).

- Add `SENTRY_SPOTLIGHT` env variable support (#3443) by @BYK
- Support Strawberry `0.239.2` (#3491) by @szokeasaurusrex
- Add separate `pii_denylist` to `EventScrubber` and run it always (#3463) by @sl0thentr0py
- Celery: Add wrapper for `Celery().send_task` to support behavior as `Task.apply_async` (#2377) by @divaltor
- Django: SentryWrappingMiddleware.__init__ fails if super() is object (#2466) by @cameron-simpson
- Fix data_category for sessions envelope items (#3473) by @sl0thentr0py
- Fix non-UTC timestamps (#3461) by @szokeasaurusrex
- Remove obsolete object as superclass (#3480) by @sentrivana
- Replace custom `TYPE_CHECKING` with stdlib `typing.TYPE_CHECKING` (#3447) by @dev-satoshi
- Refactor `tracing_utils.py` (#3452) by @rominf
- Explicitly export symbol in subpackages instead of ignoring (#3400) by @hartungstenio
- Better test coverage reports (#3498) by @antonpirker
- Fixed config for old coverage versions (#3504) by @antonpirker
- Fix AWS Lambda tests (#3495) by @antonpirker
- Remove broken Bottle tests (#3505) by @sentrivana

## 2.13.0

### Various fixes & improvements

- **New integration:** [Ray](https://docs.sentry.io/platforms/python/integrations/ray/) (#2400) (#2444) by @glowskir

  Usage: (add the RayIntegration to your `sentry_sdk.init()` call and make sure it is called in the worker processes)
  ```python
  import ray

  import sentry_sdk
  from sentry_sdk.integrations.ray import RayIntegration

  def init_sentry():
      sentry_sdk.init(
          dsn="...",
          traces_sample_rate=1.0,
          integrations=[RayIntegration()],
      )

  init_sentry()

  ray.init(
      runtime_env=dict(worker_process_setup_hook=init_sentry),
  )
  ```
  For more information, see the documentation for the [Ray integration](https://docs.sentry.io/platforms/python/integrations/ray/).

- **New integration:** [Litestar](https://docs.sentry.io/platforms/python/integrations/litestar/) (#2413) (#3358) by @KellyWalker

  Usage: (add the LitestarIntegration to your `sentry_sdk.init()`)
  ```python
  from litestar import Litestar, get

  import sentry_sdk
  from sentry_sdk.integrations.litestar import LitestarIntegration

  sentry_sdk.init(
      dsn="...",
      traces_sample_rate=1.0,
      integrations=[LitestarIntegration()],
  )

  @get("/")
  async def index() -> str:
      return "Hello, world!"

  app = Litestar(...)
  ```
  For more information, see the documentation for the [Litestar integration](https://docs.sentry.io/platforms/python/integrations/litestar/).

- **New integration:** [Dramatiq](https://docs.sentry.io/platforms/python/integrations/dramatiq/) from @jacobsvante (#3397) by @antonpirker
  Usage: (add the DramatiqIntegration to your `sentry_sdk.init()`)
  ```python
  import dramatiq

  import sentry_sdk
  from sentry_sdk.integrations.dramatiq import DramatiqIntegration

  sentry_sdk.init(
      dsn="...",
      traces_sample_rate=1.0,
      integrations=[DramatiqIntegration()],
  )

  @dramatiq.actor(max_retries=0)
  def dummy_actor(x, y):
      return x / y

  dummy_actor.send(12, 0)
  ```

  For more information, see the documentation for the [Dramatiq integration](https://docs.sentry.io/platforms/python/integrations/dramatiq/).

- **New config option:** Expose `custom_repr` function that precedes `safe_repr` invocation in serializer (#3438) by @sl0thentr0py

  See: https://docs.sentry.io/platforms/python/configuration/options/#custom-repr

- Profiling: Add client SDK info to profile chunk (#3386) by @Zylphrex
- Serialize vars early to avoid living references (#3409) by @sl0thentr0py
- Deprecate hub-based `sessions.py` logic (#3419) by @szokeasaurusrex
- Deprecate `is_auto_session_tracking_enabled` (#3428) by @szokeasaurusrex
- Add note to generated yaml files (#3423) by @sentrivana
- Slim down PR template (#3382) by @sentrivana
- Use new banner in readme (#3390) by @sentrivana

## 2.12.0

### Various fixes & improvements

- API: Expose the scope getters to top level API and use them everywhere  (#3357) by @sl0thentr0py
- API: `push_scope` deprecation warning (#3355) (#3355) by @szokeasaurusrex
- API: Replace `push_scope` (#3353, #3354) by @szokeasaurusrex
- API: Deprecate, avoid, or stop using `configure_scope` (#3348, #3349, #3350, #3351) by @szokeasaurusrex
- OTel: Remove experimental autoinstrumentation (#3239) by @sentrivana
- Graphene: Add span for grapqhl operation (#2788) by @czyber
- AI: Add async support for `ai_track` decorator (#3376) by @czyber
- CI: Workaround bug preventing Django test runs (#3371) by @szokeasaurusrex
- CI: Remove Django setuptools pin (#3378) by @szokeasaurusrex
- Tests: Test with Django 5.1 RC (#3370) by @sentrivana
- Broaden `add_attachment` type (#3342) by @szokeasaurusrex
- Add span data to the transactions trace context (#3374) by @antonpirker
- Gracefully fail attachment path not found case (#3337) by @sl0thentr0py
- Document attachment parameters (#3342) by @szokeasaurusrex
- Bump checkouts/data-schemas from `0feb234` to `6d2c435` (#3369) by @dependabot
- Bump checkouts/data-schemas from `88273a9` to `0feb234` (#3252) by @dependabot

## 2.11.0

### Various fixes & improvements

- Add `disabled_integrations` (#3328) by @sentrivana

  Disabling individual integrations is now much easier.
  Instead of disabling all automatically enabled integrations and specifying the ones
  you want to keep, you can now use the new
  [`disabled_integrations`](https://docs.sentry.io/platforms/python/configuration/options/#auto-enabling-integrations)
  config option to provide a list of integrations to disable:

  ```python
  import sentry_sdk
  from sentry_sdk.integrations.flask import FlaskIntegration

  sentry_sdk.init(
      # Do not use the Flask integration even if Flask is installed.
      disabled_integrations=[
          FlaskIntegration(),
      ],
  )
  ```

- Use operation name as transaction name in Strawberry (#3294) by @sentrivana
- WSGI integrations respect `SCRIPT_NAME` env variable (#2622) by @sarvaSanjay
- Make Django DB spans have origin `auto.db.django` (#3319) by @antonpirker
- Sort breadcrumbs by time before sending (#3307) by @antonpirker
- Fix `KeyError('sentry-monitor-start-timestamp-s')` (#3278) by @Mohsen-Khodabakhshi
- Set MongoDB tags directly on span data (#3290) by @0Calories
- Lower logger level for some messages (#3305) by @sentrivana and @antonpirker
- Emit deprecation warnings from `Hub` API (#3280) by @szokeasaurusrex
- Clarify that `instrumenter` is internal-only (#3299) by @szokeasaurusrex
- Support Django 5.1 (#3207) by @sentrivana
- Remove apparently unnecessary `if` (#3298) by @szokeasaurusrex
- Preliminary support for Python 3.13 (#3200) by @sentrivana
- Move `sentry_sdk.init` out of `hub.py` (#3276) by @szokeasaurusrex
- Unhardcode integration list (#3240) by @rominf
- Allow passing of PostgreSQL port in tests (#3281) by @rominf
- Add tests for `@ai_track` decorator (#3325) by @colin-sentry
- Do not include type checking code in coverage report (#3327) by @antonpirker
- Fix test_installed_modules (#3309) by @szokeasaurusrex
- Fix typos and grammar in a comment (#3293) by @szokeasaurusrex
- Fixed failed tests setup (#3303) by @antonpirker
- Only assert warnings we are interested in (#3314) by @szokeasaurusrex

## 2.10.0

### Various fixes & improvements

- Add client cert and key support to `HttpTransport` (#3258) by @grammy-jiang

  Add `cert_file` and `key_file` to your `sentry_sdk.init` to use a custom client cert and key. Alternatively, the environment variables `CLIENT_CERT_FILE` and `CLIENT_KEY_FILE` can be used as well.

- OpenAI: Lazy initialize tiktoken to avoid http at import time (#3287) by @colin-sentry
- OpenAI, Langchain: Make tiktoken encoding name configurable + tiktoken usage opt-in (#3289) by @colin-sentry

  Fixed a bug where having certain packages installed along the Sentry SDK caused an HTTP request to be made to OpenAI infrastructure when the Sentry SDK was initialized. The request was made when the `tiktoken` package and at least one of the `openai` or `langchain` packages were installed.

  The request was fetching a `tiktoken` encoding in order to correctly measure token usage in some OpenAI and Langchain calls. This behavior is now opt-in. The choice of encoding to use was made configurable as well. To opt in, set the `tiktoken_encoding_name` parameter in the OpenAPI or Langchain integration.

  ```python
  sentry_sdk.init(
      integrations=[
          OpenAIIntegration(tiktoken_encoding_name="cl100k_base"),
          LangchainIntegration(tiktoken_encoding_name="cl100k_base"),
      ],
  )
  ```

- PyMongo: Send query description as valid JSON (#3291) by @0Calories
- Remove Python 2 compatibility code (#3284) by @szokeasaurusrex
- Fix `sentry_sdk.init` type hint (#3283) by @szokeasaurusrex
- Deprecate `hub` in `Profile` (#3270) by @szokeasaurusrex
- Stop using `Hub` in `init` (#3275) by @szokeasaurusrex
- Delete `_should_send_default_pii` (#3274) by @szokeasaurusrex
- Remove `Hub` usage in `conftest` (#3273) by @szokeasaurusrex
- Rename debug logging filter (#3260) by @szokeasaurusrex
- Update `NoOpSpan.finish` signature (#3267) by @szokeasaurusrex
- Remove `Hub` in `Transaction.finish` (#3267) by @szokeasaurusrex
- Remove Hub from `capture_internal_exception` logic (#3264) by @szokeasaurusrex
- Improve `Scope._capture_internal_exception` type hint (#3264) by @szokeasaurusrex
- Correct `ExcInfo` type (#3266) by @szokeasaurusrex
- Stop using `Hub` in `tracing_utils` (#3269) by @szokeasaurusrex

## 2.9.0

### Various fixes & improvements

- ref(transport): Improve event data category typing (#3243) by @szokeasaurusrex
- ref(tracing): Improved handling of span status (#3261) by @antonpirker
- test(client): Add tests for dropped span client reports (#3244) by @szokeasaurusrex
- test(transport): Test new client report features (#3244) by @szokeasaurusrex
- feat(tracing): Record lost spans in client reports (#3244) by @szokeasaurusrex
- test(sampling): Replace custom logic with `capture_record_lost_event_calls` (#3257) by @szokeasaurusrex
- test(transport): Non-order-dependent discarded events assertion (#3255) by @szokeasaurusrex
- test(core): Introduce `capture_record_lost_event_calls` fixture (#3254) by @szokeasaurusrex
- test(core): Fix non-idempotent test (#3253) by @szokeasaurusrex

## 2.8.0

### Various fixes & improvements

- `profiler_id` uses underscore (#3249) by @Zylphrex
- Don't send full env to subprocess (#3251) by @kmichel-aiven
- Stop using `Hub` in `HttpTransport` (#3247) by @szokeasaurusrex
- Remove `ipdb` from test requirements (#3237) by @rominf
- Avoid propagation of empty baggage (#2968) by @hartungstenio
- Add entry point for `SentryPropagator` (#3086) by @mender
- Bump checkouts/data-schemas from `8c13457` to `88273a9` (#3225) by @dependabot

## 2.7.1

### Various fixes & improvements

- fix(otel): Fix missing baggage (#3218) by @sentrivana
- This is the config file of asdf-vm which we do not use. (#3215) by @antonpirker
- Added option to disable middleware spans in Starlette (#3052) by @antonpirker
- build: Update tornado version in setup.py to match code check. (#3206) by @aclemons

## 2.7.0

- Add `origin` to spans and transactions (#3133) by @antonpirker
- OTel: Set up typing for OTel (#3168) by @sentrivana
- OTel: Auto instrumentation skeleton (#3143) by @sentrivana
- OpenAI: If there is an internal error, still return a value (#3192) by @colin-sentry
- MongoDB: Add MongoDB collection span tag (#3182) by @0Calories
- MongoDB: Change span operation from `db.query` to `db` (#3186) by @0Calories
- MongoDB: Remove redundant command name in query description (#3189) by @0Calories
- Apache Spark: Fix spark driver integration (#3162) by @seyoon-lim
- Apache Spark: Add Spark test suite to tox.ini and to CI (#3199) by @sentrivana
- Codecov: Add failed test commits in PRs (#3190) by @antonpirker
- Update library, Python versions in tests (#3202) by @sentrivana
- Remove Hub from our test suite (#3197) by @antonpirker
- Use env vars for default CA cert bundle location (#3160) by @DragoonAethis
- Create a separate test group for AI (#3198) by @sentrivana
- Add additional stub packages for type checking (#3122) by @Daverball
- Proper naming of requirements files (#3191) by @antonpirker
- Pinning pip because new version does not work with some versions of Celery and Httpx (#3195) by @antonpirker
- build(deps): bump supercharge/redis-github-action from 1.7.0 to 1.8.0 (#3193) by @dependabot
- build(deps): bump actions/checkout from 4.1.6 to 4.1.7 (#3171) by @dependabot
- build(deps): update pytest-asyncio requirement (#3087) by @dependabot

## 2.6.0

- Introduce continuous profiling mode (#2830) by @Zylphrex
- Profiling: Add deprecation comment for profiler internals (#3167) by @sentrivana
- Profiling: Move thread data to trace context (#3157) by @Zylphrex
- Explicitly export cron symbols for typecheckers (#3072) by @spladug
- Cleaning up ASGI tests for Django (#3180) by @antonpirker
- Celery: Add Celery receive latency (#3174) by @antonpirker
- Metrics: Update type hints for tag values (#3156) by @elramen
- Django: Fix psycopg3 reconnect error (#3111) by @szokeasaurusrex
- Tracing: Keep original function signature when decorated (#3178) by @sentrivana
- Reapply "Refactor the Celery Beat integration (#3105)" (#3144) (#3175) by @antonpirker
- Added contributor image to readme (#3183) by @antonpirker
- bump actions/checkout from 4.1.4 to 4.1.6 (#3147) by @dependabot
- bump checkouts/data-schemas from `59f9683` to `8c13457` (#3146) by @dependabot

## 2.5.1

This change fixes a regression in our cron monitoring feature, which caused cron checkins not to be sent. The regression appears to have been introduced in version 2.4.0.

**We recommend that all users, who use Cron monitoring and are currently running sentry-python ≥2.4.0, upgrade to this release as soon as possible!**

### Other fixes & improvements

- feat(tracing): Warn if not-started transaction entered (#3003) by @szokeasaurusrex
- test(scope): Ensure `last_event_id` cleared (#3124) by @szokeasaurusrex
- fix(scope): Clear last_event_id on scope clear (#3124) by @szokeasaurusrex

## 2.5.0

### Various fixes & improvements

- Allow to configure status codes to report to Sentry in Starlette and FastAPI (#3008) by @sentrivana

  By passing a new option to the FastAPI and Starlette integrations, you're now able to configure what
  status codes should be sent as events to Sentry. Here's how it works:

  ```python
  from sentry_sdk.integrations.starlette import StarletteIntegration
  from sentry_sdk.integrations.fastapi import FastApiIntegration

  sentry_sdk.init(
      # ...
      integrations=[
          StarletteIntegration(
              failed_request_status_codes=[403, range(500, 599)],
          ),
          FastApiIntegration(
              failed_request_status_codes=[403, range(500, 599)],
          ),
      ]
  )
  ```

  `failed_request_status_codes` expects a list of integers or containers (objects that allow membership checks via `in`)
  of integers. Examples of valid `failed_request_status_codes`:

  - `[500]` will only send events on HTTP 500.
  - `[400, range(500, 599)]` will send events on HTTP 400 as well as the 500-599 range.
  - `[500, 503]` will send events on HTTP 500 and 503.

  The default is `[range(500, 599)]`.

  See the [FastAPI](https://docs.sentry.io/platforms/python/integrations/fastapi/) and [Starlette](https://docs.sentry.io/platforms/python/integrations/starlette/) integration docs for more details.

- Support multiple keys with `cache_prefixes` (#3136) by @sentrivana
- Support integer Redis keys (#3132) by @sentrivana
- Update SDK version in CONTRIBUTING.md (#3129) by @sentrivana
- Bump actions/checkout from 4.1.4 to 4.1.5 (#3067) by @dependabot

## 2.4.0

### Various fixes & improvements

- Celery: Made `cache.key` span data field a list (#3110) by @antonpirker
- Celery Beat: Refactor the Celery Beat integration (#3105) by @antonpirker
- GRPC: Add None check for grpc.aio interceptor (#3109) by @ordinary-jamie
- Docs: Remove `last_event_id` from migration guide (#3126) by @szokeasaurusrex
- fix(django): Proper transaction names for i18n routes (#3104) by @sentrivana
- fix(scope): Copy `_last_event_id` in `Scope.__copy__` (#3123) by @szokeasaurusrex
- fix(tests): Adapt to new Anthropic version (#3119) by @sentrivana
- build(deps): bump checkouts/data-schemas from `4381a97` to `59f9683` (#3066) by @dependabot

## 2.3.1

### Various fixes & improvements

- Handle also byte arras as strings in Redis caches (#3101) by @antonpirker
- Do not crash exceptiongroup (by patching excepthook and keeping the name of the function) (#3099) by @antonpirker

## 2.3.0

### Various fixes & improvements

- NEW: Redis integration supports now Sentry Caches module. See https://docs.sentry.io/product/performance/caches/ (#3073) by @antonpirker
- NEW: Django integration supports now Sentry Caches module. See https://docs.sentry.io/product/performance/caches/ (#3009) by @antonpirker
- Fix `cohere` testsuite for new release of `cohere` (#3098) by @antonpirker
- Fix ClickHouse integration where `_sentry_span` might be missing (#3096) by @sentrivana

## 2.2.1

### Various fixes & improvements

- Add conditional check for delivery_info's existence (#3083) by @cmanallen
- Updated deps for latest langchain version (#3092) by @antonpirker
- Fixed grpcio extras to work as described in the docs (#3081) by @antonpirker
- Use pythons venv instead of virtualenv to create virtual envs (#3077) by @antonpirker
- Celery: Add comment about kwargs_headers (#3079) by @szokeasaurusrex
- Celery: Queues module producer implementation (#3079) by @szokeasaurusrex
- Fix N803 flake8 failures (#3082) by @szokeasaurusrex

## 2.2.0

### New features

- Celery integration now sends additional data to Sentry to enable new features to guage the health of your queues
- Added a new integration for Cohere
- Reintroduced the `last_event_id` function, which had been removed in 2.0.0

### Other fixes & improvements

- Add tags + data passing functionality to @ai_track (#3071) by @colin-sentry
- Only propagate headers from spans within transactions (#3070) by @szokeasaurusrex
- Improve type hints for set metrics (#3048) by @elramen
- Fix `get_client` typing (#3063) by @szokeasaurusrex
- Auto-enable Anthropic integration + gate imports (#3054) by @colin-sentry
- Made `MeasurementValue.unit` NotRequired (#3051) by @antonpirker

## 2.1.1

- Fix trace propagation in Celery tasks started by Celery Beat. (#3047) by @antonpirker

## 2.1.0

- fix(quart): Fix Quart integration (#3043) by @szokeasaurusrex

- **New integration:** [Langchain](https://docs.sentry.io/platforms/python/integrations/langchain/) (#2911) by @colin-sentry

  Usage: (Langchain is auto enabling, so you do not need to do anything special)
  ```python
  from langchain_openai import ChatOpenAI
  import sentry_sdk

  sentry_sdk.init(
      dsn="...",
      enable_tracing=True,
      traces_sample_rate=1.0,
  )

  llm = ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0)
  ```

  Check out [the LangChain docs](https://docs.sentry.io/platforms/python/integrations/langchain/) for details.

- **New integration:** [Anthropic](https://docs.sentry.io/platforms/python/integrations/anthropic/) (#2831) by @czyber

  Usage: (add the AnthropicIntegration to your `sentry_sdk.init()` call)
  ```python
  from anthropic import Anthropic

  import sentry_sdk

  sentry_sdk.init(
      dsn="...",
      enable_tracing=True,
      traces_sample_rate=1.0,
      integrations=[AnthropicIntegration()],
  )

  client = Anthropic()
  ```
  Check out [the Anthropic docs](https://docs.sentry.io/platforms/python/integrations/anthropic/) for details.

- **New integration:** [Huggingface Hub](https://docs.sentry.io/platforms/python/integrations/huggingface/) (#3033) by @colin-sentry

  Usage: (Huggingface Hub is auto enabling, so you do not need to do anything special)

  ```python
  import sentry_sdk
  from huggingface_hub import InferenceClient

  sentry_sdk.init(
      dsn="...",
      enable_tracing=True,
      traces_sample_rate=1.0,
  )

  client = InferenceClient("some-model")
  ```

  Check out [the Huggingface docs](https://docs.sentry.io/platforms/python/integrations/huggingface/) for details. (comming soon!)

- fix(huggingface): Reduce API cross-section for huggingface in test (#3042) by @colin-sentry
- fix(django): Fix Django ASGI integration on Python 3.12 (#3027) by @bellini666
- feat(perf): Add ability to put measurements directly on spans. (#2967) by @colin-sentry
- fix(tests): Fix trytond tests (#3031) by @sentrivana
- fix(tests): Update `pytest-asyncio` to fix CI (#3030) by @sentrivana
- fix(docs): Link to respective migration guides directly (#3020) by @sentrivana
- docs(scope): Add docstring to `Scope.set_tags` (#2978) by @szokeasaurusrex
- test(scope): Fix typos in assert error message (#2978) by @szokeasaurusrex
- feat(scope): New `set_tags` function (#2978) by @szokeasaurusrex
- test(scope): Add unit test for `Scope.set_tags` (#2978) by @szokeasaurusrex
- feat(scope): Add `set_tags` to top-level API (#2978) by @szokeasaurusrex
- test(scope): Add unit test for top-level API `set_tags` (#2978) by @szokeasaurusrex
- feat(tests): Parallelize tox (#3025) by @sentrivana
- build(deps): Bump checkouts/data-schemas from `4aa14a7` to `4381a97` (#3028) by @dependabot
- meta(license): Bump copyright year (#3029) by @szokeasaurusrex

## 2.0.1

### Various fixes & improvements

- Fix: Do not use convenience decorator (#3022) by @sentrivana
- Refactoring propagation context (#2970) by @antonpirker
- Use `pid` for test database name in Django tests (#2998) by @antonpirker
- Remove outdated RC mention in docs (#3018) by @sentrivana
- Delete inaccurate comment from docs (#3002) by @szokeasaurusrex
- Add Lambda function that deletes test Lambda functions (#2960) by @antonpirker
- Correct discarded transaction debug message (#3002) by @szokeasaurusrex
- Add tests for discarded transaction debug messages (#3002) by @szokeasaurusrex
- Fix comment typo in metrics (#2992) by @szokeasaurusrex
- build(deps): bump actions/checkout from 4.1.1 to 4.1.4 (#3011) by @dependabot
- build(deps): bump checkouts/data-schemas from `1e17eb5` to `4aa14a7` (#2997) by @dependabot

## 2.0.0

This is the first major update in a *long* time!

We dropped support for some ancient languages and frameworks (Yes, Python 2.7 is no longer supported). Additionally we refactored a big part of the foundation of the SDK (how data inside the SDK is handled).

We hope you like it!

For a shorter version of what you need to do, to upgrade to Sentry SDK 2.0 see: https://docs.sentry.io/platforms/python/migration/1.x-to-2.x

### New Features

- Additional integrations will now be activated automatically if the SDK detects the respective package is installed: Ariadne, ARQ, asyncpg, Chalice, clickhouse-driver, GQL, Graphene, huey, Loguru, PyMongo, Quart, Starlite, Strawberry.
- Added new API for custom instrumentation: `new_scope`, `isolation_scope`. See the [Deprecated](#deprecated) section to see how they map to the existing APIs.

### Changed
(These changes are all backwards-incompatible. **Breaking Change** (if you are just skimming for that phrase))

- The Pyramid integration will not capture errors that might happen in `authenticated_userid()` in a custom `AuthenticationPolicy` class.
- The method `need_code_loation` of the `MetricsAggregator` was renamed to `need_code_location`.
- The `BackgroundWorker` thread used to process events was renamed from `raven-sentry.BackgroundWorker` to `sentry-sdk.BackgroundWorker`.
- The `reraise` function was moved from `sentry_sdk._compat` to `sentry_sdk.utils`.
- The `_ScopeManager` was moved from `sentry_sdk.hub` to `sentry_sdk.scope`.
- Moved the contents of `tracing_utils_py3.py` to `tracing_utils.py`. The `start_child_span_decorator` is now in `sentry_sdk.tracing_utils`.
- The actual implementation of `get_current_span` was moved to `sentry_sdk.tracing_utils`. `sentry_sdk.get_current_span` is still accessible as part of the top-level API.
- `sentry_sdk.tracing_utils.add_query_source()`: Removed the `hub` parameter. It is not necessary anymore.
- `sentry_sdk.tracing_utils.record_sql_queries()`: Removed the `hub` parameter. It is not necessary anymore.
- `sentry_sdk.tracing_utils.get_current_span()` does now take a `scope` instead of a `hub` as parameter.
- `sentry_sdk.tracing_utils.should_propagate_trace()` now takes a `Client` instead of a `Hub` as first parameter.
- `sentry_sdk.utils.is_sentry_url()` now takes a `Client` instead of a `Hub` as first parameter.
- `sentry_sdk.utils._get_contextvars` does not return a tuple with three values, but a tuple with two values. The `copy_context` was removed.
- If you create a transaction manually and later mutate the transaction in a `configure_scope` block this does not work anymore. Here is a recipe on how to change your code to make it work:
    Your existing implementation:
    ```python
    transaction = sentry_sdk.transaction(...)

    # later in the code execution:

    with sentry_sdk.configure_scope() as scope:
        scope.set_transaction_name("new-transaction-name")
    ```

    needs to be changed to this:
    ```python
    transaction = sentry_sdk.transaction(...)

    # later in the code execution:

    scope = sentry_sdk.get_current_scope()
    scope.set_transaction_name("new-transaction-name")
    ```
- The classes listed in the table below are now abstract base classes. Therefore, they can no longer be instantiated. Subclasses can only be instantiated if they implement all of the abstract methods.
  <details>
    <summary><b>Show table</b></summary>

  | Class                                 | Abstract methods                       |
  | ------------------------------------- | -------------------------------------- |
  | `sentry_sdk.integrations.Integration` | `setup_once`                           |
  | `sentry_sdk.metrics.Metric`           | `add`, `serialize_value`, and `weight` |
  | `sentry_sdk.profiler.Scheduler`       | `setup` and `teardown`                 |
  | `sentry_sdk.transport.Transport`      | `capture_envelope`                     |

    </details>

### Removed
(These changes are all backwards-incompatible. **Breaking Change** (if you are just skimming for that phrase))

- Removed support for Python 2 and Python 3.5. The SDK now requires at least Python 3.6.
- Removed support for Celery 3.\*.
- Removed support for Django 1.8, 1.9, 1.10.
- Removed support for Flask 0.\*.
- Removed support for gRPC < 1.39.
- Removed support for Tornado < 6.
- Removed `last_event_id()` top level API. The last event ID is still returned by `capture_event()`, `capture_exception()` and `capture_message()` but the top level API `sentry_sdk.last_event_id()` has been removed.
- Removed support for sending events to the `/store` endpoint. Everything is now sent to the `/envelope` endpoint. If you're on SaaS you don't have to worry about this, but if you're running Sentry yourself you'll need version `20.6.0` or higher of self-hosted Sentry.
- The deprecated `with_locals` configuration option was removed. Use `include_local_variables` instead. See https://docs.sentry.io/platforms/python/configuration/options/#include-local-variables.
- The deprecated `request_bodies` configuration option was removed. Use `max_request_body_size`. See https://docs.sentry.io/platforms/python/configuration/options/#max-request-body-size.
- Removed support for `user.segment`. It was also removed from the trace header as well as from the dynamic sampling context.
- Removed support for the `install` method for custom integrations. Please use `setup_once` instead.
- Removed `sentry_sdk.tracing.Span.new_span`. Use `sentry_sdk.tracing.Span.start_child` instead.
- Removed `sentry_sdk.tracing.Transaction.new_span`. Use `sentry_sdk.tracing.Transaction.start_child` instead.
- Removed support for creating transactions via `sentry_sdk.tracing.Span(transaction=...)`. To create a transaction, please use `sentry_sdk.tracing.Transaction(name=...)`.
- Removed `sentry_sdk.utils.Auth.store_api_url`.
- `sentry_sdk.utils.Auth.get_api_url`'s now accepts a `sentry_sdk.consts.EndpointType` enum instead of a string as its only parameter. We recommend omitting this argument when calling the function, since the parameter's default value is the only possible `sentry_sdk.consts.EndpointType` value. The parameter exists for future compatibility.
- Removed `tracing_utils_py2.py`. The `start_child_span_decorator` is now in `sentry_sdk.tracing_utils`.
- Removed the `sentry_sdk.profiler.Scheduler.stop_profiling` method. Any calls to this method can simply be removed, since this was a no-op method.

### Deprecated

- Using the `Hub` directly as well as using hub-based APIs has been deprecated. Where available, use [the top-level API instead](sentry_sdk/api.py); otherwise use the [scope API](sentry_sdk/scope.py) or the [client API](sentry_sdk/client.py).

  Before:

  ```python
  with hub.start_span(...):
      # do something
  ```

  After:

  ```python
  import sentry_sdk

  with sentry_sdk.start_span(...):
      # do something
  ```

- Hub cloning is deprecated.

  Before:

  ```python
  with Hub(Hub.current) as hub:
      # do something with the cloned hub
  ```

  After:

  ```python
  import sentry_sdk

  with sentry_sdk.isolation_scope() as scope:
      # do something with the forked scope
  ```

- `configure_scope` is deprecated. Use the new isolation scope directly via `get_isolation_scope()` instead.

  Before:

  ```python
  with configure_scope() as scope:
      # do something with `scope`
  ```

  After:

  ```python
  from sentry_sdk import get_isolation_scope

  scope = get_isolation_scope()
  # do something with `scope`
  ```

- `push_scope` is deprecated. Use the new `new_scope` context manager to fork the necessary scopes.

  Before:

  ```python
  with push_scope() as scope:
      # do something with `scope`
  ```

  After:

  ```python
  import sentry_sdk

  with sentry_sdk.new_scope() as scope:
      # do something with `scope`
  ```

- Accessing the client via the hub has been deprecated. Use the top-level `sentry_sdk.get_client()` to get the current client.
- `profiler_mode` and `profiles_sample_rate` have been deprecated as `_experiments` options. Use them as top level options instead:
  ```python
  sentry_sdk.init(
      ...,
      profiler_mode="thread",
      profiles_sample_rate=1.0,
  )
  ```
- Deprecated `sentry_sdk.transport.Transport.capture_event`. Please use `sentry_sdk.transport.Transport.capture_envelope`, instead.
- Passing a function to `sentry_sdk.init`'s `transport` keyword argument has been deprecated. If you wish to provide a custom transport, please pass a `sentry_sdk.transport.Transport` instance or a subclass.
- The parameter `propagate_hub` in `ThreadingIntegration()` was deprecated and renamed to `propagate_scope`.

## 1.45.0

This is the final 1.x release for the forseeable future. Development will continue on the 2.x release line. The first 2.x version will be available in the next few weeks.

### Various fixes & improvements

- Allow to upsert monitors (#2929) by @sentrivana

  It's now possible to provide `monitor_config` to the `monitor` decorator/context manager directly:

  ```python
  from sentry_sdk.crons import monitor

  # All keys except `schedule` are optional
  monitor_config = {
      "schedule": {"type": "crontab", "value": "0 0 * * *"},
      "timezone": "Europe/Vienna",
      "checkin_margin": 10,
      "max_runtime": 10,
      "failure_issue_threshold": 5,
      "recovery_threshold": 5,
  }

  @monitor(monitor_slug='<monitor-slug>', monitor_config=monitor_config)
  def tell_the_world():
      print('My scheduled task...')
  ```

  Check out [the cron docs](https://docs.sentry.io/platforms/python/crons/) for details.

- Add Django `signals_denylist` to filter signals that are attached to by `signals_spans` (#2758) by @lieryan

  If you want to exclude some Django signals from performance tracking, you can use the new `signals_denylist` Django option:

  ```python
  import django.db.models.signals
  import sentry_sdk

  sentry_sdk.init(
      ...
      integrations=[
          DjangoIntegration(
              ...
              signals_denylist=[
                  django.db.models.signals.pre_init,
                  django.db.models.signals.post_init,
              ],
          ),
      ],
  )
  ```

- `increment` for metrics (#2588) by @mitsuhiko

  `increment` and `inc` are equivalent, so you can pick whichever you like more.

- Add `value`, `unit` to `before_emit_metric` (#2958) by @sentrivana

  If you add a custom `before_emit_metric`, it'll now accept 4 arguments (the `key`, `value`, `unit` and `tags`) instead of just `key` and `tags`.

  ```python
  def before_emit(key, value, unit, tags):
      if key == "removed-metric":
          return False
      tags["extra"] = "foo"
      del tags["release"]
      return True

  sentry_sdk.init(
      ...
      _experiments={
          "before_emit_metric": before_emit,
      }
  )
  ```

- Remove experimental metric summary options (#2957) by @sentrivana

  The `_experiments` options `metrics_summary_sample_rate` and `should_summarize_metric` have been removed.

- New normalization rules for metric keys, names, units, tags (#2946) by @sentrivana
- Change `data_category` from `statsd` to `metric_bucket` (#2954) by @cleptric
- Accessing `__mro__` might throw a `ValueError` (#2952) by @sentrivana
- Suppress prompt spawned by subprocess when using `pythonw` (#2936) by @collinbanko
- Handle `None` in GraphQL query #2715 (#2762) by @czyber
- Do not send "quiet" Sanic exceptions to Sentry (#2821) by @hamedsh
- Implement `metric_bucket` rate limits (#2933) by @cleptric
- Fix type hints for `monitor` decorator (#2944) by @szokeasaurusrex
- Remove deprecated `typing` imports in crons (#2945) by @szokeasaurusrex
- Make `monitor_config` a `TypedDict` (#2931) by @sentrivana
- Add `devenv-requirements.txt` and update env setup instructions (#2761) by @arr-ee
- Bump `types-protobuf` from `4.24.0.20240311` to `4.24.0.20240408` (#2941) by @dependabot
- Disable Codecov check run annotations (#2537) by @eliatcodecov

## 1.44.1

### Various fixes & improvements

- Make `monitor` async friendly (#2912) by @sentrivana

  You can now decorate your async functions with the `monitor`
  decorator and they will correctly report their duration
  and completion status.

- Fixed `Event | None` runtime `TypeError` (#2928) by @szokeasaurusrex


## 1.44.0

### Various fixes & improvements

- ref: Define types at runtime (#2914) by @szokeasaurusrex
- Explicit reexport of types (#2866) (#2913) by @szokeasaurusrex
- feat(profiling): Add thread data to spans (#2843) by @Zylphrex

## 1.43.0

### Various fixes & improvements

- Add optional `keep_alive` (#2842) by @sentrivana

  If you're experiencing frequent network issues between the SDK and Sentry,
  you can try turning on TCP keep-alive:

  ```python
  import sentry_sdk

  sentry_sdk.init(
      # ...your usual settings...
      keep_alive=True,
  )
  ```

- Add support for Celery Redbeat cron tasks (#2643) by @kwigley

  The SDK now supports the Redbeat scheduler in addition to the default
  Celery Beat scheduler for auto instrumenting crons. See
  [the docs](https://docs.sentry.io/platforms/python/integrations/celery/crons/)
  for more information about how to set this up.

- `aws_event` can be an empty list (#2849) by @sentrivana
- Re-export `Event` in `types.py` (#2829) by @szokeasaurusrex
- Small API docs improvement (#2828) by @antonpirker
- Fixed OpenAI tests (#2834) by @antonpirker
- Bump `checkouts/data-schemas` from `ed078ed` to `8232f17` (#2832) by @dependabot


## 1.42.0

### Various fixes & improvements

- **New integration:** [OpenAI integration](https://docs.sentry.io/platforms/python/integrations/openai/) (#2791) by @colin-sentry

  We added an integration for OpenAI to capture errors and also performance data when using the OpenAI Python SDK.

  Useage:

  This integrations is auto-enabling, so if you have the `openai` package in your project it will be enabled. Just initialize Sentry before you create your OpenAI client.

  ```python
  from openai import OpenAI

  import sentry_sdk

  sentry_sdk.init(
      dsn="___PUBLIC_DSN___",
      enable_tracing=True,
      traces_sample_rate=1.0,
  )

  client = OpenAI()
  ```

  For more information, see the documentation for [OpenAI integration](https://docs.sentry.io/platforms/python/integrations/openai/).

- Discard open OpenTelemetry spans after 10 minutes (#2801) by @antonpirker
- Propagate sentry-trace and baggage headers to Huey tasks (#2792) by @cnschn
- Added Event type (#2753) by @szokeasaurusrex
- Improve scrub_dict typing (#2768) by @szokeasaurusrex
- Dependencies: bump types-protobuf from 4.24.0.20240302 to 4.24.0.20240311 (#2797) by @dependabot

## 1.41.0

### Various fixes & improvements

- Add recursive scrubbing to `EventScrubber` (#2755) by @Cheapshot003

  By default, the `EventScrubber` will not search your events for potential
  PII recursively. With this release, you can enable this behavior with:

  ```python
  import sentry_sdk
  from sentry_sdk.scrubber import EventScrubber

  sentry_sdk.init(
      # ...your usual settings...
      event_scrubber=EventScrubber(recursive=True),
  )
  ```

- Expose `socket_options` (#2786) by @sentrivana

  If the SDK is experiencing connection issues (connection resets, server
  closing connection without response, etc.) while sending events to Sentry,
  tweaking the default `urllib3` socket options to the following can help:

  ```python
  import socket
  from urllib3.connection import HTTPConnection
  import sentry_sdk

  sentry_sdk.init(
      # ...your usual settings...
      socket_options=HTTPConnection.default_socket_options + [
          (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
          # note: skip the following line if you're on MacOS since TCP_KEEPIDLE doesn't exist there
          (socket.SOL_TCP, socket.TCP_KEEPIDLE, 45),
          (socket.SOL_TCP, socket.TCP_KEEPINTVL, 10),
          (socket.SOL_TCP, socket.TCP_KEEPCNT, 6),
      ],
  )
  ```

- Allow to configure merge target for releases (#2777) by @sentrivana
- Allow empty character in metric tags values (#2775) by @viglia
- Replace invalid tag values with an empty string instead of _ (#2773) by @markushi
- Add documentation comment to `scrub_list` (#2769) by @szokeasaurusrex
- Fixed regex to parse version in lambda package file (#2767) by @antonpirker
- xfail broken AWS Lambda tests for now (#2794) by @sentrivana
- Removed print statements because it messes with the tests (#2789) by @antonpirker
- Bump `types-protobuf` from 4.24.0.20240129 to 4.24.0.20240302 (#2782) by @dependabot
- Bump `checkouts/data-schemas` from `eb941c2` to `ed078ed` (#2781) by @dependabot

## 1.40.6

### Various fixes & improvements

- Fix compatibility with `greenlet`/`gevent` (#2756) by @sentrivana
- Fix query source relative filepath (#2717) by @gggritso
- Support `clickhouse-driver==0.2.7` (#2752) by @sentrivana
- Bump `checkouts/data-schemas` from `6121fd3` to `eb941c2` (#2747) by @dependabot

## 1.40.5

### Various fixes & improvements

- Deprecate `last_event_id()`. (#2749) by @antonpirker
- Warn if uWSGI is set up without proper thread support (#2738) by @sentrivana

    uWSGI has to be run in threaded mode for the SDK to run properly. If this is
    not the case, the consequences could range from features not working unexpectedly
    to uWSGI workers crashing.

    Please make sure to run uWSGI with both `--enable-threads` and `--py-call-uwsgi-fork-hooks`.

- `parsed_url` can be `None` (#2734) by @sentrivana
- Python 3.7 is not supported anymore by Lambda, so removed it and added 3.12 (#2729) by @antonpirker

## 1.40.4

### Various fixes & improvements

- Only start metrics flusher thread on demand (#2727) by @sentrivana
- Bump checkouts/data-schemas from `aa7058c` to `6121fd3` (#2724) by @dependabot

## 1.40.3

### Various fixes & improvements

- Turn off metrics for uWSGI (#2720) by @sentrivana
- Minor improvements (#2714) by @antonpirker

## 1.40.2

### Various fixes & improvements

- test: Fix `pytest` error (#2712) by @szokeasaurusrex
- build(deps): bump types-protobuf from 4.24.0.4 to 4.24.0.20240129 (#2691) by @dependabot

## 1.40.1

### Various fixes & improvements

- Fix uWSGI workers hanging (#2694) by @sentrivana
- Make metrics work with `gevent` (#2694) by @sentrivana
- Guard against `engine.url` being `None` (#2708) by @sentrivana
- Fix performance regression in `sentry_sdk.utils._generate_installed_modules` (#2703) by @GlenWalker
- Guard against Sentry initialization mid SQLAlchemy cursor (#2702) by @apmorton
- Fix yaml generation script (#2695) by @sentrivana
- Fix AWS Lambda workflow (#2710) by @sentrivana
- Bump `codecov/codecov-action` from 3 to 4 (#2706) by @dependabot
- Bump `actions/cache` from 3 to 4 (#2661) by @dependabot
- Bump `actions/checkout` from 3.1.0 to 4.1.1 (#2561) by @dependabot
- Bump `github/codeql-action` from 2 to 3 (#2603) by @dependabot
- Bump `actions/setup-python` from 4 to 5 (#2577) by @dependabot

## 1.40.0

### Various fixes & improvements

- Enable metrics related settings by default (#2685) by @iambriccardo
- Fix `UnicodeDecodeError` on Python 2 (#2657) by @sentrivana
- Enable DB query source by default (#2629) by @sentrivana
- Fix query source duration check (#2675) by @sentrivana
- Reformat with `black==24.1.0` (#2680) by @sentrivana
- Cleaning up existing code to prepare for new Scopes API (#2611) by @antonpirker
- Moved redis related tests to databases (#2674) by @antonpirker
- Improve `sentry_sdk.trace` type hints (#2633) by @szokeasaurusrex
- Bump `checkouts/data-schemas` from `e9f7d58` to `aa7058c` (#2639) by @dependabot

## 1.39.2

### Various fixes & improvements

- Fix timestamp in transaction created by OTel (#2627) by @antonpirker
- Fix relative path in DB query source  (#2624) by @antonpirker
- Run more CI checks on 2.0 branch (#2625) by @sentrivana
- Fix tracing `TypeError` for static and class methods (#2559) by @szokeasaurusrex
- Fix missing `ctx` in Arq integration (#2600) by @ivanovart
- Change `data_category` from `check_in` to `monitor` (#2598) by @sentrivana

## 1.39.1

### Various fixes & improvements

- Fix psycopg2 detection in the Django integration (#2593) by @sentrivana
- Filter out empty string releases (#2591) by @sentrivana
- Fixed local var not present when there is an error in a user's `error_sampler` function (#2511) by @antonpirker
- Fixed typing in `aiohttp` (#2590) by @antonpirker

## 1.39.0

### Various fixes & improvements

- Add support for cluster clients from Redis SDK (#2394) by @md384
- Improve location reporting for timer metrics (#2552) by @mitsuhiko
- Fix Celery `TypeError` with no-argument `apply_async` (#2575) by @szokeasaurusrex
- Fix Lambda integration with EventBridge source (#2546) by @davidcroda
- Add max tries to Spotlight (#2571) by @hazAT
- Handle `os.path.devnull` access issues (#2579) by @sentrivana
- Change `code.filepath` frame picking logic (#2568) by @sentrivana
- Trigger AWS Lambda tests on label (#2538) by @sentrivana
- Run permissions step on pull_request_target but not push (#2548) by @sentrivana
- Hash AWS Lambda test functions based on current revision (#2557) by @sentrivana
- Update Django version in tests (#2562) by @sentrivana
- Make metrics tests non-flaky (#2572) by @antonpirker

## 1.38.0

### Various fixes & improvements

- Only add trace context to checkins and do not run `event_processors` for checkins (#2536) by @antonpirker
- Metric span summaries (#2522) by @mitsuhiko
- Add source context to code locations (#2539) by @jan-auer
- Use in-app filepath instead of absolute path (#2541) by @antonpirker
- Switch to `jinja2` for generating CI yamls (#2534) by @sentrivana

## 1.37.1

### Various fixes & improvements

- Fix `NameError` on `parse_version` with eventlet (#2532) by @sentrivana
- build(deps): bump checkouts/data-schemas from `68def1e` to `e9f7d58` (#2501) by @dependabot

## 1.37.0

### Various fixes & improvements

- Move installed modules code to utils (#2429) by @sentrivana

    Note: We moved the internal function `_get_installed_modules` from `sentry_sdk.integrations.modules` to `sentry_sdk.utils`.
    So if you use this function you have to update your imports

- Add code locations for metrics (#2526) by @jan-auer
- Add query source to DB spans (#2521) by @antonpirker
- Send events to Spotlight sidecar (#2524) by @HazAT
- Run integration tests with newest `pytest` (#2518) by @sentrivana
- Bring tests up to date (#2512) by @sentrivana
- Fix: Prevent global var from being discarded at shutdown (#2530) by @antonpirker
- Fix: Scope transaction source not being updated in scope.span setter (#2519) by @sl0thentr0py

## 1.36.0

### Various fixes & improvements

- Django: Support Django 5.0 (#2490) by @sentrivana
- Django: Handling ASGI body in the right way. (#2513) by @antonpirker
- Flask: Test with Flask 3.0 (#2506) by @sentrivana
- Celery: Do not create a span when task is triggered by Celery Beat (#2510) by @antonpirker
- Redis: Ensure `RedisIntegration` is disabled, unless `redis` is installed (#2504) by @szokeasaurusrex
- Quart: Fix Quart integration for Quart 0.19.4  (#2516) by @antonpirker
- gRPC: Make async gRPC less noisy (#2507) by @jyggen

## 1.35.0

### Various fixes & improvements

- **Updated gRPC integration:** Asyncio interceptors and easier setup (#2369) by @fdellekart

  Our gRPC integration now instruments incoming unary-unary grpc requests and outgoing unary-unary, unary-stream grpc requests using grpcio channels. Everything works now for sync and async code.

  Before this release you had to add Sentry interceptors by hand to your gRPC code, now the only thing you need to do is adding the `GRPCIntegration` to you `sentry_sdk_init()` call. (See [documentation](https://docs.sentry.io/platforms/python/integrations/grpc/) for more information):

  ```python
  import sentry_sdk
  from sentry_sdk.integrations.grpc import GRPCIntegration

  sentry_sdk.init(
      dsn="___PUBLIC_DSN___",
      enable_tracing=True,
      integrations=[
          GRPCIntegration(),
      ],
  )
  ```
  The old way still works, but we strongly encourage you to update your code to the way described above.

- Python 3.12: Replace deprecated datetime functions (#2502) by @sentrivana
- Metrics: Unify datetime format (#2409) by @mitsuhiko
- Celery: Set correct data in `check_in`s (#2500) by @antonpirker
- Celery: Read timezone for Crons monitors from `celery_schedule` if existing (#2497) by @antonpirker
- Django: Removing redundant code in Django tests (#2491) by @vagi8
- Django: Make reading the request body work in Django ASGI apps. (#2495) by @antonpirker
- FastAPI: Use wraps on fastapi request call wrapper (#2476) by @nkaras
- Fix: Probe for psycopg2 and psycopg3 parameters function. (#2492) by @antonpirker
- Fix: Remove unnecessary TYPE_CHECKING alias (#2467) by @rafrafek

## 1.34.0

### Various fixes & improvements
- Added Python 3.12 support (#2471, #2483)
- Handle missing `connection_kwargs` in `patch_redis_client` (#2482) by @szokeasaurusrex
- Run common test suite on Python 3.12 (#2479) by @sentrivana

## 1.33.1

### Various fixes & improvements

- Make parse_version work in utils.py itself. (#2474) by @antonpirker

## 1.33.0

### Various fixes & improvements

- New: Added `error_sampler` option (#2456) by @szokeasaurusrex
- Python 3.12: Detect interpreter in shutdown state on thread spawn (#2468) by @mitsuhiko
- Patch eventlet under Sentry SDK (#2464) by @szokeasaurusrex
- Mitigate CPU spikes when sending lots of events with lots of data (#2449) by @antonpirker
- Make `debug` option also configurable via environment (#2450) by @antonpirker
- Make sure `get_dsn_parameters` is an actual function (#2441) by @sentrivana
- Bump pytest-localserver, add compat comment (#2448) by @sentrivana
- AWS Lambda: Update compatible runtimes for AWS Lambda layer (#2453) by @antonpirker
- AWS Lambda: Load AWS Lambda secrets in Github CI (#2153) by @antonpirker
- Redis: Connection attributes in `redis` database spans (#2398) by @antonpirker
- Falcon: Falcon integration checks response status before reporting error (#2465) by @szokeasaurusrex
- Quart: Support Quart 0.19 onwards (#2403) by @pgjones
- Sanic: Sanic integration initial version (#2419) by @szokeasaurusrex
- Django: Fix parsing of Django `path` patterns (#2452) by @sentrivana
- Django: Add Django 4.2 to test suite (#2462) by @sentrivana
- Polish changelog (#2434) by @sentrivana
- Update CONTRIBUTING.md (#2443) by @krishvsoni
- Update README.md (#2435) by @sentrivana

## 1.32.0

### Various fixes & improvements

- **New:** Error monitoring for some of the most popular Python GraphQL libraries:
  - Add [GQL GraphQL integration](https://docs.sentry.io/platforms/python/integrations/gql/) (#2368) by @szokeasaurusrex

    Usage:

    ```python
      import sentry_sdk
      from sentry_sdk.integrations.gql import GQLIntegration

      sentry_sdk.init(
          dsn='___PUBLIC_DSN___',
          integrations=[
              GQLIntegration(),
          ],
      )
    ```

  - Add [Graphene GraphQL error integration](https://docs.sentry.io/platforms/python/integrations/graphene/) (#2389) by @sentrivana

    Usage:

    ```python
      import sentry_sdk
      from sentry_sdk.integrations.graphene import GrapheneIntegration

      sentry_sdk.init(
          dsn='___PUBLIC_DSN___',
          integrations=[
              GrapheneIntegration(),
          ],
      )
    ```

  - Add [Strawberry GraphQL error & tracing integration](https://docs.sentry.io/platforms/python/integrations/strawberry/) (#2393) by @sentrivana

    Usage:

    ```python
      import sentry_sdk
      from sentry_sdk.integrations.strawberry import StrawberryIntegration

      sentry_sdk.init(
          dsn='___PUBLIC_DSN___',
          integrations=[
              # make sure to set async_execution to False if you're executing
              # GraphQL queries synchronously
              StrawberryIntegration(async_execution=True),
          ],
          traces_sample_rate=1.0,
      )
    ```

  - Add [Ariadne GraphQL error integration](https://docs.sentry.io/platforms/python/integrations/ariadne/) (#2387) by @sentrivana

    Usage:

    ```python
      import sentry_sdk
      from sentry_sdk.integrations.ariadne import AriadneIntegration

      sentry_sdk.init(
          dsn='___PUBLIC_DSN___',
          integrations=[
              AriadneIntegration(),
          ],
      )
    ```

- Capture multiple named groups again (#2432) by @sentrivana
- Don't fail when upstream scheme is unusual (#2371) by @vanschelven
- Support new RQ version (#2405) by @antonpirker
- Remove `utcnow`, `utcfromtimestamp` deprecated in Python 3.12 (#2415) by @rmad17
- Add `trace` to `__all__` in top-level `__init__.py` (#2401) by @lobsterkatie
- Move minimetrics code to the SDK (#2385) by @mitsuhiko
- Add configurable compression levels (#2382) by @mitsuhiko
- Shift flushing by up to a rollup window (#2396) by @mitsuhiko
- Make a consistent noop flush behavior (#2428) by @mitsuhiko
- Stronger recursion protection (#2426) by @mitsuhiko
- Remove `OpenTelemetryIntegration` from `__init__.py` (#2379) by @sentrivana
- Update API docs (#2397) by @antonpirker
- Pin some test requirements because new majors break our tests (#2404) by @antonpirker
- Run more `requests`, `celery`, `falcon` tests (#2414) by @sentrivana
- Move `importorskip`s in tests to `__init__.py` files (#2412) by @sentrivana
- Fix `mypy` errors (#2433) by @sentrivana
- Fix pre-commit issues (#2424) by @bukzor-sentryio
- Update [CONTRIBUTING.md](https://github.com/getsentry/sentry-python/blob/master/CONTRIBUTING.md) (#2411) by @sentrivana
- Bump `sphinx` from 7.2.5 to 7.2.6 (#2378) by @dependabot
- [Experimental] Add explain plan to DB spans (#2315) by @antonpirker

## 1.31.0

### Various fixes & improvements

- **New:** Add integration for `clickhouse-driver` (#2167) by @mimre25

  For more information, see the documentation for [clickhouse-driver](https://docs.sentry.io/platforms/python/configuration/integrations/clickhouse-driver) for more information.

  Usage:

  ```python
    import sentry_sdk
    from sentry_sdk.integrations.clickhouse_driver import ClickhouseDriverIntegration

    sentry_sdk.init(
        dsn='___PUBLIC_DSN___',
        integrations=[
            ClickhouseDriverIntegration(),
        ],
    )
  ```

- **New:** Add integration for `asyncpg` (#2314) by @mimre25

  For more information, see the documentation for [asyncpg](https://docs.sentry.io/platforms/python/configuration/integrations/asyncpg/) for more information.

  Usage:

  ```python
    import sentry_sdk
    from sentry_sdk.integrations.asyncpg import AsyncPGIntegration

    sentry_sdk.init(
        dsn='___PUBLIC_DSN___',
        integrations=[
            AsyncPGIntegration(),
        ],
    )
  ```

- **New:** Allow to override `propagate_traces` in `Celery` per task (#2331) by @jan-auer

  For more information, see the documentation for [Celery](https://docs.sentry.io//platforms/python/guides/celery/#distributed-traces) for more information.

  Usage:
  ```python
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration

    # Enable global distributed traces (this is the default, just to be explicit.)
    sentry_sdk.init(
        dsn='___PUBLIC_DSN___',
        integrations=[
            CeleryIntegration(propagate_traces=True),
        ],
    )

    ...

    # This will NOT propagate the trace. (The task will start its own trace):
    my_task_b.apply_async(
        args=("some_parameter", ),
        headers={"sentry-propagate-traces": False},
    )
  ```

- Prevent Falcon integration from breaking ASGI apps (#2359) by @szokeasaurusrex
- Backpressure: only downsample a max of 10 times (#2347) by @sl0thentr0py
- Made NoOpSpan compatible to Transactions. (#2364) by @antonpirker
- Cleanup ASGI integration (#2335) by @antonpirker
- Pin anyio in tests (dep of httpx), because new major 4.0.0 breaks tests. (#2336) by @antonpirker
- Added link to backpressure section in docs. (#2354) by @antonpirker
- Add .vscode to .gitignore (#2317) by @shoaib-mohd
- Documenting Spans and Transactions (#2358) by @antonpirker
- Fix in profiler: do not call getcwd from module root (#2329) by @Zylphrex
- Fix deprecated version attribute (#2338) by @vagi8
- Fix transaction name in Starlette and FastAPI (#2341) by @antonpirker
- Fix tests using Postgres (#2362) by @antonpirker
- build(deps): Updated linting tooling (#2350) by @antonpirker
- build(deps): bump sphinx from 7.2.4 to 7.2.5 (#2344) by @dependabot
- build(deps): bump actions/checkout from 2 to 4 (#2352) by @dependabot
- build(deps): bump checkouts/data-schemas from `ebc77d3` to `68def1e` (#2351) by @dependabot

## 1.30.0

### Various fixes & improvements

- Officially support Python 3.11 (#2300) by @sentrivana
- Context manager monitor (#2290) by @szokeasaurusrex
- Set response status code in transaction `response` context. (#2312) by @antonpirker
- Add missing context kwarg to `_sentry_task_factory` (#2267) by @JohnnyDeuss
- In Postgres take the connection params from the connection  (#2308) by @antonpirker
- Experimental: Allow using OTel for performance instrumentation (#2272) by @sentrivana

    This release includes experimental support for replacing Sentry's default
    performance monitoring solution with one powered by OpenTelemetry without having
    to do any manual setup.

    Try it out by installing `pip install sentry-sdk[opentelemetry-experimental]` and
    then initializing the SDK with:

    ```python
    sentry_sdk.init(
        # ...your usual options...
        _experiments={"otel_powered_performance": True},
    )
    ```

    This enables OpenTelemetry performance monitoring support for some of the most
    popular frameworks and libraries (Flask, Django, FastAPI, requests...).

    We're looking forward to your feedback! Please let us know about your experience
    in this discussion: https://github.com/getsentry/sentry/discussions/55023

    **Important note:** Please note that this feature is experimental and in a
    proof-of-concept stage and is not meant for production use. It may be changed or
    removed at any point.

- Enable backpressure handling by default (#2298) by @sl0thentr0py

    The SDK now dynamically downsamples transactions to reduce backpressure in high
    throughput systems. It starts a new `Monitor` thread to perform some health checks
    which decide to downsample (halved each time) in 10 second intervals till the system
    is healthy again.

    To disable this behavior, use:

    ```python
    sentry_sdk.init(
        # ...your usual options...
        enable_backpressure_handling=False,
    )
    ```

    If your system serves heavy load, please let us know how this feature works for you!

    Check out the [documentation](https://docs.sentry.io/platforms/python/configuration/options/#enable-backpressure-handling) for more information.

- Stop recording spans for internal web requests to Sentry (#2297) by @szokeasaurusrex
- Add test for `ThreadPoolExecutor` (#2259) by @gggritso
- Add docstrings for `Scope.update_from_*` (#2311) by @sentrivana
- Moved `is_sentry_url` to utils (#2304) by @szokeasaurusrex
- Fix: arq attribute error on settings, support worker args (#2260) by @rossmacarthur
- Fix: Exceptions include detail property for their value  (#2193) by @nicolassanmar
- build(deps): bump mypy from 1.4.1 to 1.5.1 (#2319) by @dependabot
- build(deps): bump sphinx from 7.1.2 to 7.2.4 (#2322) by @dependabot
- build(deps): bump sphinx from 7.0.1 to 7.1.2 (#2296) by @dependabot
- build(deps): bump checkouts/data-schemas from `1b85152` to `ebc77d3` (#2254) by @dependabot

## 1.29.2

### Various fixes & improvements

- Revert GraphQL integration (#2287) by @sentrivana

## 1.29.1

### Various fixes & improvements

- Fix GraphQL integration swallowing responses (#2286) by @sentrivana
- Fix typo (#2283) by @sentrivana

## 1.29.0

### Various fixes & improvements

- Capture GraphQL client errors (#2243) by @sentrivana
  - The SDK will now create dedicated errors whenever an HTTP client makes a request to a `/graphql` endpoint and the response contains an error. You can opt out of this by providing `capture_graphql_errors=False` to the HTTP client integration.
- Read MAX_VALUE_LENGTH from client options (#2121) (#2171) by @puittenbroek
- Rename `request_bodies` to `max_request_body_size` (#2247) by @mgaligniana
- Always sample checkin regardless of `sample_rate` (#2279) by @szokeasaurusrex
- Add information to short-interval cron error message (#2246) by @lobsterkatie
- Add DB connection attributes in spans (#2274) by @antonpirker
- Add `db.system` to remaining Redis spans (#2271) by @AbhiPrasad
- Clarified the procedure for running tests (#2276) by @szokeasaurusrex
- Fix Chalice tests (#2278) by @sentrivana
- Bump Black from 23.3.0 to 23.7.0 (#2256) by @dependabot
- Remove py3.4 from tox.ini (#2248) by @sentrivana

## 1.28.1

### Various fixes & improvements

- Redis: Add support for redis.asyncio (#1933) by @Zhenay
- Make sure each task that is started by Celery Beat has its own trace. (#2249) by @antonpirker
- Add Sampling Decision to Trace Envelope Header (#2239) by @antonpirker
- Do not add trace headers (`sentry-trace` and `baggage`) to HTTP requests to Sentry (#2240) by @antonpirker
- Prevent adding `sentry-trace` header multiple times (#2235) by @antonpirker
- Skip distributions with incomplete metadata (#2231) by @rominf
- Remove stale.yml (#2245) by @hubertdeng123
- Django: Fix 404 Handler handler being labeled as "generic ASGI request" (#1277) by @BeryJu

## 1.28.0

### Various fixes & improvements

- Add support for cron jobs in ARQ integration (#2088) by @lewazo
- Backpressure handling prototype (#2189) by @sl0thentr0py
- Add "replay" context to event payload (#2234) by @antonpirker
- Update test Django app to be compatible for Django 4.x (#1794) by @DilLip-Chowdary-Codes

## 1.27.1

### Various fixes & improvements

- Add Starlette/FastAPI template tag for adding Sentry tracing information (#2225) by @antonpirker
  - By adding `{{ sentry_trace_meta }}` to your Starlette/FastAPI Jinja2 templates we will include Sentry trace information as a meta tag in the rendered HTML to allow your frontend to pick up and continue the trace started in the backend.
- Fixed generation of baggage when a DSC is already in propagation context (#2232) by @antonpirker
- Handle explicitly passing `None` for `trace_configs` in `aiohttp` (#2230) by @Harmon758
- Support newest Starlette versions (#2227) by @antonpirker

## 1.27.0

### Various fixes & improvements

- Support for SQLAlchemy 2.0 (#2200) by @antonpirker
- Add instrumentation of `aiohttp` client requests (#1761) by @md384
- Add Django template tag for adding Sentry tracing information (#2222) by @antonpirker
  - By adding `{{ sentry_trace_meta }}` to your Django templates we will include Sentry trace information as a meta tag in the rendered HTML to allow your frontend to pick up and continue the trace started in the backend.

- Update Flask HTML meta helper (#2203) by @antonpirker
- Take trace ID always from propagation context (#2209) by @antonpirker
- Fix trace context in event payload (#2205) by @antonpirker
- Use new top level API in `trace_propagation_meta` (#2202) by @antonpirker
- Do not overwrite existing baggage on outgoing requests (#2191, #2214) by @sentrivana
- Set the transaction/span status from an OTel span (#2115) by @daniil-konovalenko
- Fix propagation of OTel `NonRecordingSpan` (#2187) by @hartungstenio
- Fix `TaskLockedException` handling in Huey integration (#2206) by @Zhenay
- Add message format configuration arguments to Loguru integration (#2208) by @Gwill
- Profiling: Add client reports for profiles (#2207) by @Zylphrex
- CI: Fix CI (#2220) by @antonpirker
- Dependencies: Bump `checkouts/data-schemas` from `7fdde87` to `1b85152` (#2218) by @dependabot
- Dependencies: Bump `mypy` from 1.3.0 to 1.4.1 (#2194) by @dependabot
- Docs: Change API doc theme (#2210) by @sentrivana
- Docs: Allow (some) autocompletion for top-level API (#2213) by @sentrivana
- Docs: Revert autocomplete hack (#2224) by @sentrivana

## 1.26.0

### Various fixes & improvements

- Tracing without performance (#2136) by @antonpirker
- Load tracing information from environment (#2176) by @antonpirker
- Auto-enable HTTPX integration if HTTPX installed (#2177) by @sentrivana
- Support for SOCKS proxies (#1050) by @Roguelazer
- Wrap `parse_url` calls in `capture_internal_exceptions` (#2162) by @sentrivana
- Run 2.7 tests in CI again (#2181) by @sentrivana
- Crons: Do not support sub-minute cron intervals (#2172) by @antonpirker
- Profile: Add function name to profiler frame cache (#2164) by @Zylphrex
- Dependencies: bump checkouts/data-schemas from `0ed3357` to `7fdde87` (#2165) by @dependabot
- Update changelog (#2163) by @sentrivana

## 1.25.1

### Django update (ongoing)

Collections of improvements to our Django integration.

By: @mgaligniana (#1773)

### Various fixes & improvements

- Fix `parse_url` (#2161) by @sentrivana and @antonpirker

  Our URL sanitization used in multiple integrations broke with the recent Python security update. If you started seeing `ValueError`s with `"'Filtered' does not appear to be an IPv4 or IPv6 address"`, this release fixes that. See [the original issue](https://github.com/getsentry/sentry-python/issues/2160) for more context.

- Better version parsing in integrations (#2152) by @antonpirker

  We now properly support all integration versions that conform to [PEP 440](https://peps.python.org/pep-0440/). This replaces our naïve version parsing that wouldn't accept versions such as `2.0.0rc1` or `2.0.5.post1`.

- Align HTTP status code as span data field `http.response.status_code` (#2113) by @antonpirker
- Do not encode cached value to determine size (#2143) by @sentrivana
- Fix using `unittest.mock` whenever available (#1926) by @mgorny
- Fix 2.7 `common` tests (#2145) by @sentrivana
- Bump `actions/stale` from `6` to `8` (#1978) by @dependabot
- Bump `black` from `22.12.0` to `23.3.0` (#1984) by @dependabot
- Bump `mypy` from `1.2.0` to `1.3.0` (#2110) by @dependabot
- Bump `sphinx` from `5.3.0` to `7.0.1` (#2112) by @dependabot

## 1.25.0

### Various fixes & improvements

- Support urllib3>=2.0.0 (#2148) by @asottile-sentry

  We're now supporting urllib3's new major version, 2.0.0. If you encounter issues (e.g. some of your dependencies not supporting the new urllib3 version yet) you might consider pinning the urllib3 version to `<2.0.0` manually in your project. Check out the [the urllib3 migration guide](https://urllib3.readthedocs.io/en/latest/v2-migration-guide.html#migrating-as-an-application-developer) for details.

- Auto-retry tests on failure (#2134) by @sentrivana
- Correct `importlib.metadata` check in `test_modules` (#2149) by @asottile-sentry
- Fix distribution name normalization (PEP-0503) (#2144) by @rominf
- Fix `functions_to_trace` typing (#2141) by @rcmarron

## 1.24.0

### Various fixes & improvements

- **New:** Celery Beat exclude tasks option (#2130) by @antonpirker

  You can exclude Celery Beat tasks from being auto-instrumented. To do this, add a list of tasks you want to exclude as option `exclude_beat_tasks` when creating `CeleryIntegration`. The list can contain simple strings with the full task name, as specified in the Celery Beat schedule, or regular expressions to match multiple tasks.

  For more information, see the documentation for [Crons](https://docs.sentry.io/platforms/python/guides/celery/crons/) for more information.

  Usage:

  ```python
      exclude_beat_tasks = [
          "some-task-a",
          "payment-check-.*",
      ]
      sentry_sdk.init(
          dsn='___PUBLIC_DSN___',
          integrations=[
              CeleryIntegration(
                  monitor_beat_tasks=True,
                  exclude_beat_tasks=exclude_beat_tasks,
              ),
          ],
      )
  ```

  In this example the task `some-task-a` and all tasks with a name starting with `payment-check-` will be ignored.

- **New:** Add support for **ExceptionGroups** (#2025) by @antonpirker

  _Note:_ If running Self-Hosted Sentry, you should wait to adopt this SDK update until after updating to the 23.6.0 (est. June 2023) release of Sentry. Updating early will not break anything, but you will not get the full benefit of the Exception Groups improvements to issue grouping that were added to the Sentry backend.

- Prefer `importlib.metadata` over `pkg_resources` if available (#2081) by @sentrivana
- Work with a copy of request, vars in the event (#2125) by @sentrivana
- Pinned version of dependency that broke the build (#2133) by @antonpirker

## 1.23.1

### Various fixes & improvements

- Disable Django Cache spans by default. (#2120) by @antonpirker

## 1.23.0

### Various fixes & improvements

- **New:** Add `loguru` integration (#1994) by @PerchunPak

  Check [the documentation](https://docs.sentry.io/platforms/python/configuration/integrations/loguru/) for more information.

  Usage:

  ```python
  from loguru import logger
  import sentry_sdk
  from sentry_sdk.integrations.loguru import LoguruIntegration

  sentry_sdk.init(
      dsn="___PUBLIC_DSN___",
      integrations=[
          LoguruIntegration(),
      ],
  )

  logger.debug("I am ignored")
  logger.info("I am a breadcrumb")
  logger.error("I am an event", extra=dict(bar=43))
  logger.exception("An exception happened")
  ```

  - An error event with the message `"I am an event"` will be created.
  - `"I am a breadcrumb"` will be attached as a breadcrumb to that event.
  - `bar` will end up in the `extra` attributes of that event.
  - `"An exception happened"` will send the current exception from `sys.exc_info()` with the stack trace to Sentry. If there's no exception, the current stack will be attached.
  - The debug message `"I am ignored"` will not be captured by Sentry. To capture it, set `level` to `DEBUG` or lower in `LoguruIntegration`.

- Do not truncate request body if `request_bodies` is `"always"` (#2092) by @sentrivana
- Fixed Celery headers for Beat auto-instrumentation (#2102) by @antonpirker
- Add `db.operation` to Redis and MongoDB spans (#2089) by @antonpirker
- Make sure we're importing `redis` the library (#2106) by @sentrivana
- Add `include_source_context` option (#2020) by @farhat-nawaz and @sentrivana
- Import `Markup` from `markupsafe` (#2047) by @rco-ableton
- Fix `__qualname__` missing attribute in asyncio integration (#2105) by @sl0thentr0py
- Remove relay extension from AWS Layer (#2068) by @sl0thentr0py
- Add a note about `pip freeze` to the bug template (#2103) by @sentrivana

## 1.22.2

### Various fixes & improvements

- Fix: Django caching spans when using keyword arguments (#2086) by @antonpirker
- Fix: Duration in Celery Beat tasks monitoring (#2087) by @antonpirker
- Fix: Docstrings of SPANDATA (#2084) by @antonpirker

## 1.22.1

### Various fixes & improvements

- Fix: Handle a list of keys (not just a single key) in Django cache spans (#2082) by @antonpirker

## 1.22.0

### Various fixes & improvements

- Add `cache.hit` and `cache.item_size` to Django (#2057) by @antonpirker

  _Note:_ This will add spans for all requests to the caches configured in Django. This will probably add some overhead to your server an also add multiple spans to your performance waterfall diagrams. If you do not want this, you can disable this feature in the DjangoIntegration:

  ```python
  sentry_sdk.init(
      dsn="...",
      integrations=[
          DjangoIntegration(cache_spans=False),
      ]
  )
  ```

- Use `http.method` instead of `method` (#2054) by @AbhiPrasad
- Handle non-int `exc.status_code` in Starlette (#2075) by @sentrivana
- Handle SQLAlchemy `engine.name` being bytes (#2074) by @sentrivana
- Fix `KeyError` in `capture_checkin` if SDK is not initialized (#2073) by @antonpirker
- Use `functools.wrap` for `ThreadingIntegration` patches to fix attributes (#2080) by @EpicWink
- Pin `urllib3` to <2.0.0 for now (#2069) by @sl0thentr0py

## 1.21.1

### Various fixes & improvements

- Do not send monitor_config when unset (#2058) by @evanpurkhiser
- Add `db.system` span data (#2040, #2042) by @antonpirker
- Fix memory leak in profiling (#2049) by @Zylphrex
- Fix crash loop when returning none in before_send (#2045) by @sentrivana

## 1.21.0

### Various fixes & improvements

- Better handling of redis span/breadcrumb data (#2033) by @antonpirker

  _Note:_ With this release we will limit the description of redis db spans and the data in breadcrumbs represting redis db operations to 1024 characters.

  This can can lead to truncated data. If you do not want this there is a new parameter `max_data_size` in `RedisIntegration`. You can set this to `None` for disabling trimming.

  Example for **disabling** trimming of redis commands in spans or breadcrumbs:

  ```python
  sentry_sdk.init(
    integrations=[
      RedisIntegration(max_data_size=None),
    ]
  )
  ```

  Example for custom trim size of redis commands in spans or breadcrumbs:

  ```python
  sentry_sdk.init(
    integrations=[
      RedisIntegration(max_data_size=50),
    ]
  )`

  ```

- Add `db.system` to redis and SQLAlchemy db spans (#2037, #2038, #2039) (#2037) by @AbhiPrasad
- Upgraded linting tooling (#2026) by @antonpirker
- Made code more resilient. (#2031) by @antonpirker

## 1.20.0

### Various fixes & improvements

- Send all events to /envelope endpoint when tracing is enabled (#2009) by @antonpirker

  _Note:_ If you’re self-hosting Sentry 9, you need to stay in the previous version of the SDK or update your self-hosted to at least 20.6.0

- Profiling: Remove profile context from SDK (#2013) by @Zylphrex
- Profiling: Additionl performance improvements to the profiler (#1991) by @Zylphrex
- Fix: Celery Beat monitoring without restarting the Beat process (#2001) by @antonpirker
- Fix: Using the Codecov uploader instead of deprecated python package (#2011) by @antonpirker
- Fix: Support for Quart (#2003)` (#2003) by @antonpirker

## 1.19.1

### Various fixes & improvements

- Make auto monitoring beat update support Celery 4 and 5 (#1989) by @antonpirker

## 1.19.0

### Various fixes & improvements

- **New:** [Celery Beat](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html) auto monitoring (#1967) by @antonpirker

  The CeleryIntegration can now also monitor your Celery Beat scheduled tasks automatically using the new [Crons](https://blog.sentry.io/2023/01/04/cron-job-monitoring-beta-because-scheduled-jobs-fail-too/) feature of Sentry.

  To learn more see our [Celery Beat Auto Discovery](https://docs.sentry.io/platforms/python/guides/celery/crons/) documentation.

  Usage:

  ```python
  from celery import Celery, signals
  from celery.schedules import crontab

  import sentry_sdk
  from sentry_sdk.integrations.celery import CeleryIntegration


  app = Celery('tasks', broker='...')
  app.conf.beat_schedule = {
      'set-in-beat-schedule': {
          'task': 'tasks.some_important_task',
          'schedule': crontab(...),
      },
  }


  @signals.celeryd_init.connect
  def init_sentry(**kwargs):
      sentry_sdk.init(
          dsn='...',
          integrations=[CeleryIntegration(monitor_beat_tasks=True)],  # 👈 here
          environment="local.dev.grace",
          release="v1.0",
      )
  ```

  This will auto detect all schedules tasks in your `beat_schedule` and will monitor them with Sentry [Crons](https://blog.sentry.io/2023/01/04/cron-job-monitoring-beta-because-scheduled-jobs-fail-too/).

- **New:** [gRPC](https://grpc.io/) integration (#1911) by @hossein-raeisi

  The [gRPC](https://grpc.io/) integration instruments all incoming requests and outgoing unary-unary, unary-stream grpc requests using grpcio channels.

  To learn more see our [gRPC Integration](https://docs.sentry.io/platforms/python/configuration/integrations/grpc/) documentation.

  On the server:

  ```python
  import grpc
  from sentry_sdk.integrations.grpc.server import ServerInterceptor


  server = grpc.server(
      thread_pool=...,
      interceptors=[ServerInterceptor()],
  )
  ```

  On the client:

  ```python
  import grpc
  from sentry_sdk.integrations.grpc.client import ClientInterceptor


  with grpc.insecure_channel("example.com:12345") as channel:
      channel = grpc.intercept_channel(channel, *[ClientInterceptor()])

  ```

- **New:** socket integration (#1911) by @hossein-raeisi

  Use this integration to create spans for DNS resolves (`socket.getaddrinfo()`) and connection creations (`socket.create_connection()`).

  To learn more see our [Socket Integration](https://docs.sentry.io/platforms/python/configuration/integrations/socket/) documentation.

  Usage:

  ```python
  import sentry_sdk
  from sentry_sdk.integrations.socket import SocketIntegration
  sentry_sdk.init(
      dsn="___PUBLIC_DSN___",
      integrations=[
          SocketIntegration(),
      ],
  )
  ```

- Fix: Do not trim span descriptions. (#1983) by @antonpirker

## 1.18.0

### Various fixes & improvements

- **New:** Implement `EventScrubber` (#1943) by @sl0thentr0py

  To learn more see our [Scrubbing Sensitive Data](https://docs.sentry.io/platforms/python/data-management/sensitive-data/#event-scrubber) documentation.

  Add a new `EventScrubber` class that scrubs certain potentially sensitive interfaces with a `DEFAULT_DENYLIST`. The default scrubber is automatically run if `send_default_pii = False`:

  ```python
  import sentry_sdk
  from sentry_sdk.scrubber import EventScrubber
  sentry_sdk.init(
      # ...
      send_default_pii=False,
      event_scrubber=EventScrubber(),  # this is set by default
  )
  ```

  You can also pass in a custom `denylist` to the `EventScrubber` class and filter additional fields that you want.

  ```python
  from sentry_sdk.scrubber import EventScrubber, DEFAULT_DENYLIST
  # custom denylist
  denylist = DEFAULT_DENYLIST + ["my_sensitive_var"]
  sentry_sdk.init(
      # ...
      send_default_pii=False,
      event_scrubber=EventScrubber(denylist=denylist),
  )
  ```

- **New:** Added new `functions_to_trace` option for central way of performance instrumentation (#1960) by @antonpirker

  To learn more see our [Tracing Options](https://docs.sentry.io/platforms/python/configuration/options/#functions-to-trace) documentation.

  An optional list of functions that should be set up for performance monitoring. For each function in the list, a span will be created when the function is executed.

  ```python
  functions_to_trace = [
      {"qualified_name": "tests.test_basics._hello_world_counter"},
      {"qualified_name": "time.sleep"},
      {"qualified_name": "collections.Counter.most_common"},
  ]

  sentry_sdk.init(
      # ...
      traces_sample_rate=1.0,
      functions_to_trace=functions_to_trace,
  )
  ```

- Updated denylist to include other widely used cookies/headers (#1972) by @antonpirker
- Forward all `sentry-` baggage items (#1970) by @cleptric
- Update OSS licensing (#1973) by @antonpirker
- Profiling: Handle non frame types in profiler (#1965) by @Zylphrex
- Tests: Bad arq dependency in tests (#1966) by @Zylphrex
- Better naming (#1962) by @antonpirker

## 1.17.0

### Various fixes & improvements

- **New:** Monitor Celery Beat tasks with Sentry [Cron Monitoring](https://docs.sentry.io/product/crons/).

  With this feature you can make sure that your Celery beat tasks run at the right time and see if they where successful or not.

  > **Warning**
  > Cron Monitoring is currently in beta. Beta features are still in-progress and may have bugs. We recognize the irony.
  > If you have any questions or feedback, please email us at crons-feedback@sentry.io, reach out via Discord (#cronjobs), or open an issue.

  Usage:

  ```python
  # File: tasks.py

  from celery import Celery, signals
  from celery.schedules import crontab

  import sentry_sdk
  from sentry_sdk.crons import monitor
  from sentry_sdk.integrations.celery import CeleryIntegration


  # 1. Setup your Celery beat configuration

  app = Celery('mytasks', broker='redis://localhost:6379/0')
  app.conf.beat_schedule = {
      'set-in-beat-schedule': {
          'task': 'tasks.tell_the_world',
          'schedule': crontab(hour='10', minute='15'),
          'args': ("in beat_schedule set", ),
      },
  }


  # 2. Initialize Sentry either in `celeryd_init` or `beat_init` signal.

  #@signals.celeryd_init.connect
  @signals.beat_init.connect
  def init_sentry(**kwargs):
      sentry_sdk.init(
          dsn='...',
          integrations=[CeleryIntegration()],
          environment="local.dev.grace",
          release="v1.0.7-a1",
      )


  # 3. Link your Celery task to a Sentry Cron Monitor

  @app.task
  @monitor(monitor_slug='3b861d62-ff82-4aa0-9cd6-b2b6403bd0cf')
  def tell_the_world(msg):
      print(msg)
  ```

- **New:** Add decorator for Sentry tracing (#1089) by @ynouri

  This allows you to use a decorator to setup custom performance instrumentation.

  To learn more see [Custom Instrumentation](https://docs.sentry.io/platforms/python/performance/instrumentation/custom-instrumentation/).

  Usage: Just add the new decorator to your function, and a span will be created for it:

  ```python
  import sentry_sdk

  @sentry_sdk.trace
  def my_complex_function():
    # do stuff
    ...
  ```

- Make Django signals tracing optional (#1929) by @antonpirker

  See the [Django Guide](https://docs.sentry.io/platforms/python/guides/django) to learn more.

- Deprecated `with_locals` in favor of `include_local_variables` (#1924) by @antonpirker
- Added top level API to get current span (#1954) by @antonpirker
- Profiling: Add profiler options to init (#1947) by @Zylphrex
- Profiling: Set active thread id for quart (#1830) by @Zylphrex
- Fix: Update `get_json` function call for werkzeug 2.1.0+ (#1939) by @michielderoos
- Fix: Returning the tasks result. (#1931) by @antonpirker
- Fix: Rename MYPY to TYPE_CHECKING (#1934) by @untitaker
- Fix: Fix type annotation for ignore_errors in sentry_sdk.init() (#1928) by @tiangolo
- Tests: Start a real http server instead of mocking libs (#1938) by @antonpirker

## 1.16.0

### Various fixes & improvements

- **New:** Add [arq](https://arq-docs.helpmanual.io/) Integration (#1872) by @Zhenay

  This integration will create performance spans when arq jobs will be enqueued and when they will be run.
  It will also capture errors in jobs and will link them to the performance spans.

  Usage:

  ```python
  import asyncio

  from httpx import AsyncClient
  from arq import create_pool
  from arq.connections import RedisSettings

  import sentry_sdk
  from sentry_sdk.integrations.arq import ArqIntegration
  from sentry_sdk.tracing import TransactionSource

  sentry_sdk.init(
      dsn="...",
      integrations=[ArqIntegration()],
  )

  async def download_content(ctx, url):
      session: AsyncClient = ctx['session']
      response = await session.get(url)
      print(f'{url}: {response.text:.80}...')
      return len(response.text)

  async def startup(ctx):
      ctx['session'] = AsyncClient()

  async def shutdown(ctx):
      await ctx['session'].aclose()

  async def main():
      with sentry_sdk.start_transaction(name="testing_arq_tasks", source=TransactionSource.COMPONENT):
          redis = await create_pool(RedisSettings())
          for url in ('https://facebook.com', 'https://microsoft.com', 'https://github.com', "asdf"
                      ):
              await redis.enqueue_job('download_content', url)

  class WorkerSettings:
      functions = [download_content]
      on_startup = startup
      on_shutdown = shutdown

  if __name__ == '__main__':
      asyncio.run(main())
  ```

- Update of [Falcon](https://falconframework.org/) Integration (#1733) by @bartolootrit
- Adding [Cloud Resource Context](https://docs.sentry.io/platforms/python/configuration/integrations/cloudresourcecontext/) integration (#1882) by @antonpirker
- Profiling: Use the transaction timestamps to anchor the profile (#1898) by @Zylphrex
- Profiling: Add debug logs to profiling (#1883) by @Zylphrex
- Profiling: Start profiler thread lazily (#1903) by @Zylphrex
- Fixed checks for structured http data (#1905) by @antonpirker
- Make `set_measurement` public api and remove experimental status (#1909) by @sl0thentr0py
- Add `trace_propagation_targets` option (#1916) by @antonpirker
- Add `enable_tracing` to default traces_sample_rate to 1.0 (#1900) by @sl0thentr0py
- Remove deprecated `tracestate` (#1907) by @sl0thentr0py
- Sanitize URLs in Span description and breadcrumbs (#1876) by @antonpirker
- Mechanism should default to true unless set explicitly (#1889) by @sl0thentr0py
- Better setting of in-app in stack frames (#1894) by @antonpirker
- Add workflow to test gevent (#1870) by @Zylphrex
- Updated outdated HTTPX test matrix (#1917) by @antonpirker
- Switch to MIT license (#1908) by @cleptric

## 1.15.0

### Various fixes & improvements

- New: Add [Huey](https://huey.readthedocs.io/en/latest/) Integration (#1555) by @Zhenay

  This integration will create performance spans when Huey tasks will be enqueued and when they will be executed.

  Usage:

  Task definition in `demo.py`:

  ```python
  import time

  from huey import SqliteHuey, crontab

  import sentry_sdk
  from sentry_sdk.integrations.huey import HueyIntegration

  sentry_sdk.init(
      dsn="...",
      integrations=[
          HueyIntegration(),
      ],
      traces_sample_rate=1.0,
  )

  huey = SqliteHuey(filename='/tmp/demo.db')

  @huey.task()
  def add_numbers(a, b):
      return a + b
  ```

  Running the tasks in `run.py`:

  ```python
  from demo import add_numbers, flaky_task, nightly_backup

  import sentry_sdk
  from sentry_sdk.integrations.huey import HueyIntegration
  from sentry_sdk.tracing import TransactionSource, Transaction


  def main():
      sentry_sdk.init(
          dsn="...",
          integrations=[
              HueyIntegration(),
          ],
          traces_sample_rate=1.0,
      )

      with sentry_sdk.start_transaction(name="testing_huey_tasks", source=TransactionSource.COMPONENT):
          r = add_numbers(1, 2)

  if __name__ == "__main__":
      main()
  ```

- Profiling: Do not send single sample profiles (#1879) by @Zylphrex
- Profiling: Add additional test coverage for profiler (#1877) by @Zylphrex
- Profiling: Always use builtin time.sleep (#1869) by @Zylphrex
- Profiling: Defaul in_app decision to None (#1855) by @Zylphrex
- Profiling: Remove use of threading.Event (#1864) by @Zylphrex
- Profiling: Enable profiling on all transactions (#1797) by @Zylphrex
- FastAPI: Fix check for Starlette in FastAPI integration (#1868) by @antonpirker
- Flask: Do not overwrite default for username with email address in FlaskIntegration (#1873) by @homeworkprod
- Tests: Add py3.11 to test-common (#1871) by @Zylphrex
- Fix: Don't log whole event in before_send / event_processor drops (#1863) by @sl0thentr0py

## 1.14.0

### Various fixes & improvements

- Add `before_send_transaction` (#1840) by @antonpirker

  Adds a hook (similar to `before_send`) that is called for all transaction events (performance releated data).

  Usage:

  ```python
    import sentry_sdk

    def strip_sensitive_data(event, hint):
        # modify event here (or return `None` if you want to drop the event entirely)
        return event

    sentry_sdk.init(
        # ...
        before_send_transaction=strip_sensitive_data,
    )
  ```

  See also: https://docs.sentry.io/platforms/python/configuration/filtering/#using-platformidentifier-namebefore-send-transaction-

- Django: Always remove values of Django session related cookies. (#1842) by @antonpirker
- Profiling: Enable profiling for ASGI frameworks (#1824) by @Zylphrex
- Profiling: Better gevent support (#1822) by @Zylphrex
- Profiling: Add profile context to transaction (#1860) by @Zylphrex
- Profiling: Use co_qualname in python 3.11 (#1831) by @Zylphrex
- OpenTelemetry: fix Use dict for sentry-trace context instead of tuple (#1847) by @AbhiPrasad
- OpenTelemetry: fix extra dependency (#1825) by @bernardotorres
- OpenTelemetry: fix NoOpSpan updates scope (#1834) by @Zylphrex
- OpenTelemetry: Make sure to noop when there is no DSN (#1852) by @antonpirker
- FastAPI: Fix middleware being patched multiple times (#1841) by @JohnnyDeuss
- Starlette: Avoid import of pkg_resource with Starlette integration (#1836) by @mgu
- Removed code coverage target (#1862) by @antonpirker

## 1.13.0

### Various fixes & improvements

- Add Starlite integration (#1748) by @gazorby

  Adding support for the [Starlite](https://starlite-api.github.io/starlite/1.48/) framework. Unhandled errors are captured. Performance spans for Starlite middleware are also captured. Thanks @gazorby for the great work!

  Usage:

  ```python
  from starlite import Starlite, get

  import sentry_sdk
  from sentry_sdk.integrations.starlite import StarliteIntegration

  sentry_sdk.init(
      dsn="...",
      traces_sample_rate=1.0,
      integrations=[
          StarliteIntegration(),
      ],
  )

  @get("/")
  def hello_world() -> dict[str, str]:
      """Keeping the tradition alive with hello world."""
      bla = 1/0  # causing an error
      return {"hello": "world"}

  app = Starlite(route_handlers=[hello_world])
  ```

- Profiling: Remove sample buffer from profiler (#1791) by @Zylphrex
- Profiling: Performance tweaks to profile sampler (#1789) by @Zylphrex
- Add span for Django SimpleTemplateResponse rendering (#1818) by @chdsbd
- Use @wraps for Django Signal receivers (#1815) by @meanmail
- Add enqueued_at and started_at to rq job extra (#1024) by @kruvasyan
- Remove sanic v22 pin (#1819) by @sl0thentr0py
- Add support for `byterray` and `memoryview` built-in types (#1833) by @Tarty
- Handle `"rc"` in SQLAlchemy version. (#1812) by @peterschutt
- Doc: Use .venv (not .env) as a virtual env location in CONTRIBUTING.md (#1790) by @tonyo
- Auto publish to internal pypi on release (#1823) by @asottile-sentry
- Added Python 3.11 to test suite (#1795) by @antonpirker
- Update test/linting dependencies (#1801) by @antonpirker
- Deps: bump sphinx from 5.2.3 to 5.3.0 (#1686) by @dependabot

## 1.12.1

### Various fixes & improvements

- Link errors to OTel spans (#1787) by @antonpirker

## 1.12.0

### Basic OTel support

This adds support to automatically integrate OpenTelemetry performance tracing with Sentry.

See the documentation on how to set it up:
https://docs.sentry.io/platforms/python/performance/instrumentation/opentelemetry/

Give it a try and let us know if you have any feedback or problems with using it.

By: @antonpirker (#1772, #1766, #1765)

### Various fixes & improvements

- Tox Cleanup (#1749) by @antonpirker
- CI: Fix Github action checks (#1780) by @Zylphrex
- Profiling: Introduce active thread id on scope (#1764) by @Zylphrex
- Profiling: Eagerly hash stack for profiles (#1755) by @Zylphrex
- Profiling: Resolve inherited method class names (#1756) by @Zylphrex

## 1.11.1

### Various fixes & improvements

- Move set_transaction_name out of event processor in fastapi/starlette (#1751) by @sl0thentr0py
- Expose proxy_headers as top level config and use in ProxyManager: https://docs.sentry.io/platforms/python/configuration/options/#proxy-headers (#1746) by @sl0thentr0py

## 1.11.0

### Various fixes & improvements

- Fix signals problem on sentry.io (#1732) by @antonpirker
- Fix reading FastAPI request body twice. (#1724) by @antonpirker
- ref(profiling): Do not error if already setup (#1731) by @Zylphrex
- ref(profiling): Use sleep scheduler by default (#1729) by @Zylphrex
- feat(profiling): Extract more frame info (#1702) by @Zylphrex
- Update actions/upload-artifact to v3.1.1 (#1718) by @mattgauntseo-sentry
- Performance optimizations (#1725) by @antonpirker
- feat(pymongo): add PyMongo integration (#1590) by @Agalin
- Move relay to port 5333 to avoid collisions (#1716) by @sl0thentr0py
- fix(utils): strip_string() checks text length counting bytes not chars (#1711) by @mgaligniana
- chore: remove jira workflow (#1707) by @vladanpaunovic
- build(deps): bump checkouts/data-schemas from `a214fbc` to `20ff3b9` (#1703) by @dependabot
- perf(profiling): Tune the sample profile generation code for performance (#1694) by @Zylphrex

## 1.10.1

### Various fixes & improvements

- Bug fixes for FastAPI and Sentry SDK 1.10.0 (#1699) by @antonpirker
- The wrapped receive() did not return anything. (#1698) by @antonpirker

## 1.10.0

### Various fixes & improvements

- Unified naming for span ops (#1661) by @antonpirker

  We have unified the strings of our span operations. See https://develop.sentry.dev/sdk/performance/span-operations/

  **WARNING**: If you have Sentry Dashboards or Sentry Discover queries that use `transaction.op` in their fields, conditions, aggregates or columns this change could potentially break your Dashboards/Discover setup.
  Here is a list of the changes we made to the `op`s. Please adjust your dashboards and Discover queries accordingly:

  | Old operation (`op`)     | New Operation (`op`)   |
  | ------------------------ | ---------------------- |
  | `asgi.server`            | `http.server`          |
  | `aws.request`            | `http.client`          |
  | `aws.request.stream`     | `http.client.stream`   |
  | `celery.submit`          | `queue.submit.celery`  |
  | `celery.task`            | `queue.task.celery`    |
  | `django.middleware`      | `middleware.django`    |
  | `django.signals`         | `event.django`         |
  | `django.template.render` | `template.render`      |
  | `django.view`            | `view.render`          |
  | `http`                   | `http.client`          |
  | `redis`                  | `db.redis`             |
  | `rq.task`                | `queue.task.rq`        |
  | `serverless.function`    | `function.aws`         |
  | `serverless.function`    | `function.gcp`         |
  | `starlette.middleware`   | `middleware.starlette` |

- Include framework in SDK name (#1662) by @antonpirker
- Asyncio integration (#1671) by @antonpirker
- Add exception handling to Asyncio Integration (#1695) by @antonpirker
- Fix asyncio task factory (#1689) by @antonpirker
- Have instrumentation for ASGI middleware receive/send callbacks. (#1673) by @antonpirker
- Use Django internal ASGI handling from Channels version 4.0.0. (#1688) by @antonpirker
- fix(integrations): Fix http putrequest when url is None (#1693) by @MattFlower
- build(deps): bump checkouts/data-schemas from `f0a57f2` to `a214fbc` (#1627) by @dependabot
- build(deps): bump flake8-bugbear from 22.9.11 to 22.9.23 (#1637) by @dependabot
- build(deps): bump sphinx from 5.1.1 to 5.2.3 (#1653) by @dependabot
- build(deps): bump actions/stale from 5 to 6 (#1638) by @dependabot
- build(deps): bump black from 22.8.0 to 22.10.0 (#1670) by @dependabot
- Remove unused node setup from ci. (#1681) by @antonpirker
- Check for Decimal is in_valid_sample_rate (#1672) by @Arvind2222
- Add session for aiohttp integration (#1605) by @denys-pidlisnyi
- feat(profiling): Extract qualified name for each frame (#1669) by @Zylphrex
- feat(profiling): Attach thread metadata to profiles (#1660) by @Zylphrex
- ref(profiling): Rename profiling frame keys (#1680) by @Zylphrex
- fix(profiling): get_frame_name only look at arguments (#1684) by @Zylphrex
- fix(profiling): Need to sample profile correctly (#1679) by @Zylphrex
- fix(profiling): Race condition spawning multiple profiling threads (#1676) by @Zylphrex
- tests(profiling): Add basic profiling tests (#1677) by @Zylphrex
- tests(profiling): Add tests for thread schedulers (#1683) by @Zylphrex

## 1.9.10

### Various fixes & improvements

- Use content-length header in ASGI instead of reading request body (#1646, #1631, #1595, #1573) (#1649) by @antonpirker
- Added newer Celery versions to test suite (#1655) by @antonpirker
- Django 4.x support (#1632) by @antonpirker
- Cancel old CI runs when new one is started. (#1651) by @antonpirker
- Increase max string size for desc (#1647) by @k-fish
- Pin Sanic version for CI (#1650) by @antonpirker
- Fix for partial signals in old Django and old Python versions. (#1641) by @antonpirker
- Convert profile output to the sample format (#1611) by @phacops
- Dynamically adjust profiler sleep time (#1634) by @Zylphrex

## 1.9.9

### Django update (ongoing)

- Instrument Django Signals so they show up in "Performance" view (#1526) by @BeryJu
- include other Django enhancements brought up by the community

### Various fixes & improvements

- fix(profiling): Profiler mode type hints (#1633) by @Zylphrex
- New ASGIMiddleware tests (#1600) by @antonpirker
- build(deps): bump mypy from 0.961 to 0.971 (#1517) by @dependabot
- build(deps): bump black from 22.3.0 to 22.8.0 (#1596) by @dependabot
- build(deps): bump sphinx from 5.0.2 to 5.1.1 (#1524) by @dependabot
- ref: upgrade linters to flake8 5.x (#1610) by @asottile-sentry
- feat(profiling): Introduce different profiler schedulers (#1616) by @Zylphrex
- fix(profiling): Check transaction sampled status before profiling (#1624) by @Zylphrex
- Wrap Baggage ser/deser in capture_internal_exceptions (#1630) by @sl0thentr0py
- Faster Tests (DjangoCon) (#1602) by @antonpirker
- feat(profiling): Add support for profiles_sample_rate (#1613) by @Zylphrex
- feat(profiling): Support for multithreaded profiles (#1570) by @Zylphrex

## 1.9.8

### Various fixes & improvements

- Baggage creation for head of trace (#1589) by @sl0thentr0py
  - The SDK now also generates new baggage entries for dynamic sampling when it is the first (head) SDK in the pipeline.

## 1.9.7

### Various fixes & improvements

- Let SentryAsgiMiddleware work with Starlette and FastAPI integrations (#1594) by @antonpirker

**Note:** The last version 1.9.6 introduced a breaking change where projects that used Starlette or FastAPI
and had manually setup `SentryAsgiMiddleware` could not start. This versions fixes this behaviour.
With this version if you have a manual `SentryAsgiMiddleware` setup and are using Starlette or FastAPI
everything just works out of the box.

Sorry for any inconveniences the last version might have brought to you.

We can do better and in the future we will do our best to not break your code again.

## 1.9.6

### Various fixes & improvements

- Auto-enable Starlette and FastAPI (#1533) by @antonpirker
- Add more version constraints (#1574) by @isra17
- Fix typo in starlette attribute check (#1566) by @sl0thentr0py

## 1.9.5

### Various fixes & improvements

- fix(redis): import redis pipeline using full path (#1565) by @olksdr
- Fix side effects for parallel tests (#1554) by @sl0thentr0py

## 1.9.4

### Various fixes & improvements

- Remove TRANSACTION_SOURCE_UNKNOWN and default to CUSTOM (#1558) by @sl0thentr0py
- feat(redis): Add instrumentation for redis pipeline (#1543) by @jjbayer
- Handle no release when uploading profiles (#1548) by @szokeasaurusrex

## 1.9.3

### Various fixes & improvements

- Wrap StarletteRequestExtractor in capture_internal_exceptions (#1551) by @sl0thentr0py

## 1.9.2

### Various fixes & improvements

- chore: remove quotes (#1545) by @vladanpaunovic

## 1.9.1

### Various fixes & improvements

- Fix FastAPI issues (#1532) ( #1514) (#1532) by @antonpirker
- Add deprecation warning for 3.4, 3.5 (#1541) by @sl0thentr0py
- Fast tests (#1504) by @antonpirker
- Replace Travis CI badge with GitHub Actions badge (#1538) by @153957
- chore(deps): update urllib3 minimum version with environment markers (#1312) by @miketheman
- Update Flask and Quart integrations (#1520) by @pgjones
- chore: Remove ancient examples from tracing prototype (#1528) by @sl0thentr0py
- fix(django): Send correct "url" transaction source if Django resolver fails to resolve (#1525) by @sl0thentr0py

## 1.9.0

### Various fixes & improvements

- feat(profiler): Add experimental profiler under experiments.enable_profiling (#1481) by @szokeasaurusrex
- Fixed problem with broken response and python-multipart (#1516) by @antonpirker

## 1.8.0

### Various fixes & improvements

- feat(starlette): add Starlette integration (#1441) by @sl0thentr0py
  **Important:** Remove manual usage of `SentryAsgiMiddleware`! This is now done by the Starlette integration.
  Usage:

  ```python
  from starlette.applications import Starlette

  from sentry_sdk.integrations.starlette import StarletteIntegration

  sentry_sdk.init(
      dsn="...",
      integrations=[StarletteIntegration()],
  )

  app = Starlette(debug=True, routes=[...])
  ```

- feat(fastapi): add FastAPI integration (#829) by @antonpirker

  **Important:** Remove manual usage of `SentryAsgiMiddleware`! This is now done by the FastAPI integration.

  Usage:

  ```python
  from fastapi import FastAPI

  from sentry_sdk.integrations.starlette import StarletteIntegration
  from sentry_sdk.integrations.fastapi import FastApiIntegration

  sentry_sdk.init(
      dsn="...",
      integrations=[StarletteIntegration(), FastApiIntegration()],
  )

  app = FastAPI()
  ```

  Yes, you have to add both, the `StarletteIntegration` **AND** the `FastApiIntegration`!

- fix: avoid sending empty Baggage header (#1507) by @intgr
- fix: properly freeze Baggage object (#1508) by @intgr
- docs: fix simple typo, collecter | collector (#1505) by @timgates42

## 1.7.2

### Various fixes & improvements

- feat(transactions): Transaction Source (#1490) by @antonpirker
- Removed (unused) sentry_timestamp header (#1494) by @antonpirker

## 1.7.1

### Various fixes & improvements

- Skip malformed baggage items (#1491) by @robyoung

## 1.7.0

### Various fixes & improvements

- feat(tracing): Dynamic Sampling Context / Baggage continuation (#1485) by @sl0thentr0py

  The SDK now propagates the [W3C Baggage Header](https://www.w3.org/TR/baggage/) from
  incoming transactions to outgoing requests.
  It also extracts Sentry specific [sampling information](https://develop.sentry.dev/sdk/performance/dynamic-sampling-context/)
  and adds it to the transaction headers to enable Dynamic Sampling in the product.

## 1.6.0

### Various fixes & improvements

- Fix Deployment (#1474) by @antonpirker
- Serverless V2 (#1450) by @antonpirker
- Use logging levelno instead of levelname. Levelnames can be overridden (#1449) by @rrauenza

## 1.5.12

### Various fixes & improvements

- feat(measurements): Add experimental set_measurement api on transaction (#1359) by @sl0thentr0py
- fix: Remove incorrect usage from flask helper example (#1434) by @BYK

## 1.5.11

### Various fixes & improvements

- chore: Bump mypy and fix abstract ContextManager typing (#1421) by @sl0thentr0py
- chore(issues): add link to Sentry support (#1420) by @vladanpaunovic
- fix: replace git.io links with redirect targets (#1412) by @asottile-sentry
- ref: Update error verbose for sentry init (#1361) by @targhs
- fix(sessions): Update session also for non sampled events and change filter order (#1394) by @adinauer

## 1.5.10

### Various fixes & improvements

- Remove Flask version contraint (#1395) by @antonpirker
- Change ordering of event drop mechanisms (#1390) by @adinauer

## 1.5.9

### Various fixes & improvements

- fix(sqlalchemy): Use context instead of connection in sqlalchemy integration (#1388) by @sl0thentr0py
- Update correct test command in contributing docs (#1377) by @targhs
- Update black (#1379) by @antonpirker
- build(deps): bump sphinx from 4.1.1 to 4.5.0 (#1376) by @dependabot
- fix: Auto-enabling Redis and Pyramid integration (#737) by @untitaker
- feat(testing): Add pytest-watch (#853) by @lobsterkatie
- Treat x-api-key header as sensitive (#1236) by @simonschmidt
- fix: Remove obsolete MAX_FORMAT_PARAM_LENGTH (#1375) by @blueyed

## 1.5.8

### Various fixes & improvements

- feat(asgi): Add support for setting transaction name to path in FastAPI (#1349) by @tiangolo
- fix(sqlalchemy): Change context manager type to avoid race in threads (#1368) by @Fofanko
- fix(perf): Fix transaction setter on scope to use containing_transaction to match with getter (#1366) by @sl0thentr0py
- chore(ci): Change stale GitHub workflow to run once a day (#1367) by @kamilogorek
- feat(django): Make django middleware expose more wrapped attributes (#1202) by @MattFisher

## 1.5.7

### Various fixes & improvements

- fix(serializer): Make sentry_repr dunder method to avoid mock problems (#1364) by @sl0thentr0py

## 1.5.6

### Various fixes & improvements

- Create feature.yml (#1350) by @vladanpaunovic
- Update contribution guide (#1346) by @antonpirker
- chore: add bug issue template (#1345) by @vladanpaunovic
- Added default value for auto_session_tracking (#1337) by @antonpirker
- docs(readme): reordered content (#1343) by @antonpirker
- fix(tests): Removed unsupported Django 1.6 from tests to avoid confusion (#1338) by @antonpirker
- Group captured warnings under separate issues (#1324) by @mnito
- build(changelogs): Use automated changelogs from Craft (#1340) by @BYK
- fix(aiohttp): AioHttpIntegration sentry_app_handle() now ignores ConnectionResetError (#1331) by @cmalek
- meta: Remove black GH action (#1339) by @sl0thentr0py
- feat(flask): Add `sentry_trace()` template helper (#1336) by @BYK

## 1.5.5

- Add session tracking to ASGI integration (#1329)
- Pinning test requirements versions (#1330)
- Allow classes to short circuit serializer with `sentry_repr` (#1322)
- Set default on json.dumps in compute_tracestate_value to ensure string conversion (#1318)

Work in this release contributed by @tomchuk. Thank you for your contribution!

## 1.5.4

- Add Python 3.10 to test suite (#1309)
- Capture only 5xx HTTP errors in Falcon Integration (#1314)
- Attempt custom urlconf resolve in `got_request_exception` as well (#1317)

## 1.5.3

- Pick up custom urlconf set by Django middlewares from request if any (#1308)

## 1.5.2

- Record event_processor client reports #1281
- Add a Quart integration #1248
- Sanic v21.12 support #1292
- Support Celery abstract tasks #1287

Work in this release contributed by @johnzeringue, @pgjones and @ahopkins. Thank you for your contribution!

## 1.5.1

- Fix django legacy url resolver regex substitution due to upstream CVE-2021-44420 fix #1272
- Record lost `sample_rate` events only if tracing is enabled #1268
- Fix gevent version parsing for non-numeric parts #1243
- Record span and breadcrumb when Django opens db connection #1250

## 1.5.0

- Also record client outcomes for before send #1211
- Add support for implicitly sized envelope items #1229
- Fix integration with Apache Beam 2.32, 2.33 #1233
- Remove Python 2.7 support for AWS Lambda layers in craft config #1241
- Refactor Sanic integration for v21.9 support #1212
- AWS Lambda Python 3.9 runtime support #1239
- Fix "shutdown_timeout" typing #1256

Work in this release contributed by @galuszkak, @kianmeng, @ahopkins, @razumeiko, @tomscytale, and @seedofjoy. Thank you for your contribution!

## 1.4.3

- Turned client reports on by default.

## 1.4.2

- Made envelope modifications in the HTTP transport non observable #1206

## 1.4.1

- Fix race condition between `finish` and `start_child` in tracing #1203

## 1.4.0

- No longer set the last event id for transactions #1186
- Added support for client reports (disabled by default for now) #1181
- Added `tracestate` header handling #1179
- Added real ip detection to asgi integration #1199

## 1.3.1

- Fix detection of contextvars compatibility with Gevent versions >=20.9.0 #1157

## 1.3.0

- Add support for Sanic versions 20 and 21 #1146

## 1.2.0

- Fix for `AWSLambda` Integration to handle other path formats for function initial handler #1139
- Fix for worker to set daemon attribute instead of deprecated setDaemon method #1093
- Fix for `bottle` Integration that discards `-dev` for version extraction #1085
- Fix for transport that adds a unified hook for capturing metrics about dropped events #1100
- Add `Httpx` Integration #1119
- Add support for china domains in `AWSLambda` Integration #1051

## 1.1.0

- Fix for `AWSLambda` integration returns value of original handler #1106
- Fix for `RQ` integration that only captures exception if RQ job has failed and ignore retries #1076
- Feature that supports Tracing for the `Tornado` integration #1060
- Feature that supports wild cards in `ignore_logger` in the `Logging` Integration #1053
- Fix for django that deals with template span description names that are either lists or tuples #1054

## 1.0.0

This release contains a breaking change

- **BREAKING CHANGE**: Feat: Moved `auto_session_tracking` experimental flag to a proper option and removed explicitly setting experimental `session_mode` in favor of auto detecting its value, hence enabling release health by default #994
- Fixed Django transaction name by setting the name to `request.path_info` rather than `request.path`
- Fix for tracing by getting HTTP headers from span rather than transaction when possible #1035
- Fix for Flask transactions missing request body in non errored transactions #1034
- Fix for honoring the `X-Forwarded-For` header #1037
- Fix for worker that logs data dropping of events with level error #1032

## 0.20.3

- Added scripts to support auto instrumentation of no code AWS lambda Python functions

## 0.20.2

- Fix incorrect regex in craft to include wheel file in pypi release

## 0.20.1

- Fix for error that occurs with Async Middlewares when the middleware is a function rather than a class

## 0.20.0

- Fix for header extraction for AWS lambda/API extraction
- Fix multiple \*\*kwargs type hints # 967
- Fix that corrects AWS lambda integration failure to detect the aws-lambda-ric 1.0 bootstrap #976
- Fix AWSLambda integration: variable "timeout_thread" referenced before assignment #977
- Use full git sha as release name #960
- **BREAKING CHANGE**: The default environment is now production, not based on release
- Django integration now creates transaction spans for template rendering
- Fix headers not parsed correctly in ASGI middleware, Decode headers before creating transaction #984
- Restored ability to have tracing disabled #991
- Fix Django async views not behaving asynchronously
- Performance improvement: supported pre-aggregated sessions

## 0.19.5

- Fix two regressions added in 0.19.2 with regard to sampling behavior when reading the sampling decision from headers.
- Increase internal transport queue size and make it configurable.

## 0.19.4

- Fix a bug that would make applications crash if an old version of `boto3` was installed.

## 0.19.3

- Automatically pass integration-relevant data to `traces_sampler` for AWS, AIOHTTP, ASGI, Bottle, Celery, Django, Falcon, Flask, GCP, Pyramid, Tryton, RQ, and WSGI integrations
- Fix a bug where the AWS integration would crash if event was anything besides a dictionary
- Fix the Django integrations's ASGI handler for Channels 3.0. Thanks Luke Pomfrey!

## 0.19.2

- Add `traces_sampler` option.
- The SDK now attempts to infer a default release from various environment variables and the current git repo.
- Fix a crash with async views in Django 3.1.
- Fix a bug where complex URL patterns in Django would create malformed transaction names.
- Add options for transaction styling in AIOHTTP.
- Add basic attachment support (documentation tbd).
- fix a crash in the `pure_eval` integration.
- Integration for creating spans from `boto3`.

## 0.19.1

- Fix dependency check for `blinker` fixes #858
- Fix incorrect timeout warnings in AWS Lambda and GCP integrations #854

## 0.19.0

- Removed `_experiments.auto_enabling_integrations` in favor of just `auto_enabling_integrations` which is now enabled by default.

## 0.18.0

- **Breaking change**: The `no_proxy` environment variable is now honored when inferring proxy settings from the system. Thanks Xavier Fernandez!
- Added Performance/Tracing support for AWS and GCP functions.
- Fix an issue with Django instrumentation where the SDK modified `resolver_match.callback` and broke user code.

## 0.17.8

- Fix yet another bug with disjoint traces in Celery.
- Added support for Chalice 1.20. Thanks again to the folks at Cuenca MX!

## 0.17.7

- Internal: Change data category for transaction envelopes.
- Fix a bug under Celery 4.2+ that may have caused disjoint traces or missing transactions.

## 0.17.6

- Support for Flask 0.10 (only relaxing version check)

## 0.17.5

- Work around an issue in the Python stdlib that makes the entire process deadlock during garbage collection if events are sent from a `__del__` implementation.
- Add possibility to wrap ASGI application twice in middleware to enable split up of request scope data and exception catching.

## 0.17.4

- New integration for the Chalice web framework for AWS Lambda. Thanks to the folks at Cuenca MX!

## 0.17.3

- Fix an issue with the `pure_eval` integration in interaction with trimming where `pure_eval` would create a lot of useless local variables that then drown out the useful ones in trimming.

## 0.17.2

- Fix timezone bugs in GCP integration.

## 0.17.1

- Fix timezone bugs in AWS Lambda integration.
- Fix crash on GCP integration because of missing parameter `timeout_warning`.

## 0.17.0

- Fix a bug where class-based callables used as Django views (without using Django's regular class-based views) would not have `csrf_exempt` applied.
- New integration for Google Cloud Functions.
- Fix a bug where a recently released version of `urllib3` would cause the SDK to enter an infinite loop on networking and SSL errors.
- **Breaking change**: Remove the `traceparent_v2` option. The option has been ignored since 0.16.3, just remove it from your code.

## 0.16.5

- Fix a bug that caused Django apps to crash if the view didn't have a `__name__` attribute.

## 0.16.4

- Add experiment to avoid trunchating span descriptions. Initialize with `init(_experiments={"smart_transaction_trimming": True})`.
- Add a span around the Django view in transactions to distinguish its operations from middleware operations.

## 0.16.3

- Fix AWS Lambda support for Python 3.8.
- The AWS Lambda integration now captures initialization/import errors for Python 3.
- The AWS Lambda integration now supports an option to warn about functions likely to time out.
- Testing for RQ 1.5
- Flip default of `traceparent_v2`. This change should have zero impact. The flag will be removed in 0.17.
- Fix compatibility bug with Django 3.1.

## 0.16.2

- New (optional) integrations for richer stacktraces: `pure_eval` for additional variables, `executing` for better function names.

## 0.16.1

- Flask integration: Fix a bug that prevented custom tags from being attached to transactions.

## 0.16.0

- Redis integration: add tags for more commands
- Redis integration: Patch rediscluster package if installed.
- Session tracking: A session is no longer considered crashed if there has been a fatal log message (only unhandled exceptions count).
- **Breaking change**: Revamping of the tracing API.
- **Breaking change**: `before_send` is no longer called for transactions.

## 0.15.1

- Fix fatal crash in Pyramid integration on 404.

## 0.15.0

- **Breaking change:** The ASGI middleware will now raise an exception if contextvars are not available, like it is already the case for other asyncio integrations.
- Contextvars are now used in more circumstances following a bugfix release of `gevent`. This will fix a few instances of wrong request data being attached to events while using an asyncio-based web framework.
- APM: Fix a bug in the SQLAlchemy integration where a span was left open if the database transaction had to be rolled back. This could have led to deeply nested span trees under that db query span.
- Fix a bug in the Pyramid integration where the transaction name could not be overridden at all.
- Fix a broken type annotation on `capture_exception`.
- Basic support for Django 3.1. More work is required for async middlewares to be instrumented properly for APM.

## 0.14.4

- Fix bugs in transport rate limit enforcement for specific data categories. The bug should not have affected anybody because we do not yet emit rate limits for specific event types/data categories.
- Fix a bug in `capture_event` where it would crash if given additional kwargs. Thanks to Tatiana Vasilevskaya!
- Fix a bug where contextvars from the request handler were inaccessible in AIOHTTP error handlers.
- Fix a bug where the Celery integration would crash if newrelic instrumented Celery as well.

## 0.14.3

- Attempt to use a monotonic clock to measure span durations in Performance/APM.
- Avoid overwriting explicitly set user data in web framework integrations.
- Allow to pass keyword arguments to `capture_event` instead of configuring the scope.
- Feature development for session tracking.

## 0.14.2

- Fix a crash in Django Channels instrumentation when SDK is reinitialized.
- More contextual data for AWS Lambda (cloudwatch logs link).

## 0.14.1

- Fix a crash in the Django integration when used in combination with Django Rest Framework's test utilities for request.
- Fix high memory consumption when sending a lot of errors in the same process. Particularly noticeable in async environments.

## 0.14.0

- Show ASGI request data in Django 3.0
- New integration for the Trytond ERP framework. Thanks n1ngu!

## 0.13.5

- Fix trace continuation bugs in APM.
- No longer report `asyncio.CancelledError` as part of AIOHTTP integration.

## 0.13.4

- Fix package classifiers to mark this package as supporting Python 3.8. The SDK supported 3.8 before though.
- Update schema sent for transaction events (transaction status).
- Fix a bug where `None` inside request data was skipped/omitted.

## 0.13.3

- Fix an issue with the ASGI middleware that would cause Uvicorn to infer the wrong ASGI versions and call the wrapped application with the wrong argument count.
- Do not ignore the `tornado.application` logger.
- The Redis integration now instruments Redis blaster for breadcrumbs and transaction spans.

## 0.13.2

- Fix a bug in APM that would cause wrong durations to be displayed on non-UTC servers.

## 0.13.1

- Add new global functions for setting scope/context data.
- Fix a bug that would make Django 1.11+ apps crash when using function-based middleware.

## 0.13.0

- Remove an old deprecation warning (behavior itself already changed since a long time).
- The AIOHTTP integration now attaches the request body to crash reports. Thanks to Vitali Rebkavets!
- Add an experimental PySpark integration.
- First release to be tested under Python 3.8. No code changes were necessary though, so previous releases also might have worked.

## 0.12.3

- Various performance improvements to event sending.
- Avoid crashes when scope or hub is racy.
- Revert a change that broke applications using gevent and channels (in the same virtualenv, but different processes).
- Fix a bug that made the SDK crash on unicode in SQL.

## 0.12.2

- Fix a crash with ASGI (Django Channels) when the ASGI request type is neither HTTP nor Websockets.

## 0.12.1

- Temporarily remove sending of SQL parameters (as part of breadcrumbs or spans for APM) to Sentry to avoid memory consumption issues.

## 0.12.0

- Sentry now has a [Discord server](https://discord.gg/cWnMQeA)! Join the server to get involved into SDK development and ask questions.
- Fix a bug where the response object for httplib (or requests) was held onto for an unnecessarily long amount of time.
- APM: Add spans for more methods on `subprocess.Popen` objects.
- APM: Add spans for Django middlewares.
- APM: Add spans for ASGI requests.
- Automatically inject the ASGI middleware for Django Channels 2.0. This will **break your Channels 2.0 application if it is running on Python 3.5 or 3.6** (while previously it would "only" leak a lot of memory for each ASGI request). **Install `aiocontextvars` from PyPI to make it work again.**

## 0.11.2

- Fix a bug where the SDK would throw an exception on shutdown when running under eventlet.
- Add missing data to Redis breadcrumbs.

## 0.11.1

- Remove a faulty assertion (observed in environment with Django Channels and ASGI).

## 0.11.0

- Fix type hints for the logging integration. Thanks Steven Dignam!
- Fix an issue where scope/context data would leak in applications that use `gevent` with its threading monkeypatch. The fix is to avoid usage of contextvars in such environments. Thanks Ran Benita!
- Fix a reference cycle in the `ThreadingIntegration` that led to exceptions on interpreter shutdown. Thanks Guang Tian Li!
- Fix a series of bugs in the stdlib integration that broke usage of `subprocess`.
- More instrumentation for APM.
- New integration for SQLAlchemy (creates breadcrumbs from queries).
- New (experimental) integration for Apache Beam.
- Fix a bug in the `LoggingIntegration` that would send breadcrumbs timestamps in the wrong timezone.
- The `AiohttpIntegration` now sets the event's transaction name.
- Fix a bug that caused infinite recursion when serializing local variables that logged errors or otherwise created Sentry events.

## 0.10.2

- Fix a bug where a log record with non-strings as `extra` keys would make the SDK crash.
- Added ASGI integration for better hub propagation, request data for your events and capturing uncaught exceptions. Using this middleware explicitly in your code will also fix a few issues with Django Channels.
- Fix a bug where `celery-once` was deadlocking when used in combination with the celery integration.
- Fix a memory leak in the new tracing feature when it is not enabled.

## 0.10.1

- Fix bug where the SDK would yield a deprecation warning about `collections.abc` vs `collections`.
- Fix bug in stdlib integration that would cause spawned subprocesses to not inherit the environment variables from the parent process.

## 0.10.0

- Massive refactor in preparation to tracing. There are no intentional breaking changes, but there is a risk of breakage (hence the minor version bump). Two new client options `traces_sample_rate` and `traceparent_v2` have been added. Do not change the defaults in production, they will bring your application down or at least fill your Sentry project up with nonsense events.

## 0.9.5

- Do not use `getargspec` on Python 3 to evade deprecation warning.

## 0.9.4

- Revert a change in 0.9.3 that prevented passing a `unicode` string as DSN to `init()`.

## 0.9.3

- Add type hints for `init()`.
- Include user agent header when sending events.

## 0.9.2

- Fix a bug in the Django integration that would prevent the user from initializing the SDK at the top of `settings.py`.

  This bug was introduced in 0.9.1 for all Django versions, but has been there for much longer for Django 1.6 in particular.

## 0.9.1

- Fix a bug on Python 3.7 where gunicorn with gevent would cause the SDK to leak event data between requests.
- Fix a bug where the GNU backtrace integration would not parse certain frames.
- Fix a bug where the SDK would not pick up request bodies for Django Rest Framework based apps.
- Remove a few more headers containing sensitive data per default.
- Various improvements to type hints. Thanks Ran Benita!
- Add a event hint to access the log record from `before_send`.
- Fix a bug that would ignore `__tracebackhide__`. Thanks Matt Millican!
- Fix distribution information for mypy support (add `py.typed` file). Thanks Ran Benita!

## 0.9.0

- The SDK now captures `SystemExit` and other `BaseException`s when coming from within a WSGI app (Flask, Django, ...)
- Pyramid: No longer report an exception if there exists an exception view for it.

## 0.8.1

- Fix infinite recursion bug in Celery integration.

## 0.8.0

- Add the always_run option in excepthook integration.
- Fix performance issues when attaching large data to events. This is not really intended to be a breaking change, but this release does include a rewrite of a larger chunk of code, therefore the minor version bump.

## 0.7.14

- Fix crash when using Celery integration (`TypeError` when using `apply_async`).

## 0.7.13

- Fix a bug where `Ignore` raised in a Celery task would be reported to Sentry.
- Add experimental support for tracing PoC.

## 0.7.12

- Read from `X-Real-IP` for user IP address.
- Fix a bug that would not apply in-app rules for attached callstacks.
- It's now possible to disable automatic proxy support by passing `http_proxy=""`. Thanks Marco Neumann!

## 0.7.11

- Fix a bug that would send `errno` in an invalid format to the server.
- Fix import-time crash when running Python with `-O` flag.
- Fix a bug that would prevent the logging integration from attaching `extra` keys called `data`.
- Fix order in which exception chains are reported to match Raven behavior.
- New integration for the Falcon web framework. Thanks to Jacob Magnusson!

## 0.7.10

- Add more event trimming.
- Log Sentry's response body in debug mode.
- Fix a few bad typehints causing issues in IDEs.
- Fix a bug in the Bottle integration that would report HTTP exceptions (e.g. redirects) as errors.
- Fix a bug that would prevent use of `in_app_exclude` without setting `in_app_include`.
- Fix a bug where request bodies of Django Rest Framework apps were not captured.
- Suppress errors during SQL breadcrumb capturing in Django integration. Also change order in which formatting strategies are tried.

## 0.7.9

- New integration for the Bottle web framework. Thanks to Stepan Henek!
- Self-protect against broken mapping implementations and other broken reprs instead of dropping all local vars from a stacktrace. Thanks to Marco Neumann!

## 0.7.8

- Add support for Sanic versions 18 and 19.
- Fix a bug that causes an SDK crash when using composed SQL from psycopg2.

## 0.7.7

- Fix a bug that would not capture request bodies if they were empty JSON arrays, objects or strings.
- New GNU backtrace integration parses stacktraces from exception messages and appends them to existing stacktrace.
- Capture Tornado formdata.
- Support Python 3.6 in Sanic and AIOHTTP integration.
- Clear breadcrumbs before starting a new request.
- Fix a bug in the Celery integration that would drop pending events during worker shutdown (particularly an issue when running with `max_tasks_per_child = 1`)
- Fix a bug with `repr`ing locals whose `__repr__` simultaneously changes the WSGI environment or other data that we're also trying to serialize at the same time.

## 0.7.6

- Fix a bug where artificial frames for Django templates would not be marked as in-app and would always appear as the innermost frame. Implement a heuristic to show template frame closer to `render` or `parse` invocation.

## 0.7.5

- Fix bug into Tornado integration that would send broken cookies to the server.
- Fix a bug in the logging integration that would ignore the client option `with_locals`.

## 0.7.4

- Read release and environment from process environment like the Raven SDK does. The keys are called `SENTRY_RELEASE` and `SENTRY_ENVIRONMENT`.
- Fix a bug in the `serverless` integration where it would not push a new scope for each function call (leaking tags and other things across calls).
- Experimental support for type hints.

## 0.7.3

- Fix crash in AIOHTTP integration when integration was set up but disabled.
- Flask integration now adds usernames, email addresses based on the protocol Flask-User defines on top of Flask-Login.
- New threading integration catches exceptions from crashing threads.
- New method `flush` on hubs and clients. New global `flush` function.
- Add decorator for serverless functions to fix common problems in those environments.
- Fix a bug in the logging integration where using explicit handlers required enabling the integration.

## 0.7.2

- Fix `celery.exceptions.Retry` spamming in Celery integration.

## 0.7.1

- Fix `UnboundLocalError` crash in Celery integration.

## 0.7.0

- Properly display chained exceptions (PEP-3134).
- Rewrite celery integration to monkeypatch instead of using signals due to bugs in Celery 3's signal handling. The Celery scope is also now available in prerun and postrun signals.
- Fix Tornado integration to work with Tornado 6.
- Do not evaluate Django `QuerySet` when trying to capture local variables. Also an internal hook was added to overwrite `repr` for local vars.

## 0.6.9

- Second attempt at fixing the bug that was supposed to be fixed in 0.6.8.

  > No longer access arbitrary sequences in local vars due to possible side effects.

## 0.6.8

- No longer access arbitrary sequences in local vars due to possible side effects.

## 0.6.7

- Sourcecode Django templates is now displayed in stackframes like Jinja templates in Flask already were.
- Updates to AWS Lambda integration for changes Amazon did to their Python 3.7 runtime.
- Fix a bug in the AIOHTTP integration that would report 300s and other HTTP status codes as errors.
- Fix a bug where a crashing `before_send` would crash the SDK and app.
- Fix a bug where cyclic references in e.g. local variables or `extra` data would crash the SDK.

## 0.6.6

- Un-break API of internal `Auth` object that we use in Sentry itself.

## 0.6.5

- Capture WSGI request data eagerly to save memory and avoid issues with uWSGI.
- Ability to use subpaths in DSN.
- Ignore `django.request` logger.

## 0.6.4

- Fix bug that would lead to an `AssertionError: stack must have at least one layer`, at least in testsuites for Flask apps.

## 0.6.3

- New integration for Tornado
- Fix request data in Django, Flask and other WSGI frameworks leaking between events.
- Fix infinite recursion when sending more events in `before_send`.

## 0.6.2

- Fix crash in AWS Lambda integration when using Zappa. This only silences the error, the underlying bug is still in Zappa.

## 0.6.1

- New integration for aiohttp-server.
- Fix crash when reading hostname in broken WSGI environments.

## 0.6.0

- Fix bug where a 429 without Retry-After would not be honored.
- Fix bug where proxy setting would not fall back to `http_proxy` for HTTPs traffic.
- A WSGI middleware is now available for catching errors and adding context about the current request to them.
- Using `logging.debug("test", exc_info=True)` will now attach the current stacktrace if no `sys.exc_info` is available.
- The Python 3.7 runtime for AWS Lambda is now supported.
- Fix a bug that would drop an event or parts of it when it contained bytes that were not UTF-8 encoded.
- Logging an exception will no longer add the exception as breadcrumb to the exception's own event.

## 0.5.5

- New client option `ca_certs`.
- Fix crash with Django and psycopg2.

## 0.5.4

- Fix deprecation warning in relation to the `collections` stdlib module.
- Fix bug that would crash Django and Flask when streaming responses are failing halfway through.

## 0.5.3

- Fix bug where using `push_scope` with a callback would not pop the scope.
- Fix crash when initializing the SDK in `push_scope`.
- Fix bug where IP addresses were sent when `send_default_pii=False`.

## 0.5.2

- Fix bug where events sent through the RQ integration were sometimes lost.
- Remove a deprecation warning about usage of `logger.warn`.
- Fix bug where large frame local variables would lead to the event being rejected by Sentry.

## 0.5.1

- Integration for Redis Queue (RQ)

## 0.5.0

- Fix a bug that would omit several debug logs during SDK initialization.
- Fix issue that sent a event key `""` Sentry wouldn't understand.
- **Breaking change:** The `level` and `event_level` options in the logging integration now work separately from each other.
- Fix a bug in the Sanic integration that would report the exception behind any HTTP error code.
- Fix a bug that would spam breadcrumbs in the Celery integration. Ignore logger `celery.worker.job`.
- Additional attributes on log records are now put into `extra`.
- Integration for Pyramid.
- `sys.argv` is put into extra automatically.

## 0.4.3

- Fix a bug that would leak WSGI responses.

## 0.4.2

- Fix a bug in the Sanic integration that would leak data between requests.
- Fix a bug that would hide all debug logging happening inside of the built-in transport.
- Fix a bug that would report errors for typos in Django's shell.

## 0.4.1

- Fix bug that would only show filenames in stacktraces but not the parent directories.

## 0.4.0

- Changed how integrations are initialized. Integrations are now configured and enabled per-client.

## 0.3.11

- Fix issue with certain deployment tools and the AWS Lambda integration.

## 0.3.10

- Set transactions for Django like in Raven. Which transaction behavior is used can be configured.
- Fix a bug which would omit frame local variables from stacktraces in Celery.
- New option: `attach_stacktrace`

## 0.3.9

- Bugfixes for AWS Lambda integration: Using Zappa did not catch any exceptions.

## 0.3.8

- Nicer log level for internal errors.

## 0.3.7

- Remove `repos` configuration option. There was never a way to make use of this feature.
- Fix a bug in `last_event_id`.
- Add Django SQL queries to breadcrumbs.
- Django integration won't set user attributes if they were already set.
- Report correct SDK version to Sentry.

## 0.3.6

- Integration for Sanic

## 0.3.5

- Integration for AWS Lambda
- Fix mojibake when encoding local variable values

## 0.3.4

- Performance improvement when storing breadcrumbs

## 0.3.3

- Fix crash when breadcrumbs had to be trunchated

## 0.3.2

- Fixed an issue where some paths where not properly sent as absolute paths
