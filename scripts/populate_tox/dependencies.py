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
    "anthropic": [
        "anthropic",
        "httpx",  # TODO has an upper bound anthropic-v{0.16,0.28}: httpx<0.28.0
        "pytest-asyncio",
    ],
    "ariadne": [
        "ariadne",
        "fastapi",
        "flask",
        "httpx",
    ],
    "arq": [
        "arq",
        "pydantic",  # TODO arq-v0.23: pydantic<2
        "fakeredis>=2.2.0,<2.8",
        "pytest-asyncio",
        "async-timeout",
    ],
    "asyncpg": [
        "asyncpg",
        "pytest-asyncio",
    ],
    "aws_lambda": [
        "boto3",
    ],
    "beam": ["apache-beam"],
    "boto3": [
        "boto3",
    ],
    "bottle": [
        "bottle",
        "werkzeug<2.1.0",
    ],
    "celery": [
        "celery",
        "importlib-metadata",  # {py3.7}-celery: importlib-metadata<5.0
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
    ],
    "django": [
        "django",
        "channels[daphne]",  # django-v{2.0,2.2,3.0,3.2,4.0,4.1,4.2,5.0,5.1}: channels[daphne]
        "djangorestframework",  # django-v{1.11,2.0,2.1,2.2,3.0,3.1,3.2}: djangorestframework>=3.0.0,<4.0.0
        "psycopg2-binary",
        "pytest-asyncio",
        "pytest-django",  # django-v{1.11,2.0,2.2,3.0}: pytest-django<4.0
        "six",  # django-v{2.2,3.0}: six
        "werkzeug",  # django-v{1.11,2.0,2.2,3.0,3.2}: Werkzeug<2.1.0
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
    "flask": [
        "flask",
        "flask-login",
        "werkzeug",  # flask-v{1,2.0}: Werkzeug<2.1.0
        "markupsafe",  # flask-v{1,2.0}: markupsafe<2.1.0
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
    "litestar": [
        "litestar",
        "pytest-asyncio",
        "python-multipart",
        "requests",
        "cryptography",
        "httpx",
    ],
    "loguru": [
        "loguru",
    ],
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
    "quart": [
        "quart",
        "quart-auth",
        "pytest-asyncio",
        "blinker",
        "jinja2",
        "hypercorn",
        "taskgroup",
    ],
    "ray": [
        "ray",
    ],
    "redis": [
        "redis",
        "fakeredis",
        "pytest<8.0.0",
        "pytest-asyncio",
    ],
    "redis-py-cluster-legacy": [
        "redis-py-cluster",
    ],
    "requests": [
        "requests",
    ],
    "rq": [
        "rq",
        "fakeredis",
    ],
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
    "tornado": [
        "tornado",
        "pytest",
    ],
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
