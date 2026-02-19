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

The tox CLI tool is required to run the tests locally. Follow [the installation instructions](https://tox.wiki/en/latest/installation.html) for tox. Dependencies are installed for you when you run the command below, but _you_ need to bring an appropriate Python interpreter.

[Pyenv](https://github.com/pyenv/pyenv) is a cross-platform utility for managing Python versions. You can also use a conventional package manager, but not all versions may be distributed in the package manager of your choice. For macOS, versions 3.8 and up can be installed with Homebrew.

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

### SDK Contract

The SDK runs as part of users' applications. Users do not expect their application to crash, the SDK to mutate object references, the SDK to swallow their exceptions, leaked file descriptors to eat their memory, SDK-initiated database requests, or the SDK to alter the signature of functions or coroutines.

So this means you should write the ugly code in the library to work around this?
Well, there's another consequence of running on thousands of applications. Maintenance burden is higher than for application code, because all code paths of the SDK are hit across the enormous variety of applications the SDK finds itself in. The diversity includes different CPython versions, permutations of package versions, and operating systems.
And once something you write is out there, you cannot change or remove it from the SDK without good reason (https://develop.sentry.dev/sdk/processes/breaking_changes/#introducing-breaking-changes).

What's the concrete advice when writing a new integration?

### Requirements

1. Are you supporting a product feature? Then ensure that product expectations are clearly documented in the [develop docs](https://develop.sentry.dev/sdk/). Requirements for a given Insight Module must be available under https://develop.sentry.dev/sdk/telemetry/traces/modules/.

2. Confirm that all span, breadcrumb, log or metric attributes are defined in https://github.com/getsentry/sentry-conventions.

3. Ensure that the **semantics** of the attribute are clear. If the attribute is not uniquely defined, do not add it.
- For instance, do not attach a request model to an agent invocation span. On the other hand, a default request model can be well-defined.

### Code

0. Document why you're patching or hooking into something.
  - Even if it's just to say that you're creating and managing a certain type of span in the patch, that's valuable.
  - It should be clear what span is created and exited where and which patches apply which attributes on a given span.

1. Do you even need to add this attribute on this span?
  - Be intentional with supporting product features. Only add what's necessary, or **be very sure that your addition provides value**. Decisions about what data lives on what types of spans are hard to undo, and limits future design space.

2. Avoid setting arbitrary objects.
  - In line with the point above, prefer using an include-list of valuable entries when setting a dictionary attribute. Otherwise, tests will break again and again.

3. Instrument all application instances by default. Prefer global signals/patches.
  - Patching instances is just harder. Your patches may unexpectedly not apply to some instances, or unexpectedly be applied multiple times.

4. Don't make the user pass anything to your integration for anything to work. Aim for zero configuration.
  - Users tend to only consult the documentation when something goes wrong. So the default values for integration options must lead to the best out-of-the-box experience for the majority of users.

5. Re-use code, but only when it makes sense.
  - Think about future evolution of the library and your integration.
  - If you're patching two internal methods that are similar but will diverge with time, don't force a common patch.
  - If the shared SDK logic will diverge for two patches, just don't force them through a common path in the first place.
  - If your shared code path has a bunch of conditionals today or will have a ton of conditionals in the future, that's the sign to not stick to DRY.

6. Be explicit.
  - You're developing against a library, and that library uses specific types.
  - If you use `hasattr()` or `getattr()` to gate logic, you must verify the code path for all types that have this attribute (and Python has duck typing).
  - If you use `type().__name__` to gate logic, you must verify the behavior for all types with a given name (and Python has duck typing).
  - So just use `isinstance()`.

7. Heuristics will bite you later.
  - If something you write is best-effort, make sure there are no better alternatives.

8. Obsess about the unhappy path.
  - Users are interested in seeing what went wrong when something doesn't work. The code in the `except` block should not be an afterthought.
  - Let exceptions bubble-up as far as possible when reporting unhandled exceptions.
  - Preserve the user's original exception. Python chains exceptions when code in a `except` block throws, so if a `except` block in the SDK throws, the SDK exception takes the foreground ([#5188](https://github.com/getsentry/sentry-python/issues/5188)).
  - Please don't report exceptions that are caught further up in the library's call chain as unhandled.

9. Make sure your changes don't break end user contracts. The SDK should never alter the expected behavior of the underlying library or framework from the user's perspective and it shouldn't have any side effects.
  - For example, it shouldn't open new connections or evaluate lazy queries early.

10. Be defensive, but don't add dead code.
  - Don't assume the code you're patching will stay the same forever, especially if it's an internal function. Allow for future variability whenever it makes sense.
  - Dead code adds cognitive overhead when reasoning about code, so don't account for impossible scenarios.

11. Write tests, but don't write mocks.
  - Aim for end-to-end tests, not unit tests.
  - Don't call private SDK stuff directly, just use the patched library in a way that triggers the patch.
  - Mocks are _very expensive_ to maintain, particularly when testing patches for fast-moving libraries.
  - Consider the minimum versions supported, and document in `_MIN_VERSIONS` in `integrations/__init__.py`.
  - Create a new folder in `tests/integrations/`, with an `__init__` file that skips the entire suite if the package is not installed.
  - Add the test suite to the script generating our test matrix. See [`scripts/populate_tox/README.md`](https://github.com/getsentry/sentry-python/blob/master/scripts/populate_tox/README.md#add-a-new-test-suite).

12. Be careful patching decorators
  - Does the library's decorator apply to sync or async functions?
  - Some decorators can be applied to classes and functions, and both with and without arguments. Make sure you handle and test all applicable cases.

13. Avoid registering a new client or the like. The user drives the client, and the client owns integrations.

14. Allow the user to turn off the integration by changing the client. Check `sentry_sdk.get_client().get_integration(MyIntegration)` from within your signal handlers or patches to see if your integration is still active before you do anything impactful (such as sending an event). If it's not active, the patch my be no-op.

### Document

1. Write the [docs](https://github.com/getsentry/sentry-docs). Follow the structure of [existing integration docs](https://docs.sentry.io/platforms/python/integrations/). And, please **make sure to add your integration to the table in `python/integrations/index.md`** (people often forget this step 🙂).

2. Merge docs after new version has been released. The docs are built and deployed after each merge, so your changes should go live in a few minutes.


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
