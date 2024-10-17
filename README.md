<a href="https://sentry.io/?utm_source=github&utm_medium=logo" target="_blank">
  <img src="https://sentry-brand.storage.googleapis.com/github-banners/github-sdk-python.png" alt="Sentry for Python">
</a>

Bad software is everywhere, and developers often deal with bugs that crash systems at the worst possible moments. Enter **Sentry**: our mission is to help developers write better software faster, so you can focus on building.

# Official Sentry SDK for Python

[![Build Status](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml/badge.svg)](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml)
[![PyPi page link -- version](https://img.shields.io/pypi/v/sentry-sdk.svg)](https://pypi.python.org/pypi/sentry-sdk)
[![Discord](https://img.shields.io/discord/621778831602221064)](https://discord.gg/cWnMQeA)

Welcome to the official Python SDK for **[Sentry](http://sentry.io/)**! If you care about tracking down every error and performance bottleneck in your app, you're in the right place.

## Getting Started

### Installation

Getting Sentry into your project is straightforward. Just run this command in your terminal:

```bash
pip install --upgrade sentry-sdk
```

### Basic Configuration

Here’s a quick configuration example to get Sentry up and running:

```python
import sentry_sdk

sentry_sdk.init(
    "https://12927b5f211046b575ee51fd8b1ac34f@o1.ingest.sentry.io/1",  # Your DSN here

    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    traces_sample_rate=1.0,  # Adjust this based on your performance needs
)
```

With this configuration, Sentry will monitor for exceptions and performance issues in the background while you continue coding. Adjust the `traces_sample_rate` to fine-tune performance monitoring.

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

### Migrating from `raven-python`?  
If you’re still using `raven-python`, migrating to the new Sentry SDK is smooth. [Check out this guide](https://docs.sentry.io/platforms/python/migration/) for details.

## Integrations

Sentry integrates with several popular Python libraries and frameworks, including:

- [Django](https://docs.sentry.io/platforms/python/integrations/django/) – Full support for error and performance tracking.
- [Flask](https://docs.sentry.io/platforms/python/integrations/flask/) – Flask-specific error handling.
- [FastAPI](https://docs.sentry.io/platforms/python/integrations/fastapi/) – FastAPI support.
- [Celery](https://docs.sentry.io/platforms/python/integrations/celery/) – Monitoring distributed tasks.
- [AWS Lambda](https://docs.sentry.io/platforms/python/integrations/aws-lambda/) – Support for serverless applications.

Want more? [Check out the full list of integrations](https://docs.sentry.io/platforms/python/integrations/).

### Rolling Your Own Integration?

If you want to create a new integration or improve an existing one, we’d welcome your contributions! Please read our [contributing guide](https://github.com/getsentry/sentry-python/blob/master/CONTRIBUTING.md) before starting.

## Migrating Between Versions?

### From `1.x` to `2.x`

If you're using the older `1.x` version of the SDK, now's the time to upgrade to `2.x`. It includes significant upgrades and new features. Check our [migration guide](https://docs.sentry.io/platforms/python/migration/1.x-to-2.x) for assistance.

### From `raven-python`

Using the legacy `raven-python` client? It's now in maintenance mode, and we recommend migrating to the new SDK for an improved experience. Get all the details in our [migration guide](https://docs.sentry.io/platforms/python/migration/raven-to-sentry-sdk/).

## Want to Contribute?

We’d love your help in improving the Sentry SDK! Whether it’s fixing bugs, adding features, or enhancing documentation, every contribution is valuable.

For details on how to contribute, please check out [CONTRIBUTING.md](CONTRIBUTING.md) and explore the [open issues](https://github.com/getsentry/sentry-python/issues).

## Need Help?

If you encounter issues, don’t hesitate to reach out to our community for support:

- **[Sentry Discord Community](https://discord.com/invite/Ww9hbqr)** – A helpful community is ready to assist.
- **[Stack Overflow](http://stackoverflow.com/questions/tagged/sentry)** – A great place for asking questions.
- **[Sentry Forum](https://forum.sentry.io/c/sdks)** – Share ideas and get advice.

## Resources

Here are additional resources to help you make the most of Sentry:

- [![Documentation](https://img.shields.io/badge/documentation-sentry.io-green.svg)](https://docs.sentry.io/quickstart/) – Official documentation to get started.
- [![Forum](https://img.shields.io/badge/forum-sentry-green.svg)](https://forum.sentry.io/c/sdks) – Forums for in-depth discussions.
- [![Stack Overflow](https://img.shields.io/badge/stack%20overflow-sentry-green.svg)](http://stackoverflow.com/questions/tagged/sentry) – Ask questions here.
- [![Discord](https://img.shields.io/discord/621778831602221064)](https://discord.gg/Ww9hbqr) – Join our Discord community.
- [![Twitter Follow](https://img.shields.io/twitter/follow/getsentry?label=getsentry&style=social)](https://twitter.com/intent/follow?screen_name=getsentry) – Follow us on Twitter for updates.

## License

The SDK is open-source and available under the MIT license. Check out the [LICENSE](LICENSE) file for more information.

---

Thanks to all the contributors who have made the Sentry Python SDK great!

<a href="https://github.com/getsentry/sentry-python/graphs/contributors">
  <img src="https://contributors-img.web.app/image?repo=getsentry/sentry-python" />
</a>

---

Keep writing better code and let Sentry handle the errors.
