import configparser
import functools
from datetime import datetime, timedelta
from pathlib import Path

import requests

from ..split_tox_gh_actions.split_tox_gh_actions import GROUPS

print(GROUPS)

# Only consider package versions going back this far
CUTOFF = datetime.now() - timedelta(days=365 * 3)
LOWEST_SUPPORTED_PY_VERSION = "3.6"

TOX_FILE = Path(__file__).resolve().parent.parent.parent / "tox.ini"

PYPI_PROJECT_URL = "https://pypi.python.org/pypi/{project}/json"
PYPI_VERSION_URL = "https://pypi.python.org/pypi/{project}/{version}/json"

EXCLUDE = {
    "common",
}

packages = {}


@functools.total_ordering
class Version:
    def __init__(self, version, metadata):
        self.raw = version
        self.metadata = metadata

        self.major = None
        self.minor = None
        self.patch = None
        self.parsed = None

        try:
            parsed = version.split(".")
            if parsed[2].isnumeric():
                self.major, self.minor, self.patch = (int(p) for p in parsed)
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


def fetch_metadata(package):
    url = PYPI_PROJECT_URL.format(project=package)
    pypi_data = requests.get(url)

    if pypi_data.status_code != 200:
        print(f"{package} not found")

    import pprint

    pprint.pprint(package)
    pprint.pprint(pypi_data.json())
    return pypi_data.json()


def parse_metadata(data):
    package = data["info"]["name"]

    majors = {}

    for release, metadata in data["releases"].items():
        meta = metadata[0]
        if datetime.fromisoformat(meta["upload_time"]) < CUTOFF:
            continue

        version = Version(release, meta)
        if not version.valid:
            print(f"Failed to parse version {release} of package {package}")
            continue

        if version.major not in majors:
            # 0 -> [min 0.x version, max 0.x version]
            majors[version.major] = [version, version]
            continue

        if version < majors[version.major][0]:
            majors[version.major][0] = version
        if version > majors[version.major][1]:
            majors[version.major][1] = version

        print(release, "not too old", meta["upload_time"])

    return majors


print(parse_tox())
print(packages)
print(parse_metadata(fetch_metadata("celery")))
