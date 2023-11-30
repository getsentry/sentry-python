<!-- What problem does this PR solve, what feature does it add? -->

---

## General Notes

Thank you for contributing to `sentry-python`!

Please make sure to include tests for your changes and run your code through linters (`tox -e linters`).

If this is your first `sentry-python` pull request, running the test suite will require maintainer approval. Some tests (AWS Lambda) additionally require a special label to run and will always fail if the label is not present.

---

For maintainers: Carefully check the PR and then apply the `Trigger: tests using secrets` to run sensitive test suites.
