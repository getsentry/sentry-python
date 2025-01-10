# XXX move this to conftest?
DEPENDENCIES = {
    # package: [test_dependency_1, test_dependency_2, ...]
    #
    # The main package (e.g. aiohttp for the aiohttp test suite) needs to be
    # the first dependency listed. The script will then query PYPI for releases
    # of the package and will pick a handful to test against.
    "aiohttp": [
        "aiohttp",
        "pytest-aiohttp",
        "pytest-asyncio",
    ],
    # XXX anthropic-v{0.16,0.28}: httpx<0.28.0
    "anthropic": [
        "anthropic",
        "pytest-asyncio",
    ],
    "ariadne": [
        "ariadne",
        "fastapi",
        "flask",
        "httpx",
    ],
    # XXX arq-v0.23: pydantic<2
    "arq": [
        "arq",
        "fakeredis>=2.2.0,<2.8",
        "pytest-asyncio",
        "async-timeout",
    ],
    "asyncpg": [
        "asyncpg",
        "pytest-asyncio",
    ],
    "beam": [
        "apache-beam",
    ],
    "boto3": [
        "boto3",
    ],
    "bottle": [
        "bottle",
        "werkzeug<2.1.0",
    ],
    # XXX {py3.7}-celery: importlib-metadata<5.0
    "celery": [
        "celery",
        "newrelic",
        "redis",
    ],
    "chalice": [
        "chalice",
        "pytest-chalice==0.0.5",
    ],
    "clickhouse-driver": [
        "clickhouse-driver",
    ],
    "cohere": [
        "cohere",
        "httpx",
    ],
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
    "django": [
        "django",
        "channels[daphne]",
        "djangorestframework",
        "psycopg2-binary",
        "pytest-asyncio",
        "pytest-django",
        "six",
        "werkzeug",
    ],
    "dramatiq": [
        "dramatiq",
    ],
    "falcon": [
        "falcon",
    ],
    "fastapi": [
        "fastapi",
        "httpx",
        "anyio<4.0.0",
        "python-multipart",
        "pytest-asyncio",
        "requests",
    ],
    # XXX
    # flask-v{1,2.0}: Werkzeug<2.1.0
    # flask-v{1,2.0}: markupsafe<2.1.0
    "flask": [
        "flask",
        "flask-login",
        "werkzeug",
    ],
    "gql": [
        "gql[all]",
    ],
    "graphene": [
        "graphene",
        "blinker",
        "fastapi",
        "flask",
        "httpx",
    ],
    "grpc": [
        "grpcio",
        "protobuf",
        "mypy-protobuf",
        "types-protobuf",
        "pytest-asyncio",
    ],
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
    "httpx": [
        "httpx",
        "anyio<4.0.0",
    ],
    "huey": [
        "huey",
    ],
    "huggingface_hub": [
        "huggingface_hub",
    ],
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
    "langchain": [
        "langchain",
        "langchain-community",
        "openai",
        "tiktoken",
        "httpx",
    ],
    "langchain-notiktoken": [
        "langchain",
        "langchain-openai",
        "openai",
    ],
    # litestar-v{2.0,2.6}: httpx<0.28
    "litestar": [
        "litestar",
        "pytest-asyncio",
        "python-multipart",
        "requests",
        "cryptography",
    ],
    "loguru": [
        "loguru",
    ],
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
    "openai": [
        "openai",
        "pytest-asyncio",
        "tiktoken",
        "httpx",
    ],
    "openai-notiktoken": [
        "openai",
        "pytest-asyncio",
    ],
    "openfeature": [
        "openfeature-sdk",
    ],
    "launchdarkly": [
        "launchdarkly-server-sdk",
    ],
    "opentelemetry": [
        "opentelemetry-distro",
    ],
    "pure_eval": [
        "pure_eval",
    ],
    "pymongo": [
        "pymongo",
        "mockupdb",
    ],
    "pyramid": [
        "pyramid",
        "werkzeug<2.1.0",
    ],
    # XXX
    # quart-v0.16: blinker<1.6
    # quart-v0.16: jinja2<3.1.0
    # quart-v0.16: Werkzeug<2.1.0
    # quart-v0.16: hypercorn<0.15.0
    # quart-v0.16: quart~=0.16.0
    # quart-v0.19: Werkzeug>=3.0.0
    # quart-v0.19: quart~=0.19.0
    # {py3.8}-quart: taskgroup==0.0.0a4
    "quart": [
        "quart",
        "quart-auth",
        "pytest-asyncio",
    ],
    "ray": [
        "ray",
    ],
    # XXX
    # {py3.6,py3.7}-redis: fakeredis!=2.26.0  # https://github.com/cunla/fakeredis-py/issues/341
    "redis": [
        "redis",
        "fakeredis!=1.7.4",
        "pytest<8.0.0",
        "pytest-asyncio",
    ],
    "redis-py-cluster-legacy": [
        "redis-py-cluster",
    ],
    # XXX requests: requests>=2.0
    "requests": [
        "requests",
    ],
    # XXX
    # https://github.com/jamesls/fakeredis/issues/245
    # rq-v{0.6}: fakeredis<1.0
    # rq-v{0.6}: redis<3.2.2
    # rq-v{0.13,1.0,1.5,1.10}: fakeredis>=1.0,<1.7.4
    # rq-v{1.15,1.16}: fakeredis
    # {py3.6,py3.7}-rq-v{1.15,1.16}: fakeredis!=2.26.0  # https://github.com/cunla/fakeredis-py/issues/341
    # rq-latest: fakeredis
    # {py3.6,py3.7}-rq-latest: fakeredis!=2.26.0  # https://github.com/cunla/fakeredis-py/issues/341
    "rq": [
        "rq",
        "fakeredis",
    ],
    # XXX
    # sanic-v{22,23}: sanic_testing
    # sanic-latest: sanic_testing
    # {py3.6}-sanic: aiocontextvars==0.2.1
    "sanic": [
        "sanic",
        "websockets<11.0",
        "aiohttp",
        "sanic_testing",
        "aiocontextvars",
    ],
    "spark": [
        "pyspark",
    ],
    # XXX
    # starlette-v{0.19,0.24,0.28,0.32,0.36}: httpx<0.28.0
    "starlette": [
        "starlette",
        "pytest-asyncio",
        "python-multipart",
        "requests",
        "anyio<4.0.0",
        "jinja2",
        "httpx",
    ],
    "starlite": [
        "starlite",
        "pytest-asyncio",
        "pytest-multipart",
        "requests",
        "cryptography",
        "pydantic<2.0.0",
        "httpx<0.28",
    ],
    "sqlalchemy": [
        "sqlalchemy",
    ],
    "strawberry": [
        "strawberry-graphql[fastapi,flask]",
        "fastapi",
        "flask",
        "httpx",
    ],
    # XXX
    # Tornado <6.4.1 is incompatible with Pytest ≥8.2
    # See https://github.com/tornadoweb/tornado/pull/3382.
    # tornado-{v6.0,v6.2}: pytest<8.2
    "tornado": [
        "tornado",
        "pytest",
    ],
    # XXX
    # trytond-v4: werkzeug<1.0
    "trytond": [
        "trytond",
        "werkzeug",
    ],
    "typer": [
        "typer",
    ],
    "unleash": [
        "UnleashClient",
    ],
}
