#!/usr/bin/env python

"""
Sentry-Python - Sentry SDK for Python
=====================================

**Sentry-Python is an SDK for Sentry.** Check out `GitHub
<https://github.com/getsentry/sentry-python>`_ to find out more.
"""

import os
from setuptools import setup, find_packages

here = os.path.abspath(os.path.dirname(__file__))


def get_file_text(file_name):
    with open(os.path.join(here, file_name)) as in_file:
        return in_file.read()


setup(
    name="sentry-sdk",
    version="2.8.0",
    author="Sentry Team and Contributors",
    author_email="hello@sentry.io",
    url="https://github.com/getsentry/sentry-python",
    project_urls={
        "Documentation": "https://docs.sentry.io/platforms/python/",
        "Changelog": "https://github.com/getsentry/sentry-python/blob/master/CHANGELOG.md",
    },
    description="Python client for Sentry (https://sentry.io)",
    long_description=get_file_text("README.md"),
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=("tests", "tests.*")),
    # PEP 561
    package_data={"sentry_sdk": ["py.typed"]},
    zip_safe=False,
    license="MIT",
    python_requires=">=3.6",
    install_requires=[
        "urllib3>=1.26.11",
        "certifi",
    ],
    extras_require={
        "aiohttp": ["aiohttp>=3.5"],
        "anthropic": ["anthropic>=0.16"],
        "arq": ["arq>=0.23"],
        "asyncpg": ["asyncpg>=0.23"],
        "beam": ["apache-beam>=2.12"],
        "bottle": ["bottle>=0.12.13"],
        "celery": ["celery>=3"],
        "celery-redbeat": ["celery-redbeat>=2"],
        "chalice": ["chalice>=1.16.0"],
        "clickhouse-driver": ["clickhouse-driver>=0.2.0"],
        "django": ["django>=1.8"],
        "falcon": ["falcon>=1.4"],
        "fastapi": ["fastapi>=0.79.0"],
        "flask": ["flask>=0.11", "blinker>=1.1", "markupsafe"],
        "grpcio": ["grpcio>=1.21.1", "protobuf>=3.8.0"],
        "httpx": ["httpx>=0.16.0"],
        "huey": ["huey>=2"],
        "huggingface_hub": ["huggingface_hub>=0.22"],
        "langchain": ["langchain>=0.0.210"],
        "loguru": ["loguru>=0.5"],
        "openai": ["openai>=1.0.0", "tiktoken>=0.3.0"],
        "opentelemetry": ["opentelemetry-distro>=0.35b0"],
        "opentelemetry-experimental": [
            # There's an umbrella package called
            # opentelemetry-contrib-instrumentations that installs all
            # available instrumentation packages, however it's broken in recent
            # versions (after 0.41b0), see
            # https://github.com/open-telemetry/opentelemetry-python-contrib/issues/2053
            "opentelemetry-instrumentation-aio-pika==0.46b0",
            "opentelemetry-instrumentation-aiohttp-client==0.46b0",
            # "opentelemetry-instrumentation-aiohttp-server==0.46b0",  # broken package
            "opentelemetry-instrumentation-aiopg==0.46b0",
            "opentelemetry-instrumentation-asgi==0.46b0",
            "opentelemetry-instrumentation-asyncio==0.46b0",
            "opentelemetry-instrumentation-asyncpg==0.46b0",
            "opentelemetry-instrumentation-aws-lambda==0.46b0",
            "opentelemetry-instrumentation-boto==0.46b0",
            "opentelemetry-instrumentation-boto3sqs==0.46b0",
            "opentelemetry-instrumentation-botocore==0.46b0",
            "opentelemetry-instrumentation-cassandra==0.46b0",
            "opentelemetry-instrumentation-celery==0.46b0",
            "opentelemetry-instrumentation-confluent-kafka==0.46b0",
            "opentelemetry-instrumentation-dbapi==0.46b0",
            "opentelemetry-instrumentation-django==0.46b0",
            "opentelemetry-instrumentation-elasticsearch==0.46b0",
            "opentelemetry-instrumentation-falcon==0.46b0",
            "opentelemetry-instrumentation-fastapi==0.46b0",
            "opentelemetry-instrumentation-flask==0.46b0",
            "opentelemetry-instrumentation-grpc==0.46b0",
            "opentelemetry-instrumentation-httpx==0.46b0",
            "opentelemetry-instrumentation-jinja2==0.46b0",
            "opentelemetry-instrumentation-kafka-python==0.46b0",
            "opentelemetry-instrumentation-logging==0.46b0",
            "opentelemetry-instrumentation-mysql==0.46b0",
            "opentelemetry-instrumentation-mysqlclient==0.46b0",
            "opentelemetry-instrumentation-pika==0.46b0",
            "opentelemetry-instrumentation-psycopg==0.46b0",
            "opentelemetry-instrumentation-psycopg2==0.46b0",
            "opentelemetry-instrumentation-pymemcache==0.46b0",
            "opentelemetry-instrumentation-pymongo==0.46b0",
            "opentelemetry-instrumentation-pymysql==0.46b0",
            "opentelemetry-instrumentation-pyramid==0.46b0",
            "opentelemetry-instrumentation-redis==0.46b0",
            "opentelemetry-instrumentation-remoulade==0.46b0",
            "opentelemetry-instrumentation-requests==0.46b0",
            "opentelemetry-instrumentation-sklearn==0.46b0",
            "opentelemetry-instrumentation-sqlalchemy==0.46b0",
            "opentelemetry-instrumentation-sqlite3==0.46b0",
            "opentelemetry-instrumentation-starlette==0.46b0",
            "opentelemetry-instrumentation-system-metrics==0.46b0",
            "opentelemetry-instrumentation-threading==0.46b0",
            "opentelemetry-instrumentation-tornado==0.46b0",
            "opentelemetry-instrumentation-tortoiseorm==0.46b0",
            "opentelemetry-instrumentation-urllib==0.46b0",
            "opentelemetry-instrumentation-urllib3==0.46b0",
            "opentelemetry-instrumentation-wsgi==0.46b0",
        ],
        "pure_eval": ["pure_eval", "executing", "asttokens"],
        "pymongo": ["pymongo>=3.1"],
        "pyspark": ["pyspark>=2.4.4"],
        "quart": ["quart>=0.16.1", "blinker>=1.1"],
        "rq": ["rq>=0.6"],
        "sanic": ["sanic>=0.8"],
        "sqlalchemy": ["sqlalchemy>=1.2"],
        "starlette": ["starlette>=0.19.1"],
        "starlite": ["starlite>=1.48"],
        "tornado": ["tornado>=6"],
    },
    entry_points={
        "opentelemetry_propagator": [
            "sentry=sentry_sdk.integrations.opentelemetry:SentryPropagator"
        ]
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    options={"bdist_wheel": {"universal": "1"}},
)
