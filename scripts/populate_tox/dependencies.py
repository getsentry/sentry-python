{
    "anthropic": {
        "main": "anthropic",
        "dependencies": {
            "*": ["anthropic", "pytest-asyncio"],
            "anthropic<0.35": "httpx<0.28.0",
        },
    },
    "celery": {"deps": [], "conditions": {"py3.7": "importlib-metadata<5.0"}},
    "httpx": {
        "deps": [],
        "conditions": {
            "httpx==0.16": "pytest-httpx==0.16",
        },
    },
}


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
    # XXX arq-v0.23: pydantic<2
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
    # XXX
    # django: psycopg2-binary
    # django-v{1.11,2.0,2.1,2.2,3.0,3.1,3.2}: djangorestframework>=3.0.0,<4.0.0
    # django-v{2.0,2.2,3.0,3.2,4.0,4.1,4.2,5.0,5.1}: channels[daphne]
    # django-v{2.2,3.0}: six
    # django-v{1.11,2.0,2.2,3.0,3.2}: Werkzeug<2.1.0
    # django-v{1.11,2.0,2.2,3.0}: pytest-django<4.0
    # django-v{3.2,4.0,4.1,4.2,5.0,5.1}: pytest-django
    # django-v{4.0,4.1,4.2,5.0,5.1}: djangorestframework
    # django-v{4.0,4.1,4.2,5.0,5.1}: pytest-asyncio
    # django-v{4.0,4.1,4.2,5.0,5.1}: Werkzeug
    # django-latest: djangorestframework
    # django-latest: pytest-asyncio
    # django-latest: pytest-django
    # django-latest: Werkzeug
    # django-latest: channels[daphne]
    "django": {
        "package": "django",
        "deps": {
            "*": [
                "channels[daphne]",
                "djangorestframework",
                "psycopg2-binary",
                "pytest-asyncio",
                "pytest-django",
                "six",
                "werkzeug",
            ]
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
    # XXX
    # httpx-v0.16: pytest-httpx==0.10.0
    # httpx-v0.18: pytest-httpx==0.12.0
    # httpx-v0.20: pytest-httpx==0.14.0
    # httpx-v0.22: pytest-httpx==0.19.0
    # httpx-v0.23: pytest-httpx==0.21.0
    # httpx-v0.24: pytest-httpx==0.22.0
    # httpx-v0.25: pytest-httpx==0.25.0
    # httpx: pytest-httpx
    # anyio is a dep of httpx
    # httpx: anyio<4.0.0
    "httpx": {
        "package": "httpx",
        "deps": {
            "*": ["anyio<4.0.0"],
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
    # langchain-v0.1: langchain~=0.1.11
    # langchain-v0.1: tiktoken~=0.6.0
    # langchain-v0.1: httpx<0.28.0
    # langchain-v0.3: langchain~=0.3.0
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
            "*": ["langchain-community", "openai", "tiktoken", "httpx"],
        },
    },
    "langchain_notiktoken": {
        "package": "langchain",
        "deps": {
            "*": ["langchain-openai", "openai"],
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
    # openai-v1.0: openai~=1.0.0
    # openai-v1.0: tiktoken
    # openai-v1.0: httpx<0.28.0
    # openai-v1.22: openai~=1.22.0
    # openai-v1.22: tiktoken
    # openai-v1.22: httpx<0.28.0
    # openai-v1.55: openai~=1.55.0
    # openai-v1.55: tiktoken
    # openai-latest: openai
    # openai-latest: tiktoken~=0.6.0
    # openai-notiktoken: openai
    "openai": {
        "package": "openai",
        "deps": {"*": ["pytest-asyncio", "tiktoken", "httpx"]},
    },
    "openai_notiktoken": {
        "package": "openai",
        "deps": {
            "*": ["pytest-asyncio"],
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
    # XXX
    # quart-v0.16: blinker<1.6
    # quart-v0.16: jinja2<3.1.0
    # quart-v0.16: Werkzeug<2.1.0
    # quart-v0.16: hypercorn<0.15.0
    # quart-v0.16: quart~=0.16.0
    # quart-v0.19: Werkzeug>=3.0.0
    # quart-v0.19: quart~=0.19.0
    # {py3.8}-quart: taskgroup==0.0.0a4
    "quart": {
        "package": "quart",
        "deps": {
            "*": [
                "quart-auth",
                "pytest-asyncio",
            ],
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
    # XXX
    # https://github.com/jamesls/fakeredis/issues/245
    # rq-v{0.6}: fakeredis<1.0
    # rq-v{0.6}: redis<3.2.2
    # rq-v{0.13,1.0,1.5,1.10}: fakeredis>=1.0,<1.7.4
    # rq-v{1.15,1.16}: fakeredis
    # {py3.6,py3.7}-rq-v{1.15,1.16}: fakeredis!=2.26.0  # https://github.com/cunla/fakeredis-py/issues/341
    # rq-latest: fakeredis
    # {py3.6,py3.7}-rq-latest: fakeredis!=2.26.0  # https://github.com/cunla/fakeredis-py/issues/341
    "rq": {
        "package": "rq",
        "deps": {
            "*": ["fakeredis"],
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
