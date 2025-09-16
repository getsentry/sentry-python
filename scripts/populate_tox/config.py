# The TEST_SUITE_CONFIG dictionary defines, for each integration test suite,
# the main package (framework, library) to test with; any additional test
# dependencies, optionally gated behind specific conditions; and optionally
# the Python versions to test on.
#
# See scripts/populate_tox/README.md for more info on the format and examples.

TEST_SUITE_CONFIG = {
    "aiohttp": {
        "package": "aiohttp",
        "deps": {
            "*": ["pytest-aiohttp"],
            ">=3.8": ["pytest-asyncio"],
        },
        "python": ">=3.7",
    },
    "anthropic": {
        "package": "anthropic",
        "deps": {
            "*": ["pytest-asyncio"],
            "<0.50": ["httpx<0.28.0"],
        },
        "python": ">=3.8",
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
            "*": ["async-timeout", "pytest-asyncio", "fakeredis>=2.2.0,<2.8"],
            "<=0.23": ["pydantic<2"],
        },
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
        "python": ">=3.7",
    },
    "boto3": {
        "package": "boto3",
        "deps": {
            "py3.7,py3.8": ["urllib3<2.0.0"],
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
            "*": ["newrelic<10.17.0", "redis"],
            "py3.7": ["importlib-metadata<5.0"],
        },
    },
    "chalice": {
        "package": "chalice",
        "deps": {
            "*": ["pytest-chalice"],
        },
    },
    "clickhouse_driver": {
        "package": "clickhouse-driver",
    },
    "cohere": {
        "package": "cohere",
        "python": ">=3.9",
    },
    "django": {
        "package": "django",
        "deps": {
            "*": [
                "psycopg2-binary",
                "djangorestframework",
                "pytest-django",
                "Werkzeug",
            ],
            ">=2.0": ["channels[daphne]"],
            ">=2.2,<3.1": ["six"],
            ">=3.0": ["pytest-asyncio"],
            "<3.3": [
                "djangorestframework>=3.0,<4.0",
                "Werkzeug<2.1.0",
            ],
            "<3.1": ["pytest-django<4.0"],
        },
    },
    "dramatiq": {
        "package": "dramatiq",
    },
    "falcon": {
        "package": "falcon",
        "python": "<3.13",
    },
    "fastapi": {
        "package": "fastapi",
        "deps": {
            "*": [
                "httpx",
                "pytest-asyncio",
                "python-multipart",
                "requests",
                "anyio<4",
            ],
            # There's an incompatibility between FastAPI's TestClient, which is
            # actually Starlette's TestClient, which is actually httpx's Client.
            # httpx dropped a deprecated Client argument in 0.28.0, Starlette
            # dropped it from its TestClient in 0.37.2, and FastAPI only pinned
            # Starlette>=0.37.2 from version 0.110.1 onwards -- so for older
            # FastAPI versions we use older httpx which still supports the
            # deprecated argument.
            "<0.110.1": ["httpx<0.28.0"],
            "py3.6": ["aiocontextvars"],
        },
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
    "huey": {
        "package": "huey",
    },
    "huggingface_hub": {
        "package": "huggingface_hub",
        "deps": {
            "*": ["responses"],
        },
        "include": "<1.0",
    },
    "langchain-base": {
        "package": "langchain",
        "integration_name": "langchain",
        "deps": {
            "*": ["openai", "tiktoken", "langchain-openai"],
            "<=0.1": ["httpx<0.28.0"],
            ">=0.3": ["langchain-community"],
        },
        "include": "<1.0",
    },
    "langchain-notiktoken": {
        "package": "langchain",
        "integration_name": "langchain",
        "deps": {
            "*": ["openai", "langchain-openai"],
            "<=0.1": ["httpx<0.28.0"],
            ">=0.3": ["langchain-community"],
        },
        "include": "<1.0",
    },
    "langgraph": {
        "package": "langgraph",
    },
    "launchdarkly": {
        "package": "launchdarkly-server-sdk",
    },
    "litestar": {
        "package": "litestar",
        "deps": {
            "*": ["pytest-asyncio", "python-multipart", "requests", "cryptography"],
            "<2.7": ["httpx<0.28"],
        },
    },
    "loguru": {
        "package": "loguru",
    },
    "openai-base": {
        "package": "openai",
        "integration_name": "openai",
        "deps": {
            "*": ["pytest-asyncio", "tiktoken"],
            "<1.55": ["httpx<0.28"],
        },
        "python": ">=3.8",
    },
    "openai-notiktoken": {
        "package": "openai",
        "integration_name": "openai",
        "deps": {
            "*": ["pytest-asyncio"],
            "<1.55": ["httpx<0.28"],
        },
        "python": ">=3.8",
    },
    "openai_agents": {
        "package": "openai-agents",
        "deps": {
            "*": ["pytest-asyncio"],
        },
        "python": ">=3.10",
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
    "pyramid": {
        "package": "pyramid",
        "deps": {
            "*": ["werkzeug<2.1.0"],
        },
    },
    "quart": {
        "package": "quart",
        "deps": {
            "*": ["quart-auth", "pytest-asyncio", "Werkzeug"],
            ">=0.19": ["quart-flask-patch"],
            "<0.19": [
                "blinker<1.6",
                "jinja2<3.1.0",
                "Werkzeug<2.3.0",
                "hypercorn<0.15.0",
            ],
            "py3.8": ["taskgroup==0.0.0a4"],
        },
    },
    "redis_py_cluster_legacy": {
        "package": "redis-py-cluster",
    },
    "requests": {
        "package": "requests",
    },
    "spark": {
        "package": "pyspark",
        "python": ">=3.8",
    },
    "sqlalchemy": {
        "package": "sqlalchemy",
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
            # See the comment on FastAPI's httpx bound for more info
            "<0.37.2": ["httpx<0.28.0"],
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
        "include": "!=2.0.0a1,!=2.0.0a2",  # these are not relevant as there will never be a stable 2.0 release (starlite continues as litestar)
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
            "<=0.262.5": ["pydantic<2.11"],
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
    },
    "unleash": {
        "package": "UnleashClient",
    },
}
