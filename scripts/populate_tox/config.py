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
    "bottle": {
        "package": "bottle",
        "deps": {
            "*": ["werkzeug<2.1.0"],
        },
    },
    "falcon": {
        "package": "falcon",
        "python": "<3.13",
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
    "pyramid": {
        "package": "pyramid",
        "deps": {
            "*": ["werkzeug<2.1.0"],
        },
    },
    "starlite": {
        "package": "starlite",
        "deps": {
            "*": [
                "pytest-asyncio",
                "python-multipart",
                "requests",
                "cryptography",
                "pydantic<2.0.0",
                "httpx<0.28",
            ],
        },
        "python": "<=3.11",
    },
    "strawberry": {
        "package": "strawberry-graphql[fastapi,flask]",
        "deps": {
            "*": ["httpx"],
        },
    },
    "tornado": {
        "package": "tornado",
        "deps": {
            "*": ["pytest"],
            "<=6.4.1": [
                "pytest<8.2"
            ],  # https://github.com/tornadoweb/tornado/pull/3382
            "py3.6": ["aiocontextvars"],
        },
    },
}
