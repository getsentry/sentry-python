# Contributing to Sentry SDK for Python

We welcome contributions to `sentry-python` by the community.

This file outlines the process to contribute to the SDK itself. For contributing to the documentation, please see the [Contributing to Docs](https://docs.sentry.io/contributing/) page.

## How to Report a Problem

Please search the [issue tracker](https://github.com/getsentry/sentry-python/issues) before creating a new issue (a problem or an improvement request). Please also ask in our [Sentry Community on Discord](https://discord.com/invite/Ww9hbqr) before submitting a new issue. There are a ton of great people in our Discord community ready to help you!


## Submitting Changes

- Fork the `sentry-python` repo and prepare your changes.
- Add tests for your changes to `tests/`.
- Run tests and make sure all of them pass.
- Submit a pull request, referencing any issues your changes address. Please follow our [commit message format](https://develop.sentry.dev/commit-messages/#commit-message-format) when naming your pull request.

We will review your pull request as soon as possible. Thank you for contributing!

## Development Environment

### Set up Python

Make sure that you have Python 3 installed. Version 3.7 or higher is required to run style checkers on pre-commit.

On macOS, we recommend using `brew` to install Python. For Windows, we recommend an official [python.org](https://www.python.org/downloads/) release.

### Fork and Clone the Repo

Before you can contribute, you will need to [fork the `sentry-python` repository](https://github.com/getsentry/sentry-python/fork). Then, clone the forked repository to your local development environment.

### Create a Virtual Environment

To keep your Python development environment and packages separate from the ones
used by your operation system, create a virtual environment:

```bash
cd sentry-python

python -m venv .venv
```

Then, activate your virtual environment with the following command. You will need to repeat this step every time you wish to work on your changes for `sentry-python`.

```bash
source .venv/bin/activate
```

### Install `sentry-python` in Editable Mode

Install `sentry-python` in [editable mode](https://pip.pypa.io/en/latest/topics/local-project-installs/#editable-installs). This will make any changes you make to the SDK code locally immediately effective without you having to reinstall or copy anything.

```bash
pip install -e .
```

**Hint:** Sometimes you need a sample project to run your new changes to `sentry-python`. In this case install the sample project in the same virtualenv and you should be good to go.

### Install Coding Style Pre-commit Hooks

This will make sure that your commits will have the correct coding style.

```bash
cd sentry-python

pip install -r linter-requirements.txt

pip install pre-commit

pre-commit install
```

That's it. You should be ready to make changes, run tests, and make commits! If you experience any problems, please don't hesitate to ping us in our [Discord Community](https://discord.com/invite/Ww9hbqr).

## Running Tests

To run the tests, first setup your development environment according to the instructions above. Then, install the required packages for running tests with the following command:
```bash
pip install -r test-requirements.txt
```

Once the requirements are installed, you can run all tests with the following command:
```bash
pytest tests/
```

If you would like to run the tests for a specific integration, use a command similar to the one below:

```bash
pytest -rs tests/integrations/flask/  # Replace "flask" with the specific integration you wish to test
```

**Hint:** Tests of integrations need additional dependencies. The switch `-rs` will show you why tests were skipped and what dependencies you need to install for the tests to run. (You can also consult the [tox.ini](tox.ini) file to see what dependencies are installed for each integration)

## Adding a New Integration

1. Write the integration.

   - Instrument all application instances by default. Prefer global signals/patches instead of configuring a specific instance. Don't make the user pass anything to your integration for anything to work. Aim for zero configuration.

   - Everybody monkeypatches. That means:

     - Make sure to think about conflicts with other monkeypatches when monkeypatching.

     - You don't need to feel bad about it.

   - Make sure your changes don't break end user contracts. The SDK should never alter the expected behavior of the underlying library or framework from the user's perspective and it shouldn't have any side effects.

   - Avoid modifying the hub, registering a new client or the like. The user drives the client, and the client owns integrations.

   - Allow the user to turn off the integration by changing the client. Check `Hub.current.get_integration(MyIntegration)` from within your signal handlers to see if your integration is still active before you do anything impactful (such as sending an event).

2. Write tests.

   - Consider the minimum versions supported, and test each version in a separate env in `tox.ini`.

   - Create a new folder in `tests/integrations/`, with an `__init__` file that skips the entire suite if the package is not installed.

3. Update package metadata.

   - We use `extras_require` in `setup.py` to communicate minimum version requirements for integrations. People can use this in combination with tools like Poetry or Pipenv to detect conflicts between our supported versions and their used versions programmatically.

     Do not set upper bounds on version requirements as people are often faster in adopting new versions of a web framework than we are in adding them to the test matrix or our package metadata.

4. Write the [docs](https://github.com/getsentry/sentry-docs). Follow the structure of [existing integration docs](https://docs.sentry.io/platforms/python/integrations/). And, please **make sure to add your integration to the table in `python/integrations/index.md`** (people often forget this step 🙂).

5. Merge docs after new version has been released. The docs are built and deployed after each merge, so your changes should go live in a few minutes.

6. (optional, if possible) Update data in [`sdk_updates.py`](https://github.com/getsentry/sentry/blob/master/src/sentry/sdk_updates.py) to give users in-app suggestions to use your integration. This step will only apply to some integrations.

## Releasing a New Version

_(only relevant for Sentry employees)_

### Prerequisites

- All the changes that should be released must be on the `master` branch.
- Every commit should follow the [Commit Message Format](https://develop.sentry.dev/commit-messages/#commit-message-format) convention.
- CHANGELOG.md is updated automatically. No human intervention is necessary, but you might want to consider polishing the changelog by hand to make it more user friendly by grouping related things together, adding small code snippets and links to docs, etc.

### Manual Process

- On GitHub in the `sentry-python` repository, go to "Actions" and select the "Release" workflow.
- Click on "Run workflow" on the right side, and make sure the `master` branch is selected.
- Set the "Version to release" input field. Here you decide if it is a major, minor or patch release. (See "Versioning Policy" below)
- Click "Run Workflow".

This will trigger [Craft](https://github.com/getsentry/craft) to prepare everything needed for a release. (For more information, see [craft prepare](https://github.com/getsentry/craft#craft-prepare-preparing-a-new-release).) At the end of this process a release issue is created in the [Publish](https://github.com/getsentry/publish) repository. (Example release issue: https://github.com/getsentry/publish/issues/815)

Now one of the persons with release privileges (most probably your engineering manager) will review this issue and then add the `accepted` label to the issue.

There are always two persons involved in a release.

If you are in a hurry and the release should be out immediately, there is a Slack channel called `#proj-release-approval` where you can see your release issue and where you can ping people to please have a look immediately.

When the release issue is labeled `accepted`, [Craft](https://github.com/getsentry/craft) is triggered again to publish the release to all the right platforms. (See [craft publish](https://github.com/getsentry/craft#craft-publish-publishing-the-release) for more information.) At the end of this process the release issue on GitHub will be closed and the release is completed! Congratulations!

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
