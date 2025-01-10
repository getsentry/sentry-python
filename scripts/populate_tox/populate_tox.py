import functools
import time
from collections import defaultdict
from datetime import datetime, timedelta
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pathlib import Path
from typing import Union

import requests
from jinja2 import Environment, FileSystemLoader

from sentry_sdk.integrations import _MIN_VERSIONS

from dependencies import DEPENDENCIES

# TODO:
# - put GROUPS someplace where both this script and split_tox_actions can use it
# - allow to specify version dependent dependencies
# - (optional) order by alphabet, not group then alphabet
# - clean up the hardcoded stuff in tox.ini
# - fix otel (since it only has prereleases)
# - better picking of releases (e.g., if multiple majors, pick from each, etc.)

# Only consider package versions going back this far
CUTOFF = datetime.now() - timedelta(days=365 * 5)

TOX_FILE = Path(__file__).resolve().parent.parent.parent / "tox.ini"
ENV = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent),
    trim_blocks=True,
    lstrip_blocks=True,
)

PYPI_PROJECT_URL = "https://pypi.python.org/pypi/{project}/json"
PYPI_VERSION_URL = "https://pypi.python.org/pypi/{project}/{version}/json"
CLASSIFIER_PREFIX = "Programming Language :: Python :: "

MAX_PYTHON_VERSION = "3.13"

GROUPS = {
    "Common": [
        "common",
    ],
    "AI": [
        "anthropic",
        "cohere",
        "langchain",
        "langchain-notiktoken",
        "openai",
        "openai-notiktoken",
        "huggingface_hub",
    ],
    "AWS": [
        # this is separate from Cloud Computing because only this one test suite
        # needs to run with access to GitHub secrets
        "aws_lambda",
    ],
    "Cloud": [
        "boto3",
        "chalice",
        "cloud_resource_context",
        "gcp",
    ],
    "DBs": [
        "asyncpg",
        "clickhouse-driver",
        "pymongo",
        "redis",
        "redis-py-cluster-legacy",
        "sqlalchemy",
    ],
    "Flags": [
        "launchdarkly",
        "openfeature",
        "unleash",
    ],
    "GraphQL": [
        "ariadne",
        "gql",
        "graphene",
        "strawberry",
    ],
    "Network": [
        "gevent",
        "grpc",
        "httpx",
        "requests",
    ],
    "Tasks": [
        "arq",
        "beam",
        "celery",
        "dramatiq",
        "huey",
        "ray",
        "rq",
        "spark",
    ],
    "Web 1": [
        "django",
        "flask",
        "starlette",
        "fastapi",
    ],
    "Web 2": [
        "aiohttp",
        "asgi",
        "bottle",
        "falcon",
        "litestar",
        "pyramid",
        "quart",
        "sanic",
        "starlite",
        "tornado",
    ],
    "Misc": [
        "loguru",
        "opentelemetry",
        "potel",
        "pure_eval",
        "trytond",
        "typer",
    ],
}

IGNORE = {
    # Do not try auto-generating the tox entries for these. They will be
    # hardcoded in tox.ini.
    "asgi",
    "aws_lambda",
    "cloud_resource_context",
    "common",
    "gcp",
    "gevent",
    "potel",
}


@functools.cache
def fetch_package(package: str) -> dict:
    """Fetch package metadata from PYPI."""
    url = PYPI_PROJECT_URL.format(project=package)
    pypi_data = requests.get(url)

    if pypi_data.status_code != 200:
        print(f"{package} not found")

    return pypi_data.json()


@functools.cache
def fetch_release(package: str, version: Version) -> dict:
    url = PYPI_VERSION_URL.format(project=package, version=version)
    pypi_data = requests.get(url)

    if pypi_data.status_code != 200:
        print(f"{package} not found")

    return pypi_data.json()


def get_releases(integration: str, pypi_data: dict) -> list[Version]:
    min_supported = _MIN_VERSIONS.get(integration)
    if min_supported:
        min_supported = Version(".".join(map(str, min_supported)))
        print(f"  Minimum supported version for {integration} is {min_supported}.")
    else:
        print(
            f"  {integration} doesn't have a minimum version. Maybe we should define one?"
        )

    releases = []

    for release, metadata in pypi_data["releases"].items():
        if not metadata:
            continue

        meta = metadata[0]
        if datetime.fromisoformat(meta["upload_time"]) < CUTOFF:
            continue

        if meta["yanked"]:
            continue

        version = Version(release)

        if min_supported and version < min_supported:
            continue

        # The release listing that you get via the package endpoint doesn't
        # contain all metadata for a release. `requires_python` is included,
        # but classifiers are not (they require a separate call to the release
        # endpoint).
        # Some packages don't use `requires_python`, they supply classifiers
        # instead.
        version.python_versions = None
        requires_python = meta.get("requires_python")
        if requires_python:
            print(f"  {version} has a requires python {requires_python}")
            try:
                version.python_versions = supported_python_versions(
                    SpecifierSet(requires_python)
                )
            except InvalidSpecifier:
                continue
        else:
            print(f"  {version} has no requires python. Fetching classifiers")
            # No requires_python. Let's fetch the metadata to see the classifiers.
            release_metadata = fetch_release(package, version)
            version.python_versions = supported_python_versions(
                determine_python_versions(release_metadata)
            )
            print(f"     Fetched {version.python_versions}")
            time.sleep(0.1)

        if not version.python_versions:
            continue

        if version.is_prerelease or version.is_postrelease:
            # TODO: consider the newest prerelease unless obsolete
            continue

        for i, saved_version in enumerate(releases):
            if (
                version.major == saved_version.major
                and version.minor == saved_version.minor
                and version.micro > saved_version.micro
            ):
                # don't save all patch versions of a release, just the newest one
                releases[i] = version
                break
        else:
            releases.append(version)

    return sorted(releases)


