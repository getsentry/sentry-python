# Contributing to Sentry SDK for Python

We welcome contributions to python-sentry by the community. See the [Contributing to Docs](https://docs.sentry.io/contributing/) page if you want to fix or update the documentation on the website.

## How to report a problem

Please search the [issue tracker](https://github.com/getsentry/sentry-python/issues) before creating a new issue (a problem or an improvement request). Please also ask in our [Sentry Community on Discord](https://discord.com/invite/Ww9hbqr) before submitting a new issue. There is a ton of great people in our Discord community ready to help you!

If you feel that you can fix or implement it yourself, please read a few paragraphs below to learn how to submit your changes.

## Submitting changes

- Setup the development environment.
- Clone sentry-python and prepare necessary changes.
- Add tests for your changes to `tests/`.
- Run tests and make sure all of them pass.
- Submit a pull request, referencing any issues it addresses.

We will review your pull request as soon as possible.
Thank you for contributing!

## Development environment

### Clone the repo:

```bash
git clone git@github.com:getsentry/sentry-python.git
```

Make sure that you have Python 3 installed. Version 3.7 or higher is required to run style checkers on pre-commit. On macOS, we recommend using brew to install Python. For Windows, we recommend an official python.org release.

### Create a virtual environment:

```bash
cd sentry-python

python -m venv .env

source .env/bin/activate
```

### Install `sentry-python` in editable mode

```bash
pip install -e .
```

**Hint:** Sometimes you need a sample project to run your new changes to sentry-python. In this case install the sample project in the same virtualenv and you should be good to go because the ` pip install -e .` from above installed your local sentry-python in editable mode.

### Install coding style pre-commit hooks:

This will make sure that your commits will have the correct coding style.

```bash
cd sentry-python

pip install -r linter-requirements.txt

pip install pre-commit

pre-commit install
```

That's it. You should be ready to make changes, run tests, and make commits! If you experience any problems, please don't hesitate to ping us in our [Discord Community](https://discord.com/invite/Ww9hbqr).

## Running tests

We have a `Makefile` to help people get started with hacking on the SDK
without having to know or understand the Python ecosystem.
Run `make` or `make help` to list commands.

So the simplest way to run tests is:

```bash
cd sentry-python

make test
```

This will use [Tox](https://tox.wiki/en/latest/) to run our whole test suite
under Python 2.7 and Python 3.7.

Of course you can always run the underlying commands yourself, which is
particularly useful when wanting to provide arguments to `pytest` to run
specific tests:

```bash
cd sentry-python

# create virtual environment
python -m venv .env

# activate virtual environment
source .env/bin/activate

# install sentry-python
pip install -e .

# install requirements
pip install -r test-requirements.txt

# run tests
pytest tests/
```

If you want to run the tests for a specific integration you should do so by doing this:

```bash
pytest -rs tests/integrations/flask/
```

**Hint:** Tests of integrations need additional dependencies. The switch `-rs` will show you why tests where skipped and what dependencies you need to install for the tests to run. (You can also consult the [tox.ini](tox.ini) file to see what dependencies are installed for each integration)

## Releasing a new version

(only relevant for Sentry employees)

Prerequisites:

- All the changes that should be release must be in `master` branch.
- Every commit should follow the [Commit Message Format](https://develop.sentry.dev/commit-messages/#commit-message-format) convention.
- CHANGELOG.md is updated automatically. No human intervention necessary.

Manual Process:

- On GitHub in the `sentry-python` repository go to "Actions" select the "Release" workflow.
- Click on "Run workflow" on the right side, make sure the `master` branch is selected.
- Set "Version to release" input field. Here you decide if it is a major, minor or patch release. (See "Versioning Policy" below)
- Click "Run Workflow"

This will trigger [Craft](https://github.com/getsentry/craft) to prepare everything needed for a release. (For more information see [craft prepare](https://github.com/getsentry/craft#craft-prepare-preparing-a-new-release)) At the end of this process a release issue is created in the [Publish](https://github.com/getsentry/publish) repository. (Example release issue: https://github.com/getsentry/publish/issues/815)

Now one of the persons with release privileges (most probably your engineering manager) will review this Issue and then add the `accepted` label to the issue.

There are always two persons involved in a release.

If you are in a hurry and the release should be out immediatly there is a Slack channel called `#proj-release-approval` where you can see your release issue and where you can ping people to please have a look immediatly.

When the release issue is labeled `accepted` [Craft](https://github.com/getsentry/craft) is triggered again to publish the release to all the right platforms. (See [craft publish](https://github.com/getsentry/craft#craft-publish-publishing-the-release) for more information). At the end of this process the release issue on GitHub will be closed and the release is completed! Congratulations!

There is a sequence diagram visualizing all this in the [README.md](https://github.com/getsentry/publish) of the `Publish` repository.

### Versioning Policy

This project follows [semver](https://semver.org/), with three additions:

- Semver says that major version `0` can include breaking changes at any time. Still, it is common practice to assume that only `0.x` releases (minor versions) can contain breaking changes while `0.x.y` releases (patch versions) are used for backwards-compatible changes (bugfixes and features). This project also follows that practice.

- All undocumented APIs are considered internal. They are not part of this contract.

- Certain features (e.g. integrations) may be explicitly called out as "experimental" or "unstable" in the documentation. They come with their own versioning policy described in the documentation.

We recommend to pin your version requirements against `1.x.*` or `1.x.y`.
Either one of the following is fine:

```
sentry-sdk>=1.0.0,<2.0.0
sentry-sdk==1.5.0
```

A major release `N` implies the previous release `N-1` will no longer receive updates. We generally do not backport bugfixes to older versions unless they are security relevant. However, feel free to ask for backports of specific commits on the bugtracker.

## Adding a new integration (checklist)

1. Write the integration.

   - Instrument all application instances by default. Prefer global signals/patches instead of configuring a specific instance. Don't make the user pass anything to your integration for anything to work. Aim for zero configuration.

   - Everybody monkeypatches. That means:

     - Make sure to think about conflicts with other monkeypatches when monkeypatching.

     - You don't need to feel bad about it.

   - Avoid modifying the hub, registering a new client or the like. The user drives the client, and the client owns integrations.

   - Allow the user to disable the integration by changing the client. Check `Hub.current.get_integration(MyIntegration)` from within your signal handlers to see if your integration is still active before you do anything impactful (such as sending an event).

2. Write tests.

   - Think about the minimum versions supported, and test each version in a separate env in `tox.ini`.

   - Create a new folder in `tests/integrations/`, with an `__init__` file that skips the entire suite if the package is not installed.

3. Update package metadata.

   - We use `extras_require` in `setup.py` to communicate minimum version requirements for integrations. People can use this in combination with tools like Poetry or Pipenv to detect conflicts between our supported versions and their used versions programmatically.

     Do not set upper-bounds on version requirements as people are often faster in adopting new versions of a web framework than we are in adding them to the test matrix or our package metadata.

4. Write the [docs](https://github.com/getsentry/sentry-docs). Answer the following questions:

   - What does your integration do? Split in two sections: Executive summary at top and exact behavior further down.

   - Which version of the SDK supports which versions of the modules it hooks into?

   - One code example with basic setup.

   - Make sure to add integration page to `python/index.md` (people forget to do that all the time).

Tip: Put most relevant parts wrapped in `<!--WIZARD-->..<!--ENDWIZARD-->` tags for usage from within the Sentry UI.

5. Merge docs after new version has been released (auto-deploys on merge).

6. (optional) Update data in [`sdk_updates.py`](https://github.com/getsentry/sentry/blob/master/src/sentry/sdk_updates.py) to give users in-app suggestions to use your integration. May not be applicable or doable for all kinds of integrations.

## Commit message format guidelines

See the documentation on commit messages here:

https://develop.sentry.dev/commit-messages/#commit-message-format
