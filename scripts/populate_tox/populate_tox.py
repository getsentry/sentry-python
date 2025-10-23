"""
This script populates tox.ini automatically using release data from PyPI.

See scripts/populate_tox/README.md for more info.
"""

import functools
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone  # noqa: F401
from importlib.metadata import PackageMetadata, distributions
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pathlib import Path
from textwrap import dedent
from typing import Optional, Union

# Adding the scripts directory to PATH. This is necessary in order to be able
# to import stuff from the split_tox_gh_actions script
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import requests
from jinja2 import Environment, FileSystemLoader
from sentry_sdk.integrations import _MIN_VERSIONS

from config import TEST_SUITE_CONFIG
from split_tox_gh_actions.split_tox_gh_actions import GROUPS


# Set CUTOFF this to a datetime to ignore packages older than CUTOFF
CUTOFF = None
# CUTOFF = datetime.now(tz=timezone.utc) - timedelta(days=365 * 5)

TOX_FILE = Path(__file__).resolve().parent.parent.parent / "tox.ini"
RELEASES_CACHE_FILE = Path(__file__).resolve().parent / "releases.jsonl"
ENV = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent),
    trim_blocks=True,
    lstrip_blocks=True,
)

PYPI_COOLDOWN = 0.05  # seconds to wait between requests to PyPI

PYPI_PROJECT_URL = "https://pypi.python.org/pypi/{project}/json"
PYPI_VERSION_URL = "https://pypi.python.org/pypi/{project}/{version}/json"
CLASSIFIER_PREFIX = "Programming Language :: Python :: "

CACHE = defaultdict(dict)

IGNORE = {
    # Do not try auto-generating the tox entries for these. They will be
    # hardcoded in tox.ini since they don't fit the toxgen usecase (there is no
    # one package that should be tested in different versions).
    "asgi",
    "aws_lambda",
    "cloud_resource_context",
    "common",
    "gcp",
    "gevent",
    "opentelemetry",
    "potel",
}


@dataclass(order=True)
class ThreadedVersion:
    version: Version
    no_gil: bool

    def __init__(self, version: str | Version, no_gil=False):
        self.version = Version(version) if isinstance(version, str) else version
        self.no_gil = no_gil

    def __str__(self):
        version = f"py{self.version.major}.{self.version.minor}"
        if self.no_gil:
            version += "t"

        return version


# Free-threading is experimentally supported in 3.13, and officially supported in 3.14.
MIN_FREE_THREADING_SUPPORT = ThreadedVersion("3.14")


def _fetch_sdk_metadata() -> PackageMetadata:
    (dist,) = distributions(
        name="sentry-sdk", path=[Path(__file__).parent.parent.parent]
    )
    return dist.metadata


def fetch_url(url: str) -> Optional[dict]:
    for attempt in range(3):
        pypi_data = requests.get(url)

        if pypi_data.status_code == 200:
            return pypi_data.json()

        backoff = PYPI_COOLDOWN * 2**attempt
        print(
            f"{url} returned an error: {pypi_data.status_code}. Attempt {attempt + 1}/3. Waiting {backoff}s"
        )
        time.sleep(backoff)

    return None


@functools.cache
def fetch_package(package: str) -> Optional[dict]:
    """Fetch package metadata from PyPI."""
    url = PYPI_PROJECT_URL.format(project=package)
    return fetch_url(url)


@functools.cache
def fetch_release(package: str, version: Version) -> Optional[dict]:
    """Fetch release metadata from cache or, failing that, PyPI."""
    release = _fetch_from_cache(package, version)
    if release is not None:
        return release

    url = PYPI_VERSION_URL.format(project=package, version=version)
    release = fetch_url(url)
    if release is not None:
        _save_to_cache(package, version, release)
    return release


def _fetch_from_cache(package: str, version: Version) -> Optional[dict]:
    package = _normalize_name(package)
    if package in CACHE and str(version) in CACHE[package]:
        CACHE[package][str(version)]["_accessed"] = True
        return CACHE[package][str(version)]

    return None


