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
    version="1.40.1",
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
    install_requires=[
        'urllib3>=1.25.7; python_version<="3.4"',
        'urllib3>=1.26.9; python_version=="3.5"',
        'urllib3>=1.26.11; python_version>="3.6"',
        "certifi",
    ],
    extras_require={
        "aiohttp": ["aiohttp>=3.5"],
        "arq": ["arq>=0.23"],
        "asyncpg": ["asyncpg>=0.23"],
        "beam": ["apache-beam>=2.12"],
        "bottle": ["bottle>=0.12.13"],
        "celery": ["celery>=3"],
        "chalice": ["chalice>=1.16.0"],
        "clickhouse-driver": ["clickhouse-driver>=0.2.0"],
        "django": ["django>=1.8"],
        "falcon": ["falcon>=1.4"],
        "fastapi": ["fastapi>=0.79.0"],
        "flask": ["flask>=0.11", "blinker>=1.1", "markupsafe"],
        "grpcio": ["grpcio>=1.21.1"],
        "httpx": ["httpx>=0.16.0"],
        "huey": ["huey>=2"],
        "loguru": ["loguru>=0.5"],
        "opentelemetry": ["opentelemetry-distro>=0.35b0"],
        "opentelemetry-experimental": [
            "opentelemetry-distro~=0.40b0",
            "opentelemetry-instrumentation-aiohttp-client~=0.40b0",
            "opentelemetry-instrumentation-django~=0.40b0",
            "opentelemetry-instrumentation-fastapi~=0.40b0",
            "opentelemetry-instrumentation-flask~=0.40b0",
            "opentelemetry-instrumentation-requests~=0.40b0",
            "opentelemetry-instrumentation-sqlite3~=0.40b0",
            "opentelemetry-instrumentation-urllib~=0.40b0",
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
        "tornado": ["tornado>=5"],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
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
