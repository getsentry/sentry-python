import functools
import nox
import configparser
import braceexpand
import itertools

MAX_JOBS = 50

def expand_factors(value):
    for line in value.splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        for expanded in braceexpand.braceexpand(line):
            yield expanded


def find_dependencies(deps, env):
    parts = env.split("-")

    for combo in itertools.product((True, False), repeat=len(parts)):
        key = "-".join(parts[i] for i, include in enumerate(combo) if include)

        if key in deps:
            yield deps[key]

def parse_tox():
    config = configparser.ConfigParser()
    config.read("tox.ini")

    dependencies = {}

    for declaration in expand_factors(config['testenv']['deps']):
        if declaration.startswith("-r "):
            continue

        env_matcher, dependency = declaration.split(":", 1)
        dependencies[env_matcher.strip()] = dependency

    jobs = {}

    for env in expand_factors(config['tox']['envlist']):
        python_version, integration, framework_version, *_ = (
            list(env.split("-")) + [None, None]
        )

        python_version_jobs = jobs.setdefault(python_version, [])
        for job in python_version_jobs:
            if job.setdefault(integration, framework_version) == framework_version:
                break
        else:
            python_version_jobs.append({integration: framework_version})

    return dependencies, jobs


def generate_sessions(_locals):
    dependencies, jobs = parse_tox()

    for python_version, batches in jobs.items():
        for batch_name, integrations in enumerate(batches):
            job_name = f"{python_version}-{batch_name}"
            deps = []
            for integration, version in integrations.items():
                deps.extend(find_dependencies(
                    dependencies, f"{python_version}-{integration}-{version}"
                ))

            def func(session, deps=deps):
                session.install("-r", "test-requirements.txt", *deps)

            assert python_version.startswith("py")
            nox_python_version = "pypy" if python_version == "pypy" else python_version[2:]

            _locals[job_name] = nox.session(
                func=func, reuse_venv=True, python=nox_python_version, name=job_name
            )


generate_sessions(locals())
