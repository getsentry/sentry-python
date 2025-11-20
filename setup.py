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
    version="2.45.0",
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
        "http2": ["httpcore[http2]==1.*"],
        "httpx": ["httpx>=0.16.0"],
        "huey": ["huey>=2"],
        "huggingface_hub": ["huggingface_hub>=0.22"],
        "langchain": ["langchain>=0.0.210"],
        "langgraph": ["langgraph>=0.6.6"],
        "launchdarkly": ["launchdarkly-server-sdk>=9.8.0"],
        "litellm": ["litellm>=1.77.5"],
        "litestar": ["litestar>=2.0.0"],
        "loguru": ["loguru>=0.5"],
        "mcp": ["mcp>=1.15.0"],
        "openai": ["openai>=1.0.0", "tiktoken>=0.3.0"],
        "openfeature": ["openfeature-sdk>=0.7.1"],
        "opentelemetry": ["opentelemetry-distro>=0.35b0"],
        "opentelemetry-experimental": ["opentelemetry-distro"],
        "opentelemetry-otlp": ["opentelemetry-distro[otlp]>=0.35b0"],
        "pure-eval": ["pure_eval", "executing", "asttokens"],
        "pydantic_ai": ["pydantic-ai>=1.0.0"],
        "pymongo": ["pymongo>=3.1"],
        "pyspark": ["pyspark>=2.4.4"],
        "quart": ["quart>=0.16.1", "blinker>=1.1"],
        "rq": ["rq>=0.6"],
        "sanic": ["sanic>=0.8"],
        "sqlalchemy": ["sqlalchemy>=1.2"],
        "starlette": ["starlette>=0.19.1"],
        "starlite": ["starlite>=1.48"],
        "statsig": ["statsig>=0.55.3"],
        "tornado": ["tornado>=6"],
        "unleash": ["UnleashClient>=6.0.1"],
        "google-genai": ["google-genai>=1.29.0"],
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
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    options={"bdist_wheel": {"universal": "1"}},
)
