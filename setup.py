#!/usr/bin/env python

"""
Sentry-Python - Sentry SDK for Python
=====================================

**Sentry-Python is an SDK for Sentry.** Check out `GitHub
<https://github.com/getsentry/sentry-python>`_ to find out more.
"""

import os

from setuptools import find_packages, setup

here = os.path.abspath(os.path.dirname(__file__))


def get_file_text(file_name):
    with open(os.path.join(here, file_name)) as in_file:
        return in_file.read()


setup(
    name="sentry-sdk",
    version="2.66.1",
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
    license_expression="MIT",
    python_requires=">=3.6",
    install_requires=[
        "urllib3>=1.26.11",
        "certifi",
    ],
    extras_require={
        "asyncio": ["httpcore[asyncio]==1.*"],
        "flask": ["flask", "blinker>=1.1", "markupsafe"],
        "grpcio": ["grpcio>=1.21.1", "protobuf>=3.8.0"],
        "http2": ["httpcore[http2]==1.*"],
        "openai": ["openai", "tiktoken>=0.3.0"],
        "opentelemetry-otlp": ["opentelemetry-distro[otlp]>=0.35b0"],
        "pure-eval": ["pure_eval", "executing", "asttokens"],
        "quart": ["quart", "blinker>=1.1"],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
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
)
