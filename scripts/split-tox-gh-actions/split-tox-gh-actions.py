"""Split Tox to GitHub Actions

This is a small script to split a tox.ini config file into multiple GitHub actions configuration files.
This way each framework defined in tox.ini will get its own GitHub actions configuration file
which allows them to be run in parallel in GitHub actions.

This will generate/update several configuration files, that need to be commited to Git afterwards.
Whenever tox.ini is changed, this script needs to be run.

Usage:
    python split-tox-gh-actions.py [--fail-on-changes]

If the parameter `--fail-on-changes` is set, the script will raise a RuntimeError in case the yaml
files have been changed by the scripts execution. This is used in CI to check if the yaml files
represent the current tox.ini file. (And if not the CI run fails.)
"""

import configparser
import hashlib
import sys
from collections import defaultdict
from glob import glob
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows"
TOX_FILE = Path(__file__).resolve().parent.parent.parent / "tox.ini"
TEMPLATE_DIR = Path(__file__).resolve().parent
TEMPLATE_FILE = TEMPLATE_DIR / "ci-yaml.txt"
TEMPLATE_FILE_SERVICES = TEMPLATE_DIR / "ci-yaml-services.txt"

FRAMEWORKS_NEEDING_POSTGRES = ["django"]

MATRIX_DEFINITION = """
    strategy:
      matrix:
        python-version: [{{ python-version }}]
        os: [ubuntu-latest]
"""


def write_yaml_file(
    template,
    current_framework,
    python_versions,
):
    """Write the YAML configuration file for one framework to disk."""
    # render template for print
    out = ""
    for template_line in template:
        if template_line == "{{ strategy_matrix }}\n":
            py_versions = [f'"{py.replace("py", "")}"' for py in python_versions]

            m = MATRIX_DEFINITION
            m = m.replace("{{ framework }}", current_framework).replace(
                "{{ python-version }}", ",".join(py_versions)
            )
            out += m

        elif template_line == "{{ services }}\n":
            if current_framework in FRAMEWORKS_NEEDING_POSTGRES:
                f = open(TEMPLATE_FILE_SERVICES, "r")
                out += "".join(f.readlines())
                f.close()

        else:
            out += template_line.replace("{{ framework }}", current_framework)

    # write rendered template
    outfile_name = OUT_DIR / f"test-integration-{current_framework}.yml"
    print(f"Writing {outfile_name}")
    f = open(outfile_name, "w")
    f.writelines(out)
    f.close()


def get_yaml_files_hash():
    """Calculate a hash of all the yaml configuration files"""

    hasher = hashlib.md5()
    path_pattern = (OUT_DIR / f"test-integration-*.yml").as_posix()
    for file in glob(path_pattern):
        with open(file, "rb") as f:
            buf = f.read()
            hasher.update(buf)

    return hasher.hexdigest()


def main(fail_on_changes):
    """Create one CI workflow for each framework defined in tox.ini"""
    if fail_on_changes:
        old_hash = get_yaml_files_hash()

    print("Read GitHub actions config file template")
    f = open(TEMPLATE_FILE, "r")
    template = f.readlines()
    f.close()

    print("Read tox.ini")
    config = configparser.ConfigParser()
    config.read(TOX_FILE)
    lines = [x for x in config["tox"]["envlist"].split("\n") if len(x) > 0]

    python_versions = defaultdict(list)

    print("Parse tox.ini nevlist")

    for line in lines:
        # normalize lines
        line = line.strip().lower()

        # ignore comments
        if line.startswith("#"):
            continue

        try:
            # parse tox environment definition
            try:
                (raw_python_versions, framework, _) = line.split("-")
            except ValueError:
                (raw_python_versions, framework) = line.split("-")

            # collect python versions to test the framework in
            for python_version in (
                raw_python_versions.replace("{", "").replace("}", "").split(",")
            ):
                if python_version not in python_versions[framework]:
                    python_versions[framework].append(python_version)

        except ValueError as err:
            print(f"ERROR reading line {line}")

    for framework in python_versions:
        write_yaml_file(template, framework, python_versions[framework])

    if fail_on_changes:
        new_hash = get_yaml_files_hash()

        if old_hash != new_hash:
            raise RuntimeError(
                "The yaml configuration files have changed. This means that tox.ini has changed "
                "but the changes have not been propagated to the GitHub actions config files. "
                "Please run `python scripts/split-tox-gh-actions/split-tox-gh-actions.py` "
                "locally and commit the changes of the yaml configuration files to continue. "
            )

    print("All done. Have a nice day!")


if __name__ == "__main__":
    fail_on_changes = (
        True if len(sys.argv) == 2 and sys.argv[1] == "--fail-on-changes" else False
    )
    main(fail_on_changes)
