#!/usr/bin/env python

from setuptools import setup, find_packages

# read the contents of your README file
from os import path

this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, "README.md")) as f:
    long_description = f.read()

setup(
    name="sentry-sdk",
    version="0.1.0-preview4",
    author="Sentry Team and Contributors",
    author_email="hello@getsentry.com",
    url="https://github.com/getsentry/sentry-sdk",
    description="Python client for Sentry (https://getsentry.com)",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=("tests", "tests.*")),
    zip_safe=False,
    license="BSD",
    install_requires=["urllib3", "certifi"],
    extras_require={"flask": ["flask>=0.8", "blinker>=1.1"]},
)
