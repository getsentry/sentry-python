import configparser
import functools
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Only consider package versions going back this far
CUTOFF = datetime.now() - timedelta(days=365 * 5)
LOWEST_SUPPORTED_PY_VERSION = "3.6"

TOX_FILE = Path(__file__).resolve().parent.parent.parent / "tox.ini"

PYPI_PROJECT_URL = "https://pypi.python.org/pypi/{project}/json"
PYPI_VERSION_URL = "https://pypi.python.org/pypi/{project}/{version}/json"

CLASSIFIER_PREFIX = "Programming Language :: Python :: "

EXCLUDE = {
    "common",
}

packages = {}


@functools.total_ordering
class Version:
    def __init__(self, version, metadata=None):
        self.raw = version
        self.metadata = metadata

        self.major = None
        self.minor = None
        self.patch = None
        self.parsed = None

        self.python_versions = []

        try:
            parsed = version.split(".")
            if len(parsed) == 3 and parsed[2].isnumeric():
                self.major, self.minor, self.patch = (int(p) for p in parsed)
            elif len(parsed) == 2 and parsed[1].isnumeric():
                self.major, self.minor = (int(p) for p in parsed)
        except Exception:
            # This will fail for e.g. prereleases, but we don't care about those
            # for now
            pass

        self.parsed = (self.major, self.minor, self.patch or 0)

    @property
    def valid(self):
        return self.major is not None and self.minor is not None

    def __str__(self):
        return self.raw

    def __repr__(self):
        return self.raw

    def __eq__(self, other):
        return self.parsed == other.parsed

    def __lt__(self, other):
        return self.parsed < other.parsed


def parse_tox():
    config = configparser.ConfigParser()
    config.read(TOX_FILE)
    lines = [
        line
        for line in config["tox"]["envlist"].split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]

    for line in lines:
        # normalize lines
        line = line.strip().lower()

        try:
            # parse tox environment definition
            try:
                (raw_python_versions, framework, framework_versions) = line.split("-")
            except ValueError:
                (raw_python_versions, framework) = line.split("-")
                framework_versions = []

            framework_versions
            packages[framework] = {}

            # collect python versions to test the framework in
            raw_python_versions = set(
                raw_python_versions.replace("{", "").replace("}", "").split(",")
            )

        except ValueError:
            print(f"ERROR reading line {line}")


def fetch_package(package: str) -> dict:
    """Fetch package metadata from PYPI."""
    url = PYPI_PROJECT_URL.format(project=package)
    pypi_data = requests.get(url)

    if pypi_data.status_code != 200:
        print(f"{package} not found")

    return pypi_data.json()


def get_releases(pypi_data: dict) -> list[Version]:
    package = pypi_data["info"]["name"]

    versions = []

    for release, metadata in pypi_data["releases"].items():
        if not metadata:
            continue

        meta = metadata[0]
        if datetime.fromisoformat(meta["upload_time"]) < CUTOFF:
            continue

        version = Version(release, meta)
        if not version.valid:
            print(
                f"Failed to parse version {release} of package {package}. Ignoring..."
            )
            continue

        versions.append(version)

    return sorted(versions)


def pick_releases_to_test(releases: list[Version]) -> list[Version]:
    indexes = [
        0,  # oldest version younger than CUTOFF
        len(releases) // 3,
        len(releases) // 3 * 2,
        -1,  # latest
    ]
    return [releases[i] for i in indexes]


def fetch_release(package: str, version: Version) -> dict:
    url = PYPI_VERSION_URL.format(project=package, version=version)
    pypi_data = requests.get(url)

    if pypi_data.status_code != 200:
        print(f"{package} not found")

    return pypi_data.json()


def determine_python_versions(
    package: str, version: Version, pypi_data: dict
) -> list[str]:
    try:
        classifiers = pypi_data["info"]["classifiers"]
    except (AttributeError, IndexError):
        print(f"{package} {version} has no classifiers")
        return []

    python_versions = []
    for classifier in classifiers:
        if classifier.startswith(CLASSIFIER_PREFIX):
            python_version = classifier[len(CLASSIFIER_PREFIX) :]
            if "." in python_version:
                # we don't care about stuff like
                # Programming Language :: Python :: 3 :: Only
                # Programming Language :: Python :: 3
                # etc., we're only interested in specific versions like 3.13
                python_versions.append(python_version)

    python_versions = [
        version
        for version in python_versions
        if Version(version) >= Version(LOWEST_SUPPORTED_PY_VERSION)
    ]

    return python_versions


def write_tox_file(package, versions):
    for version in versions:
        print(
            "{python_versions}-{package}-v{version}".format(
                python_versions=",".join([f"py{v}" for v in version.python_versions]),
                package=package,
                version=version,
            )
        )

    print()

    for version in versions:
        print(f"{package}-v{version}: {package}=={version.raw}")


if __name__ == "__main__":
    for package in ("celery", "django"):
        pypi_data = fetch_package(package)
        releases = get_releases(pypi_data)
        test_releases = pick_releases_to_test(releases)
        for release in test_releases:
            release_pypi_data = fetch_release(package, release)
            release.python_versions = determine_python_versions(
                package, release, release_pypi_data
            )
            # XXX if no supported python versions -> delete

            print(release, " on ", release.python_versions)
            time.sleep(0.1)

        print(releases)
        print(test_releases)

        write_tox_file(package, test_releases)

    print(parse_tox())