def _save_to_cache(package: str, version: Version, release: Optional[dict]) -> None:
    with open(RELEASES_CACHE_FILE, "a") as releases_cache:
        releases_cache.write(json.dumps(_normalize_release(release)) + "\n")

    CACHE[_normalize_name(package)][str(version)] = release
    CACHE[_normalize_name(package)][str(version)]["_accessed"] = True


def _prefilter_releases(
    integration: str,
    releases: dict[str, dict],
) -> tuple[list[Version], Optional[Version]]:
    """
    Filter `releases`, removing releases that are for sure unsupported.

    This function doesn't guarantee that all releases it returns are supported --
    there are further criteria that will be checked later in the pipeline because
    they require additional API calls to be made. The purpose of this function is
    to slim down the list so that we don't have to make more API calls than
    necessary for releases that are for sure not supported.

    The function returns a tuple with:
    - the list of prefiltered releases
    - an optional prerelease if there is one that should be tested
    """
    integration_name = (
        TEST_SUITE_CONFIG[integration].get("integration_name") or integration
    )

    min_supported = _MIN_VERSIONS.get(integration_name)
    if min_supported is not None:
        min_supported = Version(".".join(map(str, min_supported)))
    else:
        print(
            f"  {integration} doesn't have a minimum version defined in "
            f"sentry_sdk/integrations/__init__.py. Consider defining one"
        )

    include_versions = None
    if TEST_SUITE_CONFIG[integration].get("include") is not None:
        include_versions = SpecifierSet(
            TEST_SUITE_CONFIG[integration]["include"], prereleases=True
        )

    filtered_releases = []
    last_prerelease = None

    for release, data in releases.items():
        if not data:
            continue

        meta = data[0]

        if meta["yanked"]:
            continue

        uploaded = datetime.fromisoformat(meta["upload_time_iso_8601"])

        if CUTOFF is not None and uploaded < CUTOFF:
            continue

        version = Version(release)

        if min_supported and version < min_supported:
            continue

        if version.is_postrelease or version.is_devrelease:
            continue

        if include_versions is not None and version not in include_versions:
            continue

        if version.is_prerelease:
            if last_prerelease is None or version > last_prerelease:
                last_prerelease = version
            continue

        for i, saved_version in enumerate(filtered_releases):
            if (
                version.major == saved_version.major
                and version.minor == saved_version.minor
            ):
                # Don't save all patch versions of a release, just the newest one
                if version.micro > saved_version.micro:
                    filtered_releases[i] = version
                break
        else:
            filtered_releases.append(version)

    filtered_releases.sort()

    # Check if the latest prerelease is relevant (i.e., it's for a version higher
    # than the last released version); if not, don't consider it
    if last_prerelease is not None:
        if not filtered_releases or last_prerelease > filtered_releases[-1]:
            return filtered_releases, last_prerelease

    return filtered_releases, None


def get_supported_releases(
    integration: str, pypi_data: dict
) -> tuple[list[Version], Optional[Version]]:
    """
    Get a list of releases that are currently supported by the SDK.

    This takes into account a handful of parameters (Python support, the lowest
    supported version we've defined for the framework, optionally the date
    of the release).

    We return the list of supported releases and optionally also the newest
    prerelease, if it should be tested (meaning it's for a version higher than
    the current stable version).
    """
    package = pypi_data["info"]["name"]

    # Get a consolidated list without taking into account Python support yet
    # (because that might require an additional API call for some
    # of the releases)
    releases, latest_prerelease = _prefilter_releases(
        integration,
        pypi_data["releases"],
    )

    def _supports_lowest(release: Version) -> bool:
        time.sleep(PYPI_COOLDOWN)  # don't DoS PYPI

        pypi_data = fetch_release(package, release)
        if pypi_data is None:
            print("Failed to fetch necessary data from PyPI. Aborting.")
            sys.exit(1)

        py_versions = determine_python_versions(pypi_data)
        target_python_versions = _transform_target_python_versions(
            TEST_SUITE_CONFIG[integration].get("python")
        )
        return bool(supported_python_versions(py_versions, target_python_versions))

    if not _supports_lowest(releases[0]):
        i = bisect_left(releases, True, key=_supports_lowest)
        if i != len(releases) and _supports_lowest(releases[i]):
            # we found the lowest version that supports at least some Python
            # version(s) that we do, cut off the rest
            releases = releases[i:]

    return releases, latest_prerelease


