import functools
import nox
import configparser
import braceexpand
import itertools

from tox.config import _split_factor_expr

MAX_JOBS = 50

def expand_envlist(value):
    for line in value.splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        for expanded in braceexpand.braceexpand(line):
            yield expanded


def find_dependencies(deps, env):
    env_factors = set(env.split("-"))
    for (matcher, dependency) in deps.items():
        for (included, excluded) in _split_factor_expr(matcher):
            if included <= env_factors and not env_factors & excluded:
                yield dependency

def parse_tox():
    config = configparser.ConfigParser()
    config.read("tox.ini")

    dependencies = {}

    for line in config['testenv']['deps'].splitlines():
        line = line.strip()
        if not line or line.startswith(("-r", "#")):
            continue

        env_matcher, dependency = line.split(":", 1)
        dependencies[env_matcher.strip()] = dependency

    batch_jobs = {}
    single_jobs = []

    for env in expand_envlist(config['tox']['envlist']):
        python_version, integration, framework_version, *_ = (
            list(env.split("-")) + [None, None]
        )

        python_version_jobs = batch_jobs.setdefault(python_version, [])

        if integration is None:
            python_version_jobs.append({})
        else:
            for job in python_version_jobs:
                if job and job.setdefault(integration, framework_version) == framework_version:
                    break
            else:
                python_version_jobs.append({integration: framework_version})

        single_jobs.append((python_version, integration, framework_version))

    return dependencies, batch_jobs, single_jobs


def _format_job_name(python_version, integration, framework_version):
    if integration is not None:
        return "{python_version}-{integration}-{framework_version}".format(python_version=python_version, integration=integration, framework_version=framework_version)
    else:
        return "{python_version}".format(python_version=python_version)


def generate_test_sessions():
    dependencies, batch_jobs, single_jobs = parse_tox()

    def add_nox_job(job_name, integrations, python_version, deps):
        job_name = job_name.replace(".", "").replace("-", "_")

        def func(session):
            session.install("-e", ".")
            session.install("-r", "test-requirements.txt", *deps)
            session.env['COVERAGE_FILE'] = ".coverage-{job_name}".format(job_name=job_name)
            session.run(
                "pytest",
                *["tests/integrations/{integration}".format(integration=integration) for integration in integrations],
                *session.posargs
            )

        assert python_version.startswith("py")
        nox_python_version = "pypy" if python_version == "pypy" else python_version[2:]

        globals()[job_name] = nox.session(
            func=func, reuse_venv=True, python=nox_python_version, name=job_name
        )

    for (python_version, integration, framework_version) in single_jobs:
        job_name = _format_job_name(python_version,integration,framework_version)
        deps = list(find_dependencies(dependencies, job_name))

        add_nox_job("test-{job_name}".format(job_name=job_name), [integration], python_version, deps)

    for python_version, batches in batch_jobs.items():
        for batch_name, integrations in enumerate(batches):
            job_name = "batchtest-{python_version}-{batch_name}".format(python_version=python_version, batch_name=batch_name)
            deps = []
            for integration, framework_version in integrations.items():
                deps.extend(find_dependencies(
                    dependencies, 
                    _format_job_name(python_version, integration, framework_version)
                ))

            add_nox_job(job_name, integrations, python_version, deps)


@nox.session(python="3.8")
def linters(session):
    session.install("-r", "linter-requirements.txt")

    session.run(*"flake8 tests examples sentry_sdk".split())
    session.run(*"black --check tests examples sentry_sdk".split())
    session.run(*"mypy examples sentry_sdk".split())


import os
travis_python = os.environ.get("TRAVIS_PYTHON_VERSION")

if travis_python:
    @nox.session(python=travis_python)
    def travis_test(session):
        for name, f in globals().items():
            if name.startswith("test-{travis_python}".format(travis_python=travis_python)):
                f(session)


generate_test_sessions()