def pick_releases_to_test(releases: list[Version]) -> list[Version]:
    """Pick a handful of releases from a list of supported releases."""
    indexes = [
        0,  # oldest version supported
        len(releases) // 3,
        len(releases) // 3 * 2,
        -1,  # latest
    ]

    filtered_releases = []

    for i in indexes:
        try:
            if releases[i] not in filtered_releases:
                filtered_releases.append(releases[i])
        except IndexError:
            pass

    return filtered_releases


def supported_python_versions(python_versions: SpecifierSet) -> list[Version]:
    """Get an intersection of python_versions and Python versions supported in the SDK."""
    supported = []

    curr = MIN_PYTHON_VERSION
    while curr <= MAX_PYTHON_VERSION:
        if curr in python_versions:
            supported.append(curr)
        next = [int(v) for v in str(curr).split(".")]
        next[1] += 1
        curr = Version(".".join(map(str, next)))

    return supported


def pick_python_versions_to_test(python_versions: list[Version]) -> list[Version]:
    filtered_python_versions = [
        python_versions[0],
    ]

    if len(python_versions) > 1:
        filtered_python_versions.append(python_versions[-1])

    return filtered_python_versions


def determine_python_versions(pypi_data: dict) -> Union[SpecifierSet, list[Version]]:
    try:
        classifiers = pypi_data["info"]["classifiers"]
    except (AttributeError, KeyError):
        return []

    python_versions = []
    for classifier in classifiers:
        if classifier.startswith(CLASSIFIER_PREFIX):
            python_version = classifier[len(CLASSIFIER_PREFIX) :]
            if "." in python_version:
                # We don't care about stuff like
                # Programming Language :: Python :: 3 :: Only,
                # Programming Language :: Python :: 3,
                # etc., we're only interested in specific versions, like 3.13
                python_versions.append(Version(python_version))

    if python_versions:
        python_versions.sort()
        return python_versions

    try:
        requires_python = pypi_data["info"]["requires_python"]
    except (AttributeError, KeyError):
        pass

    if requires_python:
        return SpecifierSet(requires_python)

    return []


def _render_python_versions(python_versions):
    return (
        "{"
        + ",".join(f"py{version.major}.{version.minor}" for version in python_versions)
        + "}"
    )


def write_tox_file(packages: dict) -> None:
    template = ENV.get_template("tox.jinja")

    context = {"groups": {}}
    for group, integrations in packages.items():
        context["groups"][group] = []
        for integration in integrations:
            context["groups"][group].append(
                {
                    "name": integration["name"],
                    "package": integration["package"],
                    "extra": integration["extra"],
                    "releases": integration["releases"],
                    "dependencies": DEPENDENCIES[integration["name"]][1:],
                }
            )

    rendered = template.render(context)

    with open(TOX_FILE, "w") as file:
        file.write(rendered)


if __name__ == "__main__":
    print("Finding out the lowest and highest Python version supported by the SDK...")
    sdk_python_versions = determine_python_versions(fetch_package("sentry_sdk"))
    MIN_PYTHON_VERSION = sdk_python_versions[0]
    MAX_PYTHON_VERSION = sdk_python_versions[-1]
    print(
        f"The SDK supports Python versions {MIN_PYTHON_VERSION} - {MAX_PYTHON_VERSION}."
    )

    packages = defaultdict(list)

    for group, integrations in GROUPS.items():
        for integration in integrations:
            if integration in IGNORE:
                continue

            print(f"Processing {integration}...")

            # Figure out the actual main package
            package = DEPENDENCIES[integration][0]
            extra = None
            if "[" in package:
                extra = package[package.find("[") + 1 : package.find("]")]
                package = package[: package.find("[")]

            # Fetch data for the main package
            pypi_data = fetch_package(package)

            # Get the list of all supported releases
            releases = get_releases(integration, pypi_data)
            if not releases:
                print("  Found no supported releases.")
                continue

            # Pick a handful of the supported releases to actually test against
            # and fetch the PYPI data for each to determine which Python versions
            # to test it on
            test_releases = pick_releases_to_test(releases)

            for release in test_releases:
                release_pypi_data = fetch_release(package, release)
                release.python_versions = pick_python_versions_to_test(
                    release.python_versions
                )
                if not release.python_versions:
                    print(f"  Release {release} has no Python versions, skipping.")
                release.rendered_python_versions = _render_python_versions(
                    release.python_versions
                )

                # Give PYPI some breathing room
                time.sleep(0.25)

            test_releases = [
                release for release in test_releases if release.python_versions
            ]
            if test_releases:
                packages[group].append(
                    {
                        "name": integration,
                        "package": package,
                        "extra": extra,
                        "releases": test_releases,
                    }
                )

    write_tox_file(packages)
