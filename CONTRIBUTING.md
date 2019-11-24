# How to contribute to the Sentry Python SDK

`sentry-sdk` is an ordinary Python package. You can install it with `pip
install -e .` into some virtualenv, edit the sourcecode and test out your
changes manually.

## Community

The public-facing channels for support and development of Sentry SDKs can be found on [Discord](https://discord.gg/Ww9hbqr).

## Running tests and linters

Make sure you have `virtualenv` installed, and the Python versions you care
about. You should have Python 2.7 and the latest Python 3 installed.

You don't need to `workon` or `activate` anything, the `Makefile` will create
one for you. Run `make` or `make help` to list commands.

## Releasing a new version

We use [craft](https://github.com/getsentry/craft#python-package-index-pypi) to
release new versions. You need credentials for the `getsentry` PyPI user, and
must have `twine` installed globally.

The usual release process goes like this:

1. Go through git log and write new entry into `CHANGES.md`, commit to master
2. `craft p a.b.c`
3. `craft pp a.b.c`

## Adding a new integration (checklist)

1. Write the integration.

    * Instrument all application instances by default. Prefer global signals/patches instead of configuring a specific instance. Don't make the user pass anything to your integration for anything to work. Aim for zero configuration.

    * Everybody monkeypatches. That means:

      * Make sure to think about conflicts with other monkeypatches when monkeypatching.

      * You don't need to feel bad about it.

    * Avoid modifying the hub, registering a new client or the like. The user drives the client, and the client owns integrations.

    * Allow the user to disable the integration by changing the client. Check `Hub.current.get_integration(MyIntegration)` from within your signal handlers to see if your integration is still active before you do anything impactful (such as sending an event).

3. Update package metadata.

    * We use `extras_require` in `setup.py` to communicate minimum version requirements for integrations. People can use this in combination with tools like Poetry or Pipenv to detect conflicts between our supported versions and their used versions programmatically.

      Do not set upper-bounds on version requirements as people are often faster in adopting new versions of a web framework than we are in adding them to the test matrix or our package metadata.

2. Write the [docs](https://github.com/getsentry/sentry-docs). Answer the following questions:

    * What does your integration do? Split in two sections: Executive summary at top and exact behavior further down.

    * Which version of the SDK supports which versions of the modules it hooks into?

    * One code example with basic setup.

    * Make sure to add integration page to `python/index.md` (people forget to do that all the time).

  Tip: Put most relevant parts wrapped in `<!--WIZARD-->..<!--ENDWIZARD-->` tags for usage from within the Sentry UI.

3. Merge docs after new version has been released (auto-deploys on merge).
4. (optional) Update data in [`sdk_updates.py`](https://github.com/getsentry/sentry/blob/master/src/sentry/sdk_updates.py) to give users in-app suggestions to use your integration. May not be applicable or doable for all kinds of integrations.
