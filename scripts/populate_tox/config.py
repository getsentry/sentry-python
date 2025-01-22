# The TEST_SUITE_CONFIG dictionary defines, for each integration test suite,
# the main package (framework, library) to test with; any additional test
# dependencies, optionally gated behind specific conditions; and optionally
# the Python versions to test on.
#
# See scripts/populate_tox/README.md for more info on the format and examples.

TEST_SUITE_CONFIG = {
    "celery": {
        "package": "celery",
        "deps": {
            "*": ["newrelic", "redis"],
            "py3.7": ["importlib-metadata<5.0"],
        },
    },
    "dramatiq": {
        "package": "dramatiq",
    },
    "huey": {
        "package": "huey",
    },
    "spark": {
        "package": "pyspark",
        "python": ">=3.8",
    },
}