def pick_releases_to_test(
    integration: str, releases: list[Version], last_prerelease: Optional[Version]
) -> list[Version]:
    """Pick a handful of releases to test from a sorted list of supported releases."""
    # If the package has majors (or major-like releases, even if they don't do
    # semver), we want to make sure we're testing them all. If it doesn't have
    # multiple majors, we just pick the oldest, the newest, and a couple of
    # releases in between.
    #
    # If there is a relevant prerelease, also test that in addition to the above.
    num_versions = TEST_SUITE_CONFIG[integration].get("num_versions")
    if num_versions is not None and (
        not isinstance(num_versions, int) or num_versions < 2
    ):
        print("  Integration has invalid `num_versions`: must be an int >= 2")
        num_versions = None

    has_majors = len({v.major for v in releases}) > 1
    filtered_releases = set()

    if has_majors:
        # Always check the very first supported release
        filtered_releases.add(releases[0])

        # Find out the max release by each major
        releases_by_major = {}
        for release in releases:
            if (
                release.major not in releases_by_major
                or release > releases_by_major[release.major]
            ):
                releases_by_major[release.major] = release

        # Add the highest release in each major
        for max_version in releases_by_major.values():
            filtered_releases.add(max_version)

        # If num_versions was provided, slim down the selection
        if num_versions is not None:
            filtered_releases = _pick_releases(sorted(filtered_releases), num_versions)

    else:
        filtered_releases = _pick_releases(releases, num_versions)

    filtered_releases = sorted(filtered_releases)
    if last_prerelease is not None:
        filtered_releases.append(last_prerelease)

    return filtered_releases


