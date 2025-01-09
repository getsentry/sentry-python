import functools
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests
from jinja2 import Environment, FileSystemLoader

from sentry_sdk.integrations import _MIN_VERSIONS
from sentry_sdk.utils import parse_version

from dependencies import DEPENDENCIES

# TODO:
# - put GROUPS someplace where both this script and split_tox_actions can use it
# - allow to specify version dependent dependencies
# - (optional) use a proper version parser for requires_python
# - (optional) order by alphabet, not group then alphabet

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

INTEGRATIONS_MIN_VERSIONS = {}

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
    "common",
    "gevent",
    "asgi",
    "cloud_resource_context",
    "potel",
    "gcp",
}

packages = {}


@functools.total_ordering
class Version:
    def __init__(self, version, metadata=None):
        self.raw = version
        self.metadata = metadata

        self.parsed = None
        self.python_versions = []

        if isinstance(version, tuple):
            self.parsed = version
            self.raw = ".".join([str(v) for v in self.parsed])
        else:
            if not self.raw.split(".")[-1].isnumeric():
                # TODO: allow some prereleases (only the most recent one, not too old and not when an equivalent/newer stable release is available)
                return

            try:
                self.parsed = parse_version(version)
            except Exception:
                print(f"Failed to parse version {version}")
                pass

    @property
    def major(self):
        return self.parsed[0]

    @property
    def minor(self):
        return self.parsed[1]

    @property
    def patch(self):
        return self.parsed[2] if len(self.parsed) == 3 else 0

    @property
    def valid(self):
        return self.parsed is not None

    @property
    def rendered_python_versions(self):
        return (
            "{"
            + ",".join(
                f"py{version.major}.{version.minor}" for version in self.python_versions
            )
            + "}"
        )

    def __str__(self):
        return self.raw

    def __repr__(self):
        return self.raw

    def __eq__(self, other):
        return self.parsed == other.parsed

    def __lt__(self, other):
        return self.parsed < other.parsed

    def bump_minor(self):
        return Version(f"{self.major}.{self.minor + 1}")


def fetch_package(package: str) -> dict:
    """Fetch package metadata from PYPI."""
    url = PYPI_PROJECT_URL.format(project=package)
    pypi_data = requests.get(url)

    if pypi_data.status_code != 200:
        print(f"{package} not found")

    return pypi_data.json()


def get_releases(integration: str, pypi_data: dict) -> list[Version]:
    min_supported = _MIN_VERSIONS.get(integration)
    if min_supported:
        min_supported = Version(min_supported)
        print(f"Minimum supported version for {integration} is {min_supported}.")
    else:
        print(
            f"{integration} doesn't have a minimum version. Maybe we should define one?"
        )

    versions = []

    for release, metadata in pypi_data["releases"].items():
        if not metadata:
            continue

        meta = metadata[0]
        if datetime.fromisoformat(meta["upload_time"]) < CUTOFF:
            continue

        version = Version(release, meta)
        if not version.valid:
            continue

        if min_supported and version < min_supported:
            continue

        # XXX don't consider yanked stuff

        for i, saved_version in enumerate(versions):
            if (
                version.major == saved_version.major
                and version.minor == saved_version.minor
                and version.patch > (saved_version.patch or 0)
            ):
                # don't save all patch versions of a release, just the newest one
                versions[i] = version
                break
        else:
            versions.append(version)

    return sorted(versions)


def pick_releases_to_test(releases: list[Version]) -> list[Version]:
    indexes = [
        0,  # oldest version younger than CUTOFF
        len(releases) // 3,
        len(releases) // 3 * 2,
        -1,  # latest
    ]

    filtered_releases = []

    for i in indexes:
        if releases[i] not in filtered_releases:
            filtered_releases.append(releases[i])

    return filtered_releases


def pick_python_versions_to_test(python_versions: list[Version]) -> list[Version]:
    python_versions = [
        version
        for version in python_versions
        if version >= LOWEST_SUPPORTED_PYTHON_VERSION
    ]

    if not python_versions:
        return []

    python_versions.sort()

    filtered_python_versions = [
        python_versions[0],
    ]

    if len(python_versions) > 1:
        filtered_python_versions.append(python_versions[-1])

    return filtered_python_versions


def fetch_release(package: str, version: Version) -> dict:
    url = PYPI_VERSION_URL.format(project=package, version=version)
    pypi_data = requests.get(url)

    if pypi_data.status_code != 200:
        print(f"{package} not found")

    return pypi_data.json()


def _requires_python_to_python_versions(
    lowest_requires_python: Version,
) -> list[Version]:
    if lowest_requires_python < LOWEST_SUPPORTED_PYTHON_VERSION:
        lowest_requires_python = LOWEST_SUPPORTED_PYTHON_VERSION

    versions = []
    curr = lowest_requires_python
    while True:
        if curr > HIGHEST_SUPPORTED_PYTHON_VERSION:
            break
        versions.append(curr)
        curr = curr.bump_minor()

    return versions


def determine_python_versions(pypi_data: dict) -> list[str]:
    package = pypi_data["info"]["name"]

    try:
        classifiers = pypi_data["info"]["classifiers"]
    except (AttributeError, KeyError):
        print(f"{package} has no classifiers")
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

    if not python_versions:
        try:
            requires_python = pypi_data["info"]["requires_python"]
        except (AttributeError, KeyError):
            pass

        if requires_python.startswith(">="):
            try:
                lowest = Version(requires_python[len(">=") :])
            except Exception:
                print(f"Failed to parse requires_python: {requires_python}")
            else:
                print(
                    f"Used requires_python instead of classifiers for Python versions: {lowest}"
                )
                python_versions = _requires_python_to_python_versions(lowest)

    python_versions.sort()
    return python_versions


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
    sentry_sdk_python_support = determine_python_versions(fetch_package("sentry_sdk"))
    LOWEST_SUPPORTED_PYTHON_VERSION = sentry_sdk_python_support[0]
    HIGHEST_SUPPORTED_PYTHON_VERSION = sentry_sdk_python_support[-1]
    print(
        f"The SDK supports Python versions {LOWEST_SUPPORTED_PYTHON_VERSION} to {HIGHEST_SUPPORTED_PYTHON_VERSION}."
    )

    print(INTEGRATIONS_MIN_VERSIONS)

    packages = defaultdict(list)

    for group, integrations in GROUPS.items():
        for integration in integrations:
            if integration in IGNORE:
                continue

            print(f"Processing {integration}...")
            package = DEPENDENCIES[integration][0]
            extra = None
            if "[" in package:
                extra = package[package.find("[") + 1 : package.find("]")]
                package = package[: package.find("[")]

            pypi_data = fetch_package(package)

            releases = get_releases(integration, pypi_data)
            if not releases:
                print("Found no supported releases.")
                continue

            test_releases = pick_releases_to_test(releases)
            if not test_releases:
                print(
                    "No releases recent enough or for a recent enough Python version."
                )
                continue

            for release in test_releases:
                release_pypi_data = fetch_release(package, release)
                release.python_versions = pick_python_versions_to_test(
                    determine_python_versions(release_pypi_data)
                )
                if not release.python_versions:
                    print(f"Release {release} has no Python versions, skipping.")

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
