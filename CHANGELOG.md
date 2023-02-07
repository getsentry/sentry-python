# Changelog

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
  from sentry_sdk.tracing import TRANSACTION_SOURCE_COMPONENT, Transaction


  def main():
      sentry_sdk.init(
          dsn="...",
          integrations=[
              HueyIntegration(),
          ],
          traces_sample_rate=1.0,
      )

      with sentry_sdk.start_transaction(name="testing_huey_tasks", source=TRANSACTION_SOURCE_COMPONENT):
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