def _pick_releases(
    releases: list[Version], num_versions: Optional[int]
) -> set[Version]:
    num_versions = num_versions or 4

    versions = {
        releases[0],  # oldest version supported
        releases[-1],  # latest
    }

    for i in range(1, num_versions - 1):
        try:
            versions.add(releases[len(releases) // (num_versions - 1) * i])
        except IndexError:
            pass

    return versions


def supported_python_versions(
    package_python_versions: Union[SpecifierSet, list[Version]],
    custom_supported_versions: Optional[
        Union[SpecifierSet, dict[SpecifierSet, SpecifierSet]]
    ] = None,
    release_version: Optional[Version] = None,
) -> list[Version]:
    """
    Get the intersection of Python versions supported by the package and the SDK.

    Optionally, if `custom_supported_versions` is provided, the function will
    return the intersection of Python versions supported by the package, the SDK,
    and `custom_supported_versions`. This is used when a test suite definition
    in `TEST_SUITE_CONFIG` contains a range of Python versions to run the tests
    on.

    Examples:
    - The Python SDK supports Python 3.6-3.13. The package supports 3.5-3.8. This
      function will return [3.6, 3.7, 3.8] as the Python versions supported
      by both.
    - The Python SDK supports Python 3.6-3.13. The package supports 3.5-3.8. We
      have an additional test limitation in place to only test this framework
      on Python 3.7, so we can provide this as `custom_supported_versions`. The
      result of this function will then by the intersection of all three, i.e.,
      [3.7].
    - The Python SDK supports Python 3.6-3.13. The package supports 3.5-3.8.
      Additionally, we have a limitation in place to only test this framework on
      Python 3.5 if the framework version is <2.0. `custom_supported_versions`
      will contain this restriction, and `release_version` will contain the
      version of the package we're currently looking at, to determine whether the
      <2.0 restriction applies in this case.
    """
    supported = []

    # Iterate through Python versions from MIN_PYTHON_VERSION to MAX_PYTHON_VERSION
    curr = MIN_PYTHON_VERSION
    while curr <= MAX_PYTHON_VERSION:
        if curr in package_python_versions:
            if not custom_supported_versions:
                supported.append(curr)

            else:
                if isinstance(custom_supported_versions, SpecifierSet):
                    if curr in custom_supported_versions:
                        supported.append(curr)

                elif release_version is not None and isinstance(
                    custom_supported_versions, dict
                ):
                    for v, py in custom_supported_versions.items():
                        if release_version in v:
                            if curr in py:
                                supported.append(curr)
                            break
                    else:
                        supported.append(curr)

        # Construct the next Python version (i.e., bump the minor)
        next = [int(v) for v in str(curr).split(".")]
        next[1] += 1
        curr = Version(".".join(map(str, next)))

    return supported


def pick_python_versions_to_test(
    python_versions: list[Version], has_free_threading_wheel: bool
) -> list[Version]:
    """
    Given a list of Python versions, pick those that make sense to test on.

    Currently, this is the oldest, the newest, and the second newest Python
    version.

    the free-threaded variant of the newest version is also selected if
    - a free-threaded wheel is distributed; and
    - the SDK supports free-threading on the newest supported version.
    """
    filtered_python_versions = {
        python_versions[0],
    }

    filtered_python_versions.add(python_versions[-1])
    try:
        filtered_python_versions.add(python_versions[-2])
    except IndexError:
        pass

    versions_to_test = sorted(
        ThreadedVersion(version) for version in filtered_python_versions
    )

    if has_free_threading_wheel and versions_to_test[-1] >= MIN_FREE_THREADING_SUPPORT:
        versions_to_test.append(
            ThreadedVersion(versions_to_test[-1].version, no_gil=True)
        )

    return versions_to_test


def _parse_python_versions_from_classifiers(classifiers: list[str]) -> list[Version]:
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


def determine_python_versions(pypi_data: dict) -> Union[SpecifierSet, list[Version]]:
    """
    Determine the Python versions supported by the package from PyPI data.

    We're looking at Python version classifiers, if present, and
    `requires_python` if there are no classifiers.
    """
    try:
        classifiers = pypi_data["info"]["classifiers"]
    except (AttributeError, KeyError):
        # This function assumes `pypi_data` contains classifiers. This is the case
        # for the most recent release in the /{project} endpoint or for any release
        # fetched via the /{project}/{version} endpoint.
        return []

    # Try parsing classifiers
    python_versions = _parse_python_versions_from_classifiers(classifiers)
    if python_versions:
        return python_versions

    # We only use `requires_python` if there are no classifiers. This is because
    # `requires_python` doesn't tell us anything about the upper bound, which
    # implicitly depends on when the release first came out.
    try:
        requires_python = pypi_data["info"]["requires_python"]
    except (AttributeError, KeyError):
        pass

    if requires_python:
        return SpecifierSet(requires_python)

    return []


def has_free_threading_wheel(pypi_data: dict) -> bool:
    for download in pypi_data["urls"]:
        print(download)

        if download["packagetype"] == "bdist_wheel":
            abi_tag = download["filename"].removesuffix(".whl").split("-")[-2]

            if abi_tag == "none" or (
                abi_tag.endswith("t") and abi_tag.startswith("cp314")
            ):
                return True

    return False


def _render_python_versions(python_versions: list[ThreadedVersion]) -> str:
    return "{" + ",".join(str(version) for version in python_versions) + "}"


def _render_dependencies(integration: str, releases: list[Version]) -> list[str]:
    rendered = []

    if TEST_SUITE_CONFIG[integration].get("deps") is None:
        return rendered

    for constraint, deps in TEST_SUITE_CONFIG[integration]["deps"].items():
        if constraint == "*":
            for dep in deps:
                rendered.append(f"{integration}: {dep}")
        elif constraint.startswith("py3"):
            for dep in deps:
                rendered.append(f"{{{constraint}}}-{integration}: {dep}")
        else:
            restriction = SpecifierSet(constraint, prereleases=True)
            for release in releases:
                if release in restriction:
                    for dep in deps:
                        rendered.append(f"{integration}-v{release}: {dep}")

    return rendered


def write_tox_file(packages: dict) -> None:
    template = ENV.get_template("tox.jinja")

    context = {
        "groups": {},
        "testpaths": [],
    }

    for group, integrations in packages.items():
        context["groups"][group] = []
        for integration in integrations:
            context["groups"][group].append(
                {
                    "name": integration["name"],
                    "package": integration["package"],
                    "extra": integration["extra"],
                    "releases": integration["releases"],
                    "dependencies": _render_dependencies(
                        integration["name"], integration["releases"]
                    ),
                }
            )
            context["testpaths"].append(
                (
                    integration["name"],
                    f"tests/integrations/{integration['integration_name']}",
                )
            )

    context["testpaths"].sort()

    rendered = template.render(context)

    with open(TOX_FILE, "w") as file:
        file.write(rendered)
        file.write("\n")


def _get_package_name(integration: str) -> tuple[str, Optional[str]]:
    package = TEST_SUITE_CONFIG[integration]["package"]
    extra = None
    if "[" in package:
        extra = package[package.find("[") + 1 : package.find("]")]
        package = package[: package.find("[")]

    return package, extra


def _compare_min_version_with_defined(
    integration: str, releases: list[Version]
) -> None:
    defined_min_version = _MIN_VERSIONS.get(integration)
    if defined_min_version:
        defined_min_version = Version(".".join([str(v) for v in defined_min_version]))
        if (
            defined_min_version.major != releases[0].major
            or defined_min_version.minor != releases[0].minor
        ):
            print(
                f"  Integration defines {defined_min_version} as minimum "
                f"version, but the effective minimum version based on metadata "
                f"is {releases[0]}."
            )


def _add_python_versions_to_release(
    integration: str, package: str, release: Version
) -> None:
    release_pypi_data = fetch_release(package, release)
    if release_pypi_data is None:
        print("Failed to fetch necessary data from PyPI. Aborting.")
        sys.exit(1)

    time.sleep(PYPI_COOLDOWN)  # give PYPI some breathing room

    target_python_versions = _transform_target_python_versions(
        TEST_SUITE_CONFIG[integration].get("python")
    )

    release.python_versions = pick_python_versions_to_test(
        supported_python_versions(
            determine_python_versions(release_pypi_data),
            target_python_versions,
            release,
        ),
        has_free_threading_wheel(release_pypi_data),
    )

    release.rendered_python_versions = _render_python_versions(release.python_versions)


def _transform_target_python_versions(
    python_versions: Union[str, dict[str, str], None],
) -> Union[SpecifierSet, dict[SpecifierSet, SpecifierSet], None]:
    """Wrap the contents of the `python` key in SpecifierSets."""
    if not python_versions:
        return None

    if isinstance(python_versions, str):
        return SpecifierSet(python_versions)

    if isinstance(python_versions, dict):
        updated = {}
        for key, value in python_versions.items():
            updated[SpecifierSet(key)] = SpecifierSet(value)
        return updated


def get_file_hash() -> str:
    """Calculate a hash of the tox.ini file."""
    hasher = hashlib.md5()

    with open(TOX_FILE, "rb") as f:
        buf = f.read()
        hasher.update(buf)

    return hasher.hexdigest()


def get_last_updated() -> Optional[datetime]:
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    tox_ini_path = Path(repo_root) / "tox.ini"

    timestamp = subprocess.run(
        ["git", "log", "-1", "--pretty=%ct", str(tox_ini_path)],
        capture_output=True,
        text=True,
    ).stdout.strip()

    timestamp = datetime.fromtimestamp(int(timestamp), timezone.utc)
    print(f"Last committed tox.ini update: {timestamp}")
    return timestamp


def _normalize_name(package: str) -> str:
    return package.lower().replace("-", "_")


def _extract_wheel_info_to_cache(wheel: dict):
    return {
        "packagetype": wheel["packagetype"],
        "filename": wheel["filename"],
    }


def _normalize_release(release: dict) -> dict:
    """Filter out unneeded parts of the release JSON."""
    urls = [_extract_wheel_info_to_cache(wheel) for wheel in release["urls"]]
    normalized = {
        "info": {
            "classifiers": release["info"]["classifiers"],
            "name": release["info"]["name"],
            "requires_python": release["info"]["requires_python"],
            "version": release["info"]["version"],
            "yanked": release["info"]["yanked"],
        },
        "urls": urls,
    }
    return normalized


def main() -> dict[str, list]:
    """
    Generate tox.ini from the tox.jinja template.
    """
    global MIN_PYTHON_VERSION, MAX_PYTHON_VERSION
    meta = _fetch_sdk_metadata()
    sdk_python_versions = _parse_python_versions_from_classifiers(
        meta.get_all("Classifier")
    )
    MIN_PYTHON_VERSION = sdk_python_versions[0]
    MAX_PYTHON_VERSION = sdk_python_versions[-1]
    print(
        f"The SDK supports Python versions {MIN_PYTHON_VERSION} - {MAX_PYTHON_VERSION}."
    )

    # Load file cache
    global CACHE

    with open(RELEASES_CACHE_FILE) as releases_cache:
        for line in releases_cache:
            release = json.loads(line)
            name = _normalize_name(release["info"]["name"])
            version = release["info"]["version"]
            CACHE[name][version] = release
            CACHE[name][version]["_accessed"] = (
                False  # for cleaning up unused cache entries
            )

    # Process packages
    packages = defaultdict(list)

    for group, integrations in GROUPS.items():
        for integration in integrations:
            if integration in IGNORE:
                continue

            print(f"Processing {integration}...")

            # Figure out the actual main package
            package, extra = _get_package_name(integration)

            # Fetch data for the main package
            pypi_data = fetch_package(package)
            if pypi_data is None:
                print("Failed to fetch necessary data from PyPI. Aborting.")
                sys.exit(1)

            # Get the list of all supported releases

            releases, latest_prerelease = get_supported_releases(integration, pypi_data)

            if not releases:
                print("  Found no supported releases.")
                continue

            _compare_min_version_with_defined(integration, releases)

            # Pick a handful of the supported releases to actually test against
            # and fetch the PyPI data for each to determine which Python versions
            # to test it on
            test_releases = pick_releases_to_test(
                integration, releases, latest_prerelease
            )

            for release in test_releases:
                _add_python_versions_to_release(integration, package, release)
                if not release.python_versions:
                    print(f"  Release {release} has no Python versions, skipping.")

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
                        "integration_name": TEST_SUITE_CONFIG[integration].get(
                            "integration_name"
                        )
                        or integration,
                    }
                )

    write_tox_file(packages)

    # Sort the release cache file
    releases = []
    with open(RELEASES_CACHE_FILE) as releases_cache:
        releases = [json.loads(line) for line in releases_cache]
    releases.sort(key=lambda r: (r["info"]["name"], r["info"]["version"]))
    with open(RELEASES_CACHE_FILE, "w") as releases_cache:
        for release in releases:
            if (
                CACHE[_normalize_name(release["info"]["name"])][
                    release["info"]["version"]
                ]["_accessed"]
                is True
            ):
                releases_cache.write(json.dumps(release) + "\n")

    print(
        "Done generating tox.ini. Make sure to also update the CI YAML "
        "files by executing split_tox_gh_actions.py."
    )

    return packages


if __name__ == "__main__":
    main()
