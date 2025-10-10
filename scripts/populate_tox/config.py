# The TEST_SUITE_CONFIG dictionary defines, for each integration test suite,
# at least the main package (framework, library) to test with. Additional
# test dependencies, Python versions to test on, etc. can also be defined here.
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
        "num_versions": 2,
    },
    "arq": {
        "package": "arq",
        "deps": {
            "*": ["async-timeout", "pytest-asyncio", "fakeredis>=2.2.0,<2.8"],
            "<=0.23": ["pydantic<2"],
        },
        "num_versions": 2,
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
        "num_versions": 2,
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
        "num_versions": 2,
    },
    "clickhouse_driver": {
        "package": "clickhouse-driver",
        "num_versions": 2,
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
        "num_versions": 2,
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
        "num_versions": 2,
    },
    "google_genai": {
        "package": "google-genai",
        "deps": {
            "*": ["pytest-asyncio"],
        },
        "python": ">=3.9",
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
            "*": ["anyio<4.0.0"],
            ">=0.16,<0.17": ["pytest-httpx==0.10.0"],
            ">=0.17,<0.19": ["pytest-httpx==0.12.0"],
            ">=0.19,<0.21": ["pytest-httpx==0.14.0"],
            ">=0.21,<0.23": ["pytest-httpx==0.19.0"],
            ">=0.23,<0.24": ["pytest-httpx==0.21.0"],
            ">=0.24,<0.25": ["pytest-httpx==0.22.0"],
            ">=0.25,<0.26": ["pytest-httpx==0.25.0"],
            ">=0.26,<0.27": ["pytest-httpx==0.28.0"],
            ">=0.27,<0.28": ["pytest-httpx==0.30.0"],
            ">=0.28,<0.29": ["pytest-httpx==0.35.0"],
        },
        "python": {
            ">=0.28": ">=3.9",
        },
    },
    "huey": {
        "package": "huey",
        "num_versions": 2,
    },
    "huggingface_hub": {
        "package": "huggingface_hub",
        "deps": {
            "*": ["responses", "pytest-httpx"],
        },
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
        "num_versions": 2,
    },
    "litellm": {
        "package": "litellm",
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
        "num_versions": 2,
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
        "num_versions": 2,
    },
    "pure_eval": {
        "package": "pure_eval",
        "num_versions": 2,
    },
    "pydantic_ai": {
        "package": "pydantic-ai",
        "deps": {
            "*": ["pytest-asyncio"],
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
        "num_versions": 2,
    },
    "ray": {
        "package": "ray",
        "python": ">=3.9",
        "num_versions": 2,
    },
    "redis": {
        "package": "redis",
        "deps": {
            "*": ["fakeredis!=1.7.4", "pytest<8.0.0"],
            ">=4.0,<5.0": ["fakeredis<2.31.0"],
            "py3.6,py3.7,py3.8": ["fakeredis<2.26.0"],
            "py3.7,py3.8,py3.9,py3.10,py3.11,py3.12,py3.13": ["pytest-asyncio"],
        },
    },
    "redis_py_cluster_legacy": {
        "package": "redis-py-cluster",
        "num_versions": 2,
    },
    "requests": {
        "package": "requests",
        "num_versions": 2,
    },
    "rq": {
        "package": "rq",
        "deps": {
            # https://github.com/jamesls/fakeredis/issues/245
            # https://github.com/cunla/fakeredis-py/issues/341
            "*": ["fakeredis<2.28.0"],
            "<0.9": ["fakeredis<1.0", "redis<3.2.2"],
            ">=0.9,<0.14": ["fakeredis>=1.0,<1.7.4"],
            "py3.6,py3.7": ["fakeredis!=2.26.0"],
        },
    },
    "sanic": {
        "package": "sanic",
        "deps": {
            "*": ["websockets<11.0", "aiohttp"],
            ">=22": ["sanic-testing"],
            "py3.6": ["aiocontextvars==0.2.1"],
            "py3.8": ["tracerite<1.1.2"],
        },
        "num_versions": 4,
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
        "num_versions": 2,
    },
    "statsig": {
        "package": "statsig",
        "deps": {
            "*": ["typing_extensions"],
        },
        "num_versions": 2,
    },
    "strawberry": {
        "package": "strawberry-graphql[fastapi,flask]",
        "deps": {
            "*": ["httpx"],
            "<=0.262.5": ["pydantic<2.11"],
        },
        "num_versions": 2,
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
        "num_versions": 2,
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
        "num_versions": 2,
    },
    "unleash": {
        "package": "UnleashClient",
        "num_versions": 2,
    },
}
