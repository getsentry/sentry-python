#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name="sentry-sdk",
    version="0.1.0-preview3",
    author="Sentry Team and Contributors",
    author_email="hello@getsentry.com",
    url="https://github.com/getsentry/sentry-sdk",
    description="Python client for Sentry (https://getsentry.com)",
    long_description=__doc__,
    packages=find_packages(exclude=("tests", "tests.*")),
    zip_safe=False,
    license="BSD",
    install_requires=["urllib3", "certifi"],
    extras_require={"flask": ["flask>=0.8", "blinker>=1.1"]},
)
