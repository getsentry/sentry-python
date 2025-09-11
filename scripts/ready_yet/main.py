import time
import re
import sys

import requests

from collections import defaultdict

from pathlib import Path

from tox.config.cli.parse import get_options
from tox.session.state import State
from tox.config.sets import CoreConfigSet
from tox.config.source.tox_ini import ToxIni

PYTHON_VERSION = "3.13"

MATCH_LIB_SENTRY_REGEX = r"py[\d\.]*-(.*)-.*"

PYPI_PROJECT_URL = "https://pypi.python.org/pypi/{project}/json"
PYPI_VERSION_URL = "https://pypi.python.org/pypi/{project}/{version}/json"


def get_tox_envs(tox_ini_path: Path) -> list:
    tox_ini = ToxIni(tox_ini_path)
    conf = State(get_options(), []).conf
    tox_section = next(tox_ini.sections())
    core_config_set = CoreConfigSet(
        conf, tox_section, tox_ini_path.parent, tox_ini_path
    )
    (
        core_config_set.loaders.extend(
            tox_ini.get_loaders(
                tox_section,
                base=[],
                override_map=defaultdict(list, {}),
                conf=core_config_set,
            )
        )
    )
    return core_config_set.load("env_list")


def get_libs(tox_ini: Path, regex: str) -> set:
    libs = set()
    for env in get_tox_envs(tox_ini):
        match = re.match(regex, env)
        if match:
            libs.add(match.group(1))

    return sorted(libs)


def main():
    """
    Check if libraries in our tox.ini are ready for Python version defined in `PYTHON_VERSION`.
    """
    print(f"Checking libs from tox.ini for Python {PYTHON_VERSION} compatibility:")
    
    ready = set()
    not_ready = set()
    not_found = set()

    tox_ini = Path(__file__).parent.parent.parent.joinpath("tox.ini")

    libs = get_libs(tox_ini, MATCH_LIB_SENTRY_REGEX)

    for lib in libs:
        print(".", end="")
        sys.stdout.flush()

        # Get latest version of lib
        url = PYPI_PROJECT_URL.format(project=lib)
        pypi_data = requests.get(url)

        if pypi_data.status_code != 200:
            not_found.add(lib)
            continue

        latest_version = pypi_data.json()["info"]["version"]

        # Get supported Python version of latest version of lib
        url = PYPI_PROJECT_URL.format(project=lib, version=latest_version)
        pypi_data = requests.get(url)

        if pypi_data.status_code != 200:
            continue

        classifiers = pypi_data.json()["info"]["classifiers"]

        if f"Programming Language :: Python :: {PYTHON_VERSION}" in classifiers:
            ready.add(lib)
        else:
            not_ready.add(lib)

        # cut pypi some slack
        time.sleep(0.1)

    # Print report
    print("\n")
    print(f"\nReady for Python {PYTHON_VERSION}:")
    if len(ready) == 0:
        print("- None ")

    for x in sorted(ready):
        print(f"- {x}")

    print(f"\nNOT ready for Python {PYTHON_VERSION}:")
    if len(not_ready) == 0:
        print("- None ")

    for x in sorted(not_ready):
        print(f"- {x}")

    print("\nNot found on PyPI:")
    if len(not_found) == 0:
        print("- None ")

    for x in sorted(not_found):
        print(f"- {x}")


if __name__ == "__main__":
    main()
