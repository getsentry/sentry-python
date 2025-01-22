# Populate Tox

We integrate with a number of frameworks and libraries and have a test suite for
each. The tests run against different versions of the framework/library to make
sure we support everything we claim to.

This `populate_tox.py` script is responsible for picking reasonable versions to
test automatically and generating parts of `tox.ini` to capture this.

## How it works

There is a template in this directory called `tox.jinja` which contains a
combination of hardcoded and generated entries.

The `populate_tox.py` script fills out the auto-generated part of that template.
It does this by querying PyPI for each framework's package and its metadata and
then determining which versions make sense to test to get good coverage.

The lowest supported and latest version of a framework are always tested, with
a number of releases in between:
- If the package has majors, we pick the highest version of each major. For the
  latest major, we also pick the lowest version in that major.
- If the package doesn't have multiple majors, we pick two versions in between
  lowest and highest.

#### Caveats

- Make sure the integration name is the same everywhere. If it consists of
  multiple words, use an underscore instead of a hyphen.

## Defining constraints

The `TEST_SUITE_CONFIG` dictionary defines, for each integration test suite,
the main package (framework, library) to test with; any additional test
dependencies, optionally gated behind specific conditions; and optionally
the Python versions to test on.

The format is:

```
integration_name: {
    "package": name_of_main_package_on_pypi,
    "deps": {
         rule1: [package1, package2, ...],
         rule2: [package3, package4, ...],
     },
     "python": python_version_specifier,
}
```

The following can be set as a rule:
  - `*`: packages will be always installed
  - a version specifier on the main package (e.g. `<=0.32`): packages will only
    be installed if the main package falls into the version bounds specified
  - specific Python version(s) in the form `py3.8,py3.9`: packages will only be
    installed if the Python version matches one from the list

Rules can be used to specify version bounds on older versions of the main
package's dependencies, for example. If e.g. Flask tests generally need
Werkzeug and don't care about its version, but Flask older than 3.0 needs
a specific Werkzeug version to work, you can say:

```
"flask": {
    "deps": {
        "*": ["Werkzeug"],
        "<3.0": ["Werkzeug<2.1.0"],
    }
}
```

Sometimes, things depend on the Python version installed. If the integration
test should only run on specific Python version, e.g. if you want AIOHTTP
tests to only run on Python 3.7+, you can say:

```
"aiohttp": {
    ...
    "python": ">=3.7",
}
```

If, on the other hand, you need to install a specific version of a secondary
dependency on specific Python versions (so the test suite should still run on
said Python versions, just with different dependency-of-a-dependency bounds),
you can say:

```
"celery": {
    ...
    "deps": {
        "*": ["newrelic", "redis"],
        "py3.7": ["importlib-metadata<5.0"],
    },
},
```

## How-Tos

### Add a new test suite

1. Add the minimum supported version of the framework/library to `_MIN_VERSIONS`
   in `integrations/__init__.py`. This should be the lowest version of the
   framework that we can guarantee works with the SDK. If you've just added the
   integration, you should generally set this to the latest version of the framework
   at the time.
2. Add the integration and any constraints to `TEST_SUITE_CONFIG`. See the
   "Defining constraints" section for the format.
3. Add the integration to one of the groups in the `GROUPS` dictionary in
   `scripts/split_tox_gh_actions/split_tox_gh_actions.py`.
4. Add the `TESTPATH` for the test suite in `tox.jinja`'s `setenv` section.
5. Run `scripts/generate-test-files.sh` and commit the changes.

### Migrate a test suite to populate_tox.py

A handful of integration test suites are still hardcoded. The goal is to migrate
them all to `populate_tox.py` over time.

1. Remove the integration from the `IGNORE` list in `populate_tox.py`.
2. Remove the hardcoded entries for the integration from the `envlist` and `deps` sections of `tox.jinja`.
2. Run `scripts/generate-test-files.sh`.
3. Run the test suite, either locally or by creating a PR.
4. Address any test failures that happen.

You might have to introduce additional version bounds on the dependencies of the
package. Try to determine the source of the failure and address it.

Common scenarios:
- An old version of the tested package installs a dependency without defining
  an upper version bound on it. A new version of the dependency is installed that
  is incompatible with the package. In this case you need to determine which
  versions of the dependency don't contain the breaking change and restrict this
  in `TEST_SUITE_CONFIG`.
- Tests are failing on an old Python version. In this case first double-check
  whether we were even testing them on that version in the original `tox.ini`.
