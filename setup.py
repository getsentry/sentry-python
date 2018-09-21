#!/usr/bin/env python

"""
Sentry-Python - Sentry SDK for Python
=====================================

**Sentry-Python is an experimental SDK for Sentry.** Check out `GitHub
<https://github.com/getsentry/sentry-python>`_ to find out more.
"""

from setuptools import setup, find_packages

setup(
    name="sentry-sdk",
    version="0.3.4",
    author="Sentry Team and Contributors",
    author_email="hello@getsentry.com",
    url="https://github.com/getsentry/sentry-python",
    description="Python client for Sentry (https://getsentry.com)",
    long_description=__doc__,
    packages=find_packages(exclude=("tests", "tests.*")),
    zip_safe=False,
    license="BSD",
    install_requires=["urllib3", "certifi"],
    extras_require={"flask": ["flask>=0.8", "blinker>=1.1"]},
)
