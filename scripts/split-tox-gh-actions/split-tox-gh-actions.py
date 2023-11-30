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

from jinja2 import Environment, FileSystemLoader


OUT_DIR = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows"
TOX_FILE = Path(__file__).resolve().parent.parent.parent / "tox.ini"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

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

FRAMEWORKS_NEEDING_GITHUB_SECRETS = [
    "aws_lambda",
]

ENV = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
)


def main(fail_on_changes):
    """Create one CI workflow for each framework defined in tox.ini."""
    if fail_on_changes:
        old_hash = get_files_hash()

    print("Parsing tox.ini...")
    py_versions_pinned, py_versions_latest = parse_tox()

    print("Rendering templates...")
    for framework in py_versions_pinned:
        contents = render_template(
            framework,
            py_versions_pinned[framework],
            py_versions_latest[framework],
        )
        filename = write_file(contents, framework)
        print(f"Created {filename}")

    if fail_on_changes:
        new_hash = get_files_hash()

        if old_hash != new_hash:
            raise RuntimeError(
                "The yaml configuration files have changed. This means that tox.ini has changed "
                "but the changes have not been propagated to the GitHub actions config files. "
                "Please run `python scripts/split-tox-gh-actions/split-tox-gh-actions.py` "
                "locally and commit the changes of the yaml configuration files to continue. "
            )

    print("All done. Have a nice day!")


def parse_tox():
    config = configparser.ConfigParser()
    config.read(TOX_FILE)
    lines = [
        line
        for line in config["tox"]["envlist"].split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]

    py_versions_pinned = defaultdict(set)
    py_versions_latest = defaultdict(set)

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

            # collect python versions to test the framework in
            raw_python_versions = set(
                raw_python_versions.replace("{", "").replace("}", "").split(",")
            )
            if "latest" in framework_versions:
                py_versions_latest[framework] |= raw_python_versions
            else:
                py_versions_pinned[framework] |= raw_python_versions

        except ValueError:
            print(f"ERROR reading line {line}")

    py_versions_pinned = _normalize_py_versions(py_versions_pinned)
    py_versions_latest = _normalize_py_versions(py_versions_latest)

    return py_versions_pinned, py_versions_latest


def _normalize_py_versions(py_versions):
    normalized = defaultdict(set)
    normalized |= {
        framework: sorted(
            [py.replace("py", "") for py in versions],
            key=lambda v: tuple(map(int, v.split("."))),
        )
        for framework, versions in py_versions.items()
    }
    return normalized


def get_files_hash():
    """Calculate a hash of all the yaml configuration files"""
    hasher = hashlib.md5()
    path_pattern = (OUT_DIR / "test-integration-*.yml").as_posix()
    for file in glob(path_pattern):
        with open(file, "rb") as f:
            buf = f.read()
            hasher.update(buf)

    return hasher.hexdigest()


def render_template(framework, py_versions_pinned, py_versions_latest):
    template = ENV.get_template("base.jinja")

    context = {
        "framework": framework,
        "needs_aws_credentials": framework in FRAMEWORKS_NEEDING_AWS,
        "needs_clickhouse": framework in FRAMEWORKS_NEEDING_CLICKHOUSE,
        "needs_postgres": framework in FRAMEWORKS_NEEDING_POSTGRES,
        "needs_github_secrets": framework in FRAMEWORKS_NEEDING_GITHUB_SECRETS,
        "py_versions": {
            # formatted for including in the matrix
            "pinned": [f'"{v}"' for v in py_versions_pinned if v != "2.7"],
            "py27": ['"2.7"'] if "2.7" in py_versions_pinned else [],
            "latest": [f'"{v}"' for v in py_versions_latest],
        },
    }
    rendered = template.render(context)
    rendered = postprocess_template(rendered)
    return rendered


def postprocess_template(rendered):
    return "\n".join([line for line in rendered.split("\n") if line.strip()]) + "\n"


def write_file(contents, framework):
    if framework == "common":
        outfile = OUT_DIR / f"test-{framework}.yml"
    else:
        outfile = OUT_DIR / f"test-integration-{framework}.yml"

    with open(outfile, "w") as file:
        file.write(contents)

    return outfile


if __name__ == "__main__":
    fail_on_changes = len(sys.argv) == 2 and sys.argv[1] == "--fail-on-changes"
    main(fail_on_changes)
