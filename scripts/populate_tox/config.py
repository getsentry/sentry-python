# The TEST_SUITE_CONFIG dictionary defines, for each integration test suite,
# the main package (framework, library) to test with; any additional test
# dependencies, optionally gated behind specific conditions; and optionally
# the Python versions to test on.
#
# See scripts/populate_tox/README.md for more info on the format and examples.

TEST_SUITE_CONFIG = {
    "ariadne": {
        "package": "ariadne",
        "deps": {
            "*": ["fastapi", "flask", "httpx"],
        },
        "python": ">=3.8",
    },
    "clickhouse_driver": {
        "package": "clickhouse-driver",
    },
    "gql": {
        "package": "gql[all]",
    },
    "graphene": {
        "package": "graphene",
        "deps": {
            "*": ["blinker", "fastapi", "flask", "httpx"],
            "py3.6": ["aiocontextvars"],
        },
    },
    "launchdarkly": {
        "package": "launchdarkly-server-sdk",
    },
    "loguru": {
        "package": "loguru",
    },
    "openfeature": {
        "package": "openfeature-sdk",
    },
    "pymongo": {
        "package": "pymongo",
        "deps": {
            "*": ["mockupdb"],
        },
    },
    "redis_py_cluster_legacy": {
        "package": "redis-py-cluster",
    },
    "sqlalchemy": {
        "package": "sqlalchemy",
    },
    "statsig": {
        "package": "statsig",
        "deps": {
            "*": ["typing_extensions"],
        },
    },
    "strawberry": {
        "package": "strawberry-graphql[fastapi,flask]",
        "deps": {
            "*": ["httpx"],
        },
    },
    "trytond": {
        "package": "trytond",
        "deps": {
            "*": ["werkzeug"],
            "<=5.0": ["werkzeug<1.0"],
        },
    },
    "typer": {
        "package": "typer",
    },
    "unleash": {
        "package": "UnleashClient",
    },
}
