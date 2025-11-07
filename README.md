<a href="https://sentry.io/?utm_source=github&utm_medium=logo" target="_blank">
  <img src="https://sentry-brand.storage.googleapis.com/github-banners/github-sdk-python.png" alt="Sentry for Python">
</a>
<div align="center">

_Bad software is everywhere, and we're tired of it. Sentry is on a mission to help developers write better software faster, so we can get back to enjoying technology. If you want to join us
[<kbd>**Check out our open positions**</kbd>](https://sentry.io/careers/)_.

[![Discord](https://img.shields.io/discord/621778831602221064?logo=discord&labelColor=%20%235462eb&logoColor=%20%23f5f5f5&color=%20%235462eb)](https://discord.com/invite/Ww9hbqr)
[![X Follow](https://img.shields.io/twitter/follow/sentry?label=sentry&style=social)](https://x.com/intent/follow?screen_name=sentry)
[![PyPi page link -- version](https://img.shields.io/pypi/v/sentry-sdk.svg)](https://pypi.python.org/pypi/sentry-sdk)
<img src="https://img.shields.io/badge/python-3.7 | 3.8 | 3.9 | 3.10 | 3.11 | 3.12 | 3.13-blue.svg" alt="python">
[![Build Status](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml/badge.svg)](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml)

<br/>

</div>


# Official Sentry SDK for Python

Welcome to the official Python SDK for **[Sentry](http://sentry.io/)**.


## 📦 Getting Started

### Prerequisites

You need a Sentry [account](https://sentry.io/signup/) and [project](https://docs.sentry.io/product/projects/).

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
    # of traces for performance monitoring.
    traces_sample_rate=1.0,
)
```

With this configuration, Sentry will monitor for exceptions and performance issues.

### Quick Usage Example

To generate some events that will show up in Sentry, you can log messages or capture errors:

```python
import sentry_sdk
sentry_sdk.init(...)  # same as above

sentry_sdk.capture_message("Hello Sentry!")  # You'll see this in your Sentry dashboard.

raise ValueError("Oops, something went wrong!")  # This will create an error event in Sentry.
```


## 📚 Documentation

For more details on advanced usage, integrations, and customization, check out the full documentation on [https://docs.sentry.io](https://docs.sentry.io/).


## 🧩 Integrations

Sentry integrates with a ton of popular Python libraries and frameworks, including [FastAPI](https://docs.sentry.io/platforms/python/integrations/fastapi/), [Django](https://docs.sentry.io/platforms/python/integrations/django/), [Celery](https://docs.sentry.io/platforms/python/integrations/celery/), [OpenAI](https://docs.sentry.io/platforms/python/integrations/openai/) and many, many more.  Check out the [full list of integrations](https://docs.sentry.io/platforms/python/integrations/) to get the full picture.


## 🚧 Migrating Between Versions?

### From `1.x` to `2.x`

If you're using the older `1.x` version of the SDK, now's the time to upgrade to `2.x`. It includes significant upgrades and new features. Check our [migration guide](https://docs.sentry.io/platforms/python/migration/1.x-to-2.x) for assistance.

### From `raven-python`

Using the legacy `raven-python` client? It's now in maintenance mode, and we recommend migrating to the new SDK for an improved experience. Get all the details in our [migration guide](https://docs.sentry.io/platforms/python/migration/raven-to-sentry-sdk/).


## 🙌 Want to Contribute?

We'd love your help in improving the Sentry SDK! Whether it's fixing bugs, adding features, writing new integrations, or enhancing documentation, every contribution is valuable.

For details on how to contribute, please read our [contribution guide](CONTRIBUTING.md) and explore the [open issues](https://github.com/getsentry/sentry-python/issues).


## 🛟 Need Help?

If you encounter issues or need help setting up or configuring the SDK, don't hesitate to reach out to the [Sentry Community on Discord](https://discord.com/invite/Ww9hbqr). There is a ton of great people there ready to help!


## 🔗 Resources

Here are all resources to help you make the most of Sentry:

- [Documentation](https://docs.sentry.io/platforms/python/) - Official documentation to get started.
- [Discord](https://discord.com/invite/Ww9hbqr) - Join our Discord community.
- [X/Twitter](https://x.com/intent/follow?screen_name=sentry) -  Follow us on X (Twitter) for updates.
- [Stack Overflow](https://stackoverflow.com/questions/tagged/sentry) - Questions and answers related to Sentry.

<a name="license"></a>
## 📃 License

The SDK is open-source and available under the MIT license. Check out the [LICENSE](LICENSE) file for more information.


## 😘 Contributors

Thanks to everyone who has helped improve the SDK!

<a href="https://github.com/getsentry/sentry-python/graphs/contributors">
  <img src="https://contributors-img.web.app/image?repo=getsentry/sentry-python" />
</a>
