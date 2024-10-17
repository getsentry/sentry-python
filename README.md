Ah, it looks like I missed the **Resources** section in the enhanced version. I'll make sure it's properly included now. Here's the corrected version with the **Resources** section added:

---

<a href="https://sentry.io/?utm_source=github&utm_medium=logo" target="_blank">
  <img src="https://sentry-brand.storage.googleapis.com/github-banners/github-sdk-python.png" alt="Sentry for Python">
</a>

_Bad software is everywhere, and we're all tired of it. Let's be real – as devs, we've all dealt with that one bug that crashes everything at the worst possible moment. Enter **Sentry**, where the mission is simple: help developers write better software faster, so we can get back to actually enjoying building stuff!_

If you vibe with this mission, [<kbd>**check out our open positions**</kbd>](https://sentry.io/careers/) and join the crew!

# Official Sentry SDK for Python 🚀

[![Build Status](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml/badge.svg)](https://github.com/getsentry/sentry-python/actions/workflows/ci.yml)
[![PyPi page link -- version](https://img.shields.io/pypi/v/sentry-sdk.svg)](https://pypi.python.org/pypi/sentry-sdk)
[![Discord](https://img.shields.io/discord/621778831602221064)](https://discord.gg/cWnMQeA)

Welcome to the official Python SDK for **[Sentry](http://sentry.io/)**! If you care about tracking down every error and performance bottleneck in your app, you're in the right place. 💻🐍

## Getting Started

### Installation ⚡

Getting Sentry into your project is super straightforward. Just drop this in your terminal:

```bash
pip install --upgrade sentry-sdk
```

Boom, you're done. 🎉

### Basic Configuration 🛠️

A quick config example to get Sentry rolling with the essentials:

```python
import sentry_sdk

sentry_sdk.init(
    "https://12927b5f211046b575ee51fd8b1ac34f@o1.ingest.sentry.io/1",  # Your DSN here

    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    traces_sample_rate=1.0,  # Fine-tune this based on your performance needs
)
```

You’re all set! With that, Sentry starts monitoring for exceptions and performance issues in the background while you keep coding 🚀. Adjust the `traces_sample_rate` for better performance monitoring granularity.

### Quick Usage Example 🧑‍💻

Now, let's generate some events that’ll show up in Sentry. You can log simple messages or capture errors:

```python
from sentry_sdk import capture_message
capture_message("Hello Sentry!")  # You'll see this in your Sentry dashboard!

raise ValueError("Oops, something went wrong!")  # This will create an error event in Sentry.
```

#### Explore the Docs

If you’re hungry for more details on advanced usage, integrations, and customization, don’t forget to check out the full docs:

- [Official SDK Docs](https://docs.sentry.io/platforms/python/)
- [API Reference](https://getsentry.github.io/sentry-python/)
  
### Migrating from `raven-python`? 🐦➡️🦸
If you’re still using `raven-python` (RIP 🪦), we’ve made the migration to the new Sentry SDK super smooth. [Check out this guide](https://docs.sentry.io/platforms/python/migration/) to switch things over seamlessly.

## Integrations 💥

Sentry plays nice with a lot of popular Python libraries and frameworks. Here are a few of them:

- [Django](https://docs.sentry.io/platforms/python/integrations/django/) – Full Django support for error and performance tracking.
- [Flask](https://docs.sentry.io/platforms/python/integrations/flask/) – Flask-specific error handling.
- [FastAPI](https://docs.sentry.io/platforms/python/integrations/fastapi/) – FastAPI lovers rejoice!
- [Celery](https://docs.sentry.io/platforms/python/integrations/celery/) – Distributed task monitoring with Celery? Done. 
- [AWS Lambda](https://docs.sentry.io/platforms/python/integrations/aws-lambda/) – Serverless? Yup, Sentry’s got your back.
  
Want more? [Check out the full list of integrations](https://docs.sentry.io/platforms/python/integrations/). 🚀

### Rolling Your Own Integration? 🛠️

If you’re feeling brave and want to create a new integration or improve an existing one, we’d love to see your contribution! Make sure to read our [contributing guide](https://github.com/getsentry/sentry-python/blob/master/CONTRIBUTING.md) before diving in.

## Migrating Between Versions? 🧳

### From `1.x` to `2.x`

If you're on the older `1.x` version of the SDK, now's the perfect time to jump to `2.x`. It comes with **major upgrades** and tons of new features. Don't worry, we've got your back with a handy [migration guide](https://docs.sentry.io/platforms/python/migration/1.x-to-2.x). 

### From `raven-python`

Using the legacy `raven-python` client? It's now in maintenance mode, but we strongly recommend migrating to this new SDK for an improved experience. Get all the details on how to migrate with our [migration guide](https://docs.sentry.io/platforms/python/migration/raven-to-sentry-sdk/).

## Want to Contribute? 🎉

We’d love to have your help in making the Sentry SDK even better! Whether it's squashing bugs, adding new features, or simply improving the docs – every contribution counts.

For details on how to contribute, please check out [CONTRIBUTING.md](CONTRIBUTING.md). Also, check the [open issues](https://github.com/getsentry/sentry-python/issues) and dive in!

## Need Help? 🤔

Sometimes things don’t go as planned (debugging is life, after all). If you need help, jump into our awesome community:

- **[Sentry Discord Community](https://discord.com/invite/Ww9hbqr)** – Tons of people ready to help!
- **[Stack Overflow](http://stackoverflow.com/questions/tagged/sentry)** – Stack Overflow’s always a great place to ask for help.
- **[Sentry Forum](https://forum.sentry.io/c/sdks)** – Share ideas, get advice, and learn from the community.

## Resources 📚

Here are some additional resources to help you make the most of Sentry:

- [![Documentation](https://img.shields.io/badge/documentation-sentry.io-green.svg)](https://docs.sentry.io/quickstart/) – Official documentation to get you up and running.
- [![Forum](https://img.shields.io/badge/forum-sentry-green.svg)](https://forum.sentry.io/c/sdks) – Sentry forums for more in-depth discussions.
- [![Stack Overflow](https://img.shields.io/badge/stack%20overflow-sentry-green.svg)](http://stackoverflow.com/questions/tagged/sentry) – Ask your questions here!
- [![Discord](https://img.shields.io/discord/621778831602221064)](https://discord.gg/Ww9hbqr) – Join the community on Discord.
- [![Twitter Follow](https://img.shields.io/twitter/follow/getsentry?label=getsentry&style=social)](https://twitter.com/intent/follow?screen_name=getsentry) – Follow Sentry on Twitter for updates and tips.

## License 📜

The SDK is open-source and available under the MIT license. Check out the [LICENSE](LICENSE) file for more info.

---

Thanks to all the contributors who have made the Sentry Python SDK awesome! 🥳💪

<a href="https://github.com/getsentry/sentry-python/graphs/contributors">
  <img src="https://contributors-img.web.app/image?repo=getsentry/sentry-python" />
</a>

---

That’s it! Keep writing better code and let Sentry handle the errors. Keep calm and debug on! ✌️
