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
TEMPLATE_FILE_SETUP_DB = TEMPLATE_DIR / "ci-yaml-setup-db.txt"
TEMPLATE_FILE_AWS_CREDENTIALS = TEMPLATE_DIR / "ci-yaml-aws-credentials.txt"
TEMPLATE_SNIPPET_TEST = TEMPLATE_DIR / "ci-yaml-test-snippet.txt"
TEMPLATE_SNIPPET_TEST_PY27 = TEMPLATE_DIR / "ci-yaml-test-py27-snippet.txt"
TEMPLATE_SNIPPET_TEST_LATEST = TEMPLATE_DIR / "ci-yaml-test-latest-snippet.txt"

FRAMEWORKS_NEEDING_POSTGRES = [
    "django",
    "asyncpg",
]

FRAMEWORKS_NEEDING_CLICKHOUSE = [
    "clickhouse_driver",
]

FRAMEWORKS_NEEDING_AWS = [
    "aws_lambda",
]

MATRIX_DEFINITION = """
    strategy:
      fail-fast: false
      matrix:
        python-version: [{{ python-version }}]
        # python3.6 reached EOL and is no longer being supported on
        # new versions of hosted runners on Github Actions
        # ubuntu-20.04 is the last version that supported python3.6
        # see https://github.com/actions/setup-python/issues/544#issuecomment-1332535877
        os: [ubuntu-20.04]
"""

ADDITIONAL_USES_CLICKHOUSE = """\

      - uses: getsentry/action-clickhouse-in-ci@v1
"""

CHECK_NEEDS = """\
    needs: test
"""
CHECK_NEEDS_PY27 = """\
    needs: [test, test-py27]
"""

CHECK_PY27 = """\
      - name: Check for 2.7 failures
        if: contains(needs.test-py27.result, 'failure')
        run: |
          echo "One of the dependent jobs has failed. You may need to re-run it." && exit 1
"""


def write_yaml_file(
    template,
    current_framework,
    python_versions,
    python_versions_latest,
):
    """Write the YAML configuration file for one framework to disk."""
    py_versions = sorted(
        [py.replace("py", "") for py in python_versions],
        key=lambda v: tuple(map(int, v.split("."))),
    )
    py27_supported = "2.7" in py_versions
    py_versions_latest = sorted(
        [py.replace("py", "") for py in python_versions_latest],
        key=lambda v: tuple(map(int, v.split("."))),
    )

    test_loc = template.index("{{ test }}\n")
    f = open(TEMPLATE_SNIPPET_TEST, "r")
    test_snippet = f.readlines()
    template = template[:test_loc] + test_snippet + template[test_loc + 1 :]
    f.close()

    test_py27_loc = template.index("{{ test_py27 }}\n")
    if py27_supported:
        f = open(TEMPLATE_SNIPPET_TEST_PY27, "r")
        test_py27_snippet = f.readlines()
        template = (
            template[:test_py27_loc] + test_py27_snippet + template[test_py27_loc + 1 :]
        )
        f.close()

        py_versions.remove("2.7")
    else:
        template.pop(test_py27_loc)

    test_latest_loc = template.index("{{ test_latest }}\n")
    if python_versions_latest:
        f = open(TEMPLATE_SNIPPET_TEST_LATEST, "r")
        test_latest_snippet = f.readlines()
        template = (
            template[:test_latest_loc]
            + test_latest_snippet
            + template[test_latest_loc + 1 :]
        )
        f.close()
    else:
        template.pop(test_latest_loc)

    out = ""
    py27_test_part = False
    for template_line in template:
        if template_line.strip() == "{{ strategy_matrix }}":
            m = MATRIX_DEFINITION
            m = m.replace("{{ framework }}", current_framework).replace(
                "{{ python-version }}", ",".join([f'"{v}"' for v in py_versions])
            )
            out += m

        elif template_line.strip() == "{{ strategy_matrix_latest }}":
            m = MATRIX_DEFINITION
            m = m.replace("{{ framework }}", current_framework).replace(
                "{{ python-version }}", ",".join([f'"{v}"' for v in py_versions_latest])
            )
            out += m

        elif template_line.strip() in ("{{ services }}", "{{ services_latest }}"):
            if current_framework in FRAMEWORKS_NEEDING_POSTGRES:
                f = open(TEMPLATE_FILE_SERVICES, "r")
                lines = [
                    line.replace(
                        "{{ postgres_host }}",
                        "postgres"
                        if py27_test_part and "_latest" not in template_line
                        else "localhost",
                    )
                    for line in f.readlines()
                ]
                out += "".join(lines)
                f.close()

        elif template_line.strip() == "{{ setup_postgres }}":
            if current_framework in FRAMEWORKS_NEEDING_POSTGRES:
                f = open(TEMPLATE_FILE_SETUP_DB, "r")
                out += "".join(f.readlines())

        elif template_line.strip() == "{{ aws_credentials }}":
            if current_framework in FRAMEWORKS_NEEDING_AWS:
                f = open(TEMPLATE_FILE_AWS_CREDENTIALS, "r")
                out += "".join(f.readlines())

        elif template_line.strip() == "{{ additional_uses }}":
            if current_framework in FRAMEWORKS_NEEDING_CLICKHOUSE:
                out += ADDITIONAL_USES_CLICKHOUSE

        elif template_line.strip() == "{{ check_needs }}":
            if py27_supported:
                out += CHECK_NEEDS_PY27
            else:
                out += CHECK_NEEDS

        elif template_line.strip() == "{{ check_py27 }}":
            if py27_supported:
                out += CHECK_PY27

        else:
            if template_line.strip() == "test-py27:":
                py27_test_part = True

            out += template_line.replace("{{ framework }}", current_framework)

    # write rendered template
    if current_framework == "common":
        outfile_name = OUT_DIR / f"test-{current_framework}.yml"
    else:
        outfile_name = OUT_DIR / f"test-integration-{current_framework}.yml"

    print(f"Writing {outfile_name}")
    f = open(outfile_name, "w")
    f.writelines(out)
    f.close()


def get_yaml_files_hash():
    """Calculate a hash of all the yaml configuration files"""

    hasher = hashlib.md5()
    path_pattern = (OUT_DIR / "test-integration-*.yml").as_posix()
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

    python_versions = defaultdict(set)
    python_versions_latest = defaultdict(set)

    print("Parse tox.ini envlist")

    for line in lines:
        # normalize lines
        line = line.strip().lower()

        # ignore comments
        if line.startswith("#"):
            continue

        try:
            # parse tox environment definition
            try:
                (raw_python_versions, framework, framework_versions) = line.split("-")
            except ValueError:
                (raw_python_versions, framework) = line.split("-")
                framework_versions = []

            # collect python versions to test the framework in
            raw_python_versions = set(
                raw_python_versions.replace("{", "").replace("}", "").split(",")
            )
            if "latest" in framework_versions:
                python_versions_latest[framework] |= raw_python_versions
            else:
                python_versions[framework] |= raw_python_versions

        except ValueError:
            print(f"ERROR reading line {line}")

    for framework in python_versions:
        write_yaml_file(
            template,
            framework,
            python_versions[framework],
            python_versions_latest[framework],
        )

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
