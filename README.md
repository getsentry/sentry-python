<a href="https://sentry.io/?utm_source=github&utm_medium=logo" target="_blank">
  <img src="https://sentry-brand.storage.googleapis.com/github-banners/github-sdk-python.png" alt="Sentry for Python">
</a>


_Bad software is everywhere, and we're tired of it. Sentry is on a mission to help developers write better software faster, so we can get back to enjoying technology. If you want to join us, [<kbd>**check out our open positions**</kbd>](https://sentry.io/careers/)_.

# Official Sentry SDK for Python

[![Build Status](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml/badge.svg)](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml)
[![PyPi page link -- version](https://img.shields.io/pypi/v/sentry-sdk.svg)](https://pypi.python.org/pypi/sentry-sdk)
[![Discord](https://img.shields.io/discord/621778831602221064)](https://discord.gg/cWnMQeA)

Welcome to the official Python SDK for **[Sentry](http://sentry.io/)**!

## :rocket: Getting Started

### Installation

Getting Sentry into your project is straightforward. Just run this command in your terminal:

```bash
pip install --upgrade sentry-sdk
```

### Basic Configuration

Here's a quick configuration example to get Sentry up and running:

```python
import sentry_sdk

sentry_sdk.init(
    "https://12927b5f211046b575ee51fd8b1ac34f@o1.ingest.sentry.io/1",  # Your DSN here

    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    traces_sample_rate=1.0,
)
```

With this configuration, Sentry will monitor for exceptions and performance issues.

### Quick Usage Example

To generate some events that will show up in Sentry, you can log messages or capture errors:

```python
from sentry_sdk import capture_message
capture_message("Hello Sentry!")  # You'll see this in your Sentry dashboard.

raise ValueError("Oops, something went wrong!")  # This will create an error event in Sentry.
```

#### Explore the Docs

For more details on advanced usage, integrations, and customization, check out the full documentation:

- [Official SDK Docs](https://docs.sentry.io/platforms/python/)
- [API Reference](https://getsentry.github.io/sentry-python/)

## :electric_plug: Integrations

Sentry integrates with many popular Python libraries and frameworks, including:

- [Django](https://docs.sentry.io/platforms/python/integrations/django/)
- [Flask](https://docs.sentry.io/platforms/python/integrations/flask/)
- [FastAPI](https://docs.sentry.io/platforms/python/integrations/fastapi/)
- [Celery](https://docs.sentry.io/platforms/python/integrations/celery/)
- [AWS Lambda](https://docs.sentry.io/platforms/python/integrations/aws-lambda/)

Want more? [Check out the full list of integrations](https://docs.sentry.io/platforms/python/integrations/).

### Rolling Your Own Integration?

If you want to create a new integration or improve an existing one, we'd welcome your contributions! Please read our [contributing guide](https://github.com/getsentry/sentry-python/blob/master/CONTRIBUTING.md) before starting.

## :arrows_counterclockwise: Migrating Between Versions?

### From `1.x` to `2.x`

If you're using the older `1.x` version of the SDK, now's the time to upgrade to `2.x`. It includes significant upgrades and new features. Check our [migration guide](https://docs.sentry.io/platforms/python/migration/1.x-to-2.x) for assistance.

### From `raven-python`

Using the legacy `raven-python` client? It's now in maintenance mode, and we recommend migrating to the new SDK for an improved experience. Get all the details in our [migration guide](https://docs.sentry.io/platforms/python/migration/raven-to-sentry-sdk/).

## :raised_hands: Want to Contribute?

We'd love your help in improving the Sentry SDK! Whether it's fixing bugs, adding features, or enhancing documentation, every contribution is valuable.

For details on how to contribute, please check out [CONTRIBUTING.md](CONTRIBUTING.md) and explore the [open issues](https://github.com/getsentry/sentry-python/issues).

## :lifering: Need Help?

If you encounter issues or need help setting up or configuring the SDK, don't hesitate to reach out to the [Sentry Community on Discord](https://discord.com/invite/Ww9hbqr). There is a ton of great people there ready to help!

## :books: Resources

Here are additional resources to help you make the most of Sentry:

- [![Documentation](https://img.shields.io/badge/documentation-sentry.io-green.svg)](https://docs.sentry.io/quickstart/) – Official documentation to get started.
- [![Discord](https://img.shields.io/discord/621778831602221064)](https://discord.gg/Ww9hbqr) – Join our Discord community.
- [![Twitter Follow](https://img.shields.io/twitter/follow/getsentry?label=getsentry&style=social)](https://twitter.com/intent/follow?screen_name=getsentry) – Follow us on X (Twitter) for updates.
- [![Stack Overflow](https://img.shields.io/badge/stack%20overflow-sentry-green.svg)](http://stackoverflow.com/questions/tagged/sentry) – Questions and answers related to Sentry.

## :page_with_curl: License

The SDK is open-source and available under the MIT license. Check out the [LICENSE](LICENSE) file for more information.

---

## :kissing_heart: Contributors

Thanks to everyone who has helped improve the SDK!

<a href="https://github.com/getsentry/sentry-python/graphs/contributors">
  <img src="https://contributors-img.web.app/image?repo=getsentry/sentry-python" />
</a>
