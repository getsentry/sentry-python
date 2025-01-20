# The TEST_SUITE_CONFIG dictionary defines, for each integration test suite,
# the main package (framework, library) to test with; any additional test
# dependencies, optionally gated behind specific conditions; and optionally
# the Python versions to test on.
#
# See scripts/populate_tox/README.md for more info on the format and examples.

TEST_SUITE_CONFIG = {
    "aiohttp": {
        "package": "aiohttp",
        "deps": {"*": ["pytest-aiohttp", "pytest-asyncio"]},
        "python": ">=3.7",
    },
    "anthropic": {
        "package": "anthropic",
        "deps": {
            "*": ["pytest-asyncio"],
            "<=0.32": ["httpx<0.28.0"],
        },
        "python": ">=3.7",
    },
    "ariadne": {
        "package": "ariadne",
        "deps": {
            "*": ["fastapi", "flask", "httpx"],
        },
        "python": ">=3.8",
    },
    "arq": {
        "package": "arq",
        "deps": {
            "*": ["fakeredis>=2.2.0,<2.8", "pytest-asyncio", "async-timeout"],
            "<=0.25": ["pydantic<2"],
        },
        "python": ">=3.7",
    },
    "asyncpg": {
        "package": "asyncpg",
        "deps": {
            "*": ["pytest-asyncio"],
        },
        "python": ">=3.7",
    },
    "beam": {
        "package": "apache-beam",
        "deps": {
            "*": [],
        },
        "python": ">=3.7",
    },
    "boto3": {
        "package": "boto3",
        "deps": {
            "*": [],
        },
    },
    "bottle": {
        "package": "bottle",
        "deps": {
            "*": ["werkzeug<2.1.0"],
        },
    },
    "celery": {
        "package": "celery",
        "deps": {
            "*": ["newrelic", "redis"],
            "py3.7": ["importlib-metadata<5.0"],
        },
    },
    "chalice": {
        "package": "chalice",
        "deps": {
            "*": ["pytest-chalice==0.0.5"],
        },
    },
    "clickhouse_driver": {
        "package": "clickhouse-driver",
        "deps": {
            "*": [],
        },
    },
    "cohere": {
        "package": "cohere",
        "deps": {
            "*": ["httpx"],
        },
    },
    "django": {
        "package": "django",
        "deps": {
            "*": [
                "psycopg2-binary",
                "werkzeug",
            ],
            ">=2.0,<3.0": ["six"],
            "<=3.2": [
                "werkzeug<2.1.0",
                "djangorestframework>=3.0.0,<4.0.0",
                "pytest-django",
            ],
            ">=2.0": ["channels[daphne]"],
            "<=3.0": ["pytest-django<4.0"],
            ">=4.0": ["djangorestframework", "pytest-asyncio"],
        },
    },
    "dramatiq": {
        "package": "dramatiq",
        "deps": {},
    },
    "falcon": {
        "package": "falcon",
        "deps": {},
        "python": "<3.13",
    },
    "fastapi": {
        "package": "fastapi",
        "deps": {
            "*": [
                "httpx",
                "anyio<4.0.0",
                "python-multipart",
                "pytest-asyncio",
                "requests",
            ]
        },
        "python": ">=3.7",
    },
    "flask": {
        "package": "flask",
        "deps": {
            "*": ["flask-login", "werkzeug"],
            "<2.0": ["werkzeug<2.1.0", "markupsafe<2.1.0"],
        },
    },
    "gql": {
        "package": "gql[all]",
        "deps": {},
    },
    "graphene": {
        "package": "graphene",
        "deps": {
            "*": ["blinker", "fastapi", "flask", "httpx"],
            "py3.6": ["aiocontextvars"],
        },
    },
    "grpc": {
        "package": "grpcio",
        "deps": {
            "*": ["protobuf", "mypy-protobuf", "types-protobuf", "pytest-asyncio"],
        },
        "python": ">=3.7",
    },
    "httpx": {
        "package": "httpx",
        "deps": {
            "*": ["anyio<4.0.0", "pytest-httpx"],
            "==0.16": ["pytest-httpx==0.10.0"],
            "==0.18": ["pytest-httpx==0.12.0"],
            "==0.20": ["pytest-httpx==0.14.0"],
            "==0.22": ["pytest-httpx==0.19.0"],
            "==0.23": ["pytest-httpx==0.21.0"],
            "==0.24": ["pytest-httpx==0.22.0"],
            "==0.25": ["pytest-httpx==0.25.0"],
        },
    },
    "huey": {
        "package": "huey",
        "deps": {
            "*": [],
        },
    },
    "huggingface_hub": {
        "package": "huggingface_hub",
        "deps": {"*": []},
    },
    "langchain": {
        "package": "langchain",
        "deps": {
            "*": ["openai", "tiktoken", "httpx"],
            ">=0.3": ["langchain-community"],
        },
    },
    "langchain_notiktoken": {
        "package": "langchain",
        "deps": {
            "*": ["openai", "httpx"],
            ">=0.3": ["langchain-community"],
        },
    },
    "litestar": {
        "package": "litestar",
        "deps": {
            "*": ["pytest-asyncio", "python-multipart", "requests", "cryptography"],
            "<=2.6": ["httpx<0.28"],
        },
    },
    "loguru": {
        "package": "loguru",
        "deps": {
            "*": [],
        },
    },
    # XXX
    # openai-latest: tiktoken~=0.6.0
    "openai": {
        "package": "openai",
        "deps": {
            "*": ["pytest-asyncio", "tiktoken", "httpx"],
            "<=1.22": ["httpx<0.28.0"],
        },
    },
    "openai_notiktoken": {
        "package": "openai",
        "deps": {
            "*": ["pytest-asyncio", "httpx"],
            "<=1.22": ["httpx<0.28.0"],
        },
    },
    "openfeature": {
        "package": "openfeature-sdk",
        "deps": {
            "*": [],
        },
    },
    "launchdarkly": {
        "package": "launchdarkly-server-sdk",
        "deps": {
            "*": [],
        },
    },
    "opentelemetry": {
        "package": "opentelemetry-distro",
        "deps": {
            "*": [],
        },
    },
    "pure_eval": {
        "package": "pure_eval",
        "deps": {
            "*": [],
        },
    },
    "pymongo": {
        "package": "pymongo",
        "deps": {
            "*": ["mockupdb"],
        },
    },
    "pyramid": {
        "package": "pyramid",
        "deps": {
            "*": ["werkzeug<2.1.0"],
        },
    },
    "quart": {
        "package": "quart",
        "deps": {
            "*": [
                "quart-auth",
                "pytest-asyncio",
                "werkzeug",
            ],
            "<=0.19": [
                "blinker<1.6",
                "jinja2<3.1.0",
                "Werkzeug<2.1.0",
                "hypercorn<0.15.0",
            ],
            "py3.8": ["taskgroup==0.0.0a4"],
        },
    },
    "ray": {
        "package": "ray",
        "deps": {},
    },
    "redis": {
        "package": "redis",
        "deps": {
            "*": ["fakeredis!=1.7.4", "pytest<8.0.0", "pytest-asyncio"],
            "py3.6,py3.7": [
                "fakeredis!=2.26.0"
            ],  # https://github.com/cunla/fakeredis-py/issues/341
        },
    },
    "redis_py_cluster_legacy": {
        "package": "redis-py-cluster",
        "deps": {},
    },
    "requests": {
        "package": "requests",
        "deps": {},
    },
    "rq": {
        "package": "rq",
        "deps": {
            "*": ["fakeredis"],
            "<0.13": [
                "fakeredis<1.0",
                "redis<3.2.2",
            ],  # https://github.com/jamesls/fakeredis/issues/245
            ">=0.13,<=1.10": ["fakeredis>=1.0,<1.7.4"],
            "py3.6,py3.7": [
                "fakeredis!=2.26.0"
            ],  # https://github.com/cunla/fakeredis-py/issues/341
        },
    },
    "sanic": {
        "package": "sanic",
        "deps": {
            "*": ["websockets<11.0", "aiohttp", "sanic_testing"],
            ">=22.0": ["sanic_testing"],
            "py3.6": ["aiocontextvars==0.2.1"],
        },
    },
    "spark": {
        "package": "pyspark",
        "deps": {},
        "python": ">=3.8",
    },
    "starlette": {
        "package": "starlette",
        "deps": {
            "*": [
                "pytest-asyncio",
                "python-multipart",
                "requests",
                "anyio<4.0.0",
                "jinja2",
                "httpx",
            ],
            "<=0.36": ["httpx<0.28.0"],
            "<0.15": ["jinja2<3.1"],
            "py3.6": ["aiocontextvars"],
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
    "sqlalchemy": {
        "package": "sqlalchemy",
        "deps": {},
    },
    "strawberry": {
        "package": "strawberry-graphql[fastapi,flask]",
        "deps": {
            "*": ["fastapi", "flask", "httpx"],
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
    "trytond": {
        "package": "trytond",
        "deps": {
            "*": ["werkzeug"],
            "<=5.0": ["werkzeug<1.0"],
        },
    },
    "typer": {
        "package": "typer",
        "deps": {},
    },
    "unleash": {
        "package": "UnleashClient",
        "deps": {},
    },
}
