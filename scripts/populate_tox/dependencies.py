# The DEPENDENCIES dictionary defines the test dependencies of each integration
# test suite.
#
# The format is:
# ```
# integration_name: {
#     "package": name_of_main_package_on_pypi,
#     "deps": {
#          rule1: [package1, package2, ...],
#          rule2: [package3, package4, ...],
#      }
# }
# ```
#
# The following can be set as a rule:
#   - `*`: packages will be always installed
#   - a version bound on the main package (e.g. `<=0.32`): packages will only be
#     installed if the main package falls into the version bounds specified
#   - specific Python version(s) in the form `py3.8,py3.9`: packages will only be
#     installed if the Python version matches one from the list
#
# Rules can be used to specify version bounds on older versions of the main
# package's dependencies, for example. If e.g. Flask tests generally need
# Werkzeug and don't care about its version, but Flask older than 3.0 needs
# a specific Werkzeug version to work, you can say:
#
# ```
#     "flask": {
#         "deps": {
#             "*": ["Werkzeug"],
#             "<3.0": ["Werkzeug<2.1.0"],
#         }
#     }
# ````


DEPENDENCIES = {
    "aiohttp": {
        "package": "aiohttp",
        "deps": {"*": ["pytest-aiohttp", "pytest-asyncio"]},
    },
    "anthropic": {
        "package": "anthropic",
        "deps": {
            "*": ["pytest-asyncio"],
            "<=0.32": ["httpx<0.28.0"],
        },
    },
    "ariadne": {
        "package": "ariadne",
        "deps": {
            "*": ["fastapi", "flask", "httpx"],
        },
    },
    "arq": {
        "package": "arq",
        "deps": {
            "*": ["fakeredis>=2.2.0,<2.8", "pytest-asyncio", "async-timeout"],
            "<=0.25": ["pydantic<2"],
        },
    },
    "asyncpg": {
        "package": "asyncpg",
        "deps": {
            "*": ["pytest-asyncio"],
        },
    },
    "beam": {
        "package": "apache-beam",
        "deps": {
            "*": [],
        },
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
            "py3.7": ["importlib-metata<5.0"],
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
    },
    "flask": {
        "package": "flask",
        "deps": {
            "*": ["flask-login", "werkzeug"],
            "<3.0": ["werkzeug<2.1.0", "markupsafe<2.1.0"],
        },
    },
    "gql": {
        "package": "gql[all]",
        "deps": {},
    },
    "graphene": {
        "package": "graphene",
        "deps": {"*": ["blinker", "fastapi", "flask", "httpx"]},
    },
    "grpc": {
        "package": "grpcio",
        "deps": {
            "*": ["protobuf", "mypy-protobuf", "types-protobuf", "pytest-asyncio"]
        },
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
    # XXX
    # langchain-v0.1: openai~=1.0.0
    # langchain-v0.1: tiktoken~=0.6.0
    # langchain-v0.1: httpx<0.28.0
    # langchain-v0.3: langchain-community
    # langchain-v0.3: tiktoken
    # langchain-v0.3: openai
    # langchain-{latest,notiktoken}: langchain
    # langchain-{latest,notiktoken}: langchain-openai
    # langchain-{latest,notiktoken}: openai>=1.6.1
    # langchain-latest: tiktoken~=0.6.0
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
            "<=0.16": [
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
            "*": ["websockets<11.0", "aiohttp", "sanic_testing", "aiocontextvars"],
            ">=22.0": ["sanic_testing"],
            "py3.6": ["aiocontextvars==0.2.1"],
        },
    },
    "spark": {
        "package": "pyspark",
        "deps": {},
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
        },
    },
    "starlite": {
        "package": "starlite",
        "deps": {
            "*": [
                "pytest-asyncio",
                "pytest-multipart",
                "requests",
                "cryptography",
                "pydantic<2.0.0",
                "httpx<0.28",
            ]
        },
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
