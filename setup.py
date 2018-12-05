#!/usr/bin/env python

"""
Sentry-Python - Sentry SDK for Python
=====================================

**Sentry-Python is an SDK for Sentry.** Check out `GitHub
<https://github.com/getsentry/sentry-python>`_ to find out more.
"""

from setuptools import setup, find_packages

setup(
    name="sentry-sdk",
    version="0.6.2",
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
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
