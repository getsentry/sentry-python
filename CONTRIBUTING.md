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
used by your operation system, create a [virtual environment](https://docs.python.org/3/tutorial/venv.html):

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

pip install -r requirements-devenv.txt

pip install pre-commit

pre-commit install
```

That's it. You should be ready to make changes, run tests, and make commits! If you experience any problems, please don't hesitate to ping us in our [Discord Community](https://discord.com/invite/Ww9hbqr).

## Running Tests

We test against a number of Python language and library versions, which are automatically generated and stored in the [tox.ini](tox.ini) file. The `envlist` defines the environments you can choose from when running tests, and correspond to package versions and environment variables. The `TESTPATH` environment variable, in turn, determines which tests are run.

The tox CLI tool is required to run the tests locally. Follow [the installation instructions](https://tox.wiki/en/latest/installation.html) for tox. Dependencies are installed for you when you run the command below, but _you_ need to bring an appropriate Python interpreter. Versions 3.8 and up can be installed with brew.

An environment consists of the Python major and minor version and the library name and version. The exception to the rule is that you can provide `common` instead of the library information. The environments tied to a specific library usually run the corresponding test suite, while `common` targets all tests but skips those that require uninstalled dependencies.

To run Celery tests for version v5.5.3 of its Python library using a 3.12 interpreter, use

```bash
tox -p auto -o -e py3.12-celery-v5.5.3
```

or to use the `common` environment, run

```bash
tox -p auto -o -e py3.12-common
```

To select specific tests, you can forward arguments to `pytest` like so

```bash
tox -p auto -o -e py3.12-celery-v5.5.3 -- -k test_transaction_events
```

In general, you use

```bash
tox -p auto -o -e <tox_env> -- <pytest_args>
```

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

_(only relevant for Python SDK core team)_

### Prerequisites

- All the changes that should be released must be on the `master` branch.
- Every commit should follow the [Commit Message Format](https://develop.sentry.dev/commit-messages/#commit-message-format) convention.
- CHANGELOG.md is updated automatically. No human intervention is necessary, but you might want to consider polishing the changelog by hand to make it more user friendly by grouping related things together, adding small code snippets and links to docs, etc.

### Manual Process

- On GitHub in the `sentry-python` repository, go to "Actions" and select the "Release" workflow.
- Click on "Run workflow" on the right side, and make sure the `master` branch is selected.
- Set the "Version to release" input field. Here you decide if it is a major, minor or patch release (see "Versioning Policy" below).
- Click "Run Workflow".

This will trigger [Craft](https://github.com/getsentry/craft) to prepare everything needed for a release. (For more information, see [craft prepare](https://github.com/getsentry/craft#craft-prepare-preparing-a-new-release).) At the end of this process a release issue is created in the [Publish](https://github.com/getsentry/publish) repository (example issue: https://github.com/getsentry/publish/issues/815).

At the same time, the action will create a release branch in the `sentry-python` repository called `release/<version>`. You may want to check out this branch and polish the auto-generated `CHANGELOG.md` before proceeding by including code snippets, descriptions, reordering and reformatting entries, in order to make the changelog as useful and actionable to users as possible.

CI must be passing on the release branch; if there's any failure, Craft will not create a release.

Once the release branch is ready and green, notify your team (or your manager). They will need to add the `accepted` label to the issue in the `publish` repo. There are always two people involved in a release. Do not accept your own releases.

When the release issue is labeled `accepted`, [Craft](https://github.com/getsentry/craft) is triggered again to publish the release to all the right platforms. See [craft publish](https://github.com/getsentry/craft#craft-publish-publishing-the-release) for more information. At the end of this process, the release issue on GitHub will be closed and the release is completed! Congratulations!

There is a sequence diagram visualizing all this in the [README.md](https://github.com/getsentry/publish) of the `Publish` repository.

### Versioning Policy

This project follows [semver](https://semver.org/), with three additions:

- Semver says that major version `0` can include breaking changes at any time. Still, it is common practice to assume that only `0.x` releases (minor versions) can contain breaking changes while `0.x.y` releases (patch versions) are used for backwards-compatible changes (bugfixes and features). This project also follows that practice.

- All undocumented APIs are considered internal. They are not part of this contract.

- Certain features (e.g. integrations) may be explicitly called out as "experimental" or "unstable" in the documentation. They come with their own versioning policy described in the documentation.

We recommend to pin your version requirements against `2.x.*` or `2.x.y`.
Either one of the following is fine:

```
sentry-sdk>=2.0.0,<3.0.0
sentry-sdk==2.4.0
```

A major release `N` implies the previous release `N-1` will no longer receive updates. We generally do not backport bugfixes to older versions unless they are security relevant. However, feel free to ask for backports of specific commits on the bugtracker.


## Contributing to Sentry AWS Lambda Layer

### Development environment

You need to have an AWS account and AWS CLI installed and setup.

We put together two helper functions that can help you with development:

- `./scripts/aws/aws-deploy-local-layer.sh`

  This script [scripts/aws/aws-deploy-local-layer.sh](scripts/aws/aws-deploy-local-layer.sh) will take the code you have checked out locally, create a Lambda layer out of it and deploy it to the `eu-central-1` region of your configured AWS account using `aws` CLI.

  The Lambda layer will have the name `SentryPythonServerlessSDK-local-dev`

- `./scripts/aws/aws-attach-layer-to-lambda-function.sh`

  You can use this script [scripts/aws/aws-attach-layer-to-lambda-function.sh](scripts/aws/aws-attach-layer-to-lambda-function.sh) to attach the Lambda layer you just deployed (using the first script) onto one of your existing Lambda functions. You will have to give the name of the Lambda function to attach onto as an argument. (See the script for details.)

With these two helper scripts it should be easy to rapidly iterate your development on the Lambda layer.
