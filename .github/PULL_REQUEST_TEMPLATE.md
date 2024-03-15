<!-- Describe your PR here -->

---

## General Notes

Thank you for contributing to `sentry-python`!

Please add tests to validate your changes, and lint your code using `tox -e linters`.

Running the test suite on your PR might require maintainer approval. Some tests (AWS Lambda) additionally require a maintainer to add a special label to run and will fail if the label is not present.

#### For maintainers

Sensitive test suites require maintainer review to ensure that tests do not compromise our secrets. This review must be repeated after any code revisions.

Before running sensitive test suites, please carefully check the PR. Then, apply the `Trigger: tests using secrets` label. The label will be removed after any code changes to enforce our policy requiring maintainers to review all code revisions before running sensitive tests.
