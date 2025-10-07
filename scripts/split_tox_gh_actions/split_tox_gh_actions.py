"""Split Tox to GitHub Actions

This is a small script to split a tox.ini config file into multiple GitHub actions configuration files.
This way each group of frameworks defined in tox.ini will get its own GitHub actions configuration file
which allows them to be run in parallel in GitHub actions.

This will generate/update several configuration files, that need to be commited to Git afterwards.
Whenever tox.ini is changed, this script needs to be run.

Usage:
    python split_tox_gh_actions.py [--fail-on-changes]

If the parameter `--fail-on-changes` is set, the script will raise a RuntimeError in case the yaml
files have been changed by the scripts execution. This is used in CI to check if the yaml files
represent the current tox.ini file. (And if not the CI run fails.)
"""

import configparser
import hashlib
import re
import sys
from collections import defaultdict
from functools import reduce
from glob import glob
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TOXENV_REGEX = re.compile(
    r"""
    {?(?P<py_versions>(py\d+\.\d+,?)+)}?
    -(?P<framework>[a-z](?:[a-z_]|-(?!v{?\d))*[a-z0-9])
    (?:-(
        (v{?(?P<framework_versions>[0-9.]+[0-9a-z,.]*}?))
    ))?
""",
    re.VERBOSE,
)

OUT_DIR = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows"
TOX_FILE = Path(__file__).resolve().parent.parent.parent / "tox.ini"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

FRAMEWORKS_NEEDING_POSTGRES = {
    "django",
    "asyncpg",
}

FRAMEWORKS_NEEDING_REDIS = {
    "celery",
}

FRAMEWORKS_NEEDING_CLICKHOUSE = {
    "clickhouse_driver",
}

FRAMEWORKS_NEEDING_DOCKER = {
    "aws_lambda",
}

FRAMEWORKS_NEEDING_JAVA = {
    "spark",
}

# Frameworks grouped here will be tested together to not hog all GitHub runners.
# If you add or remove a group, make sure to git rm the generated YAML file as
# well.
GROUPS = {
    "Common": [
        "common",
    ],
    "AI": [
        "anthropic",
        "cohere",
        "langchain-base",
        "langchain-notiktoken",
        "litellm",
        "openai-base",
        "openai-notiktoken",
        "langgraph",
        "google-genai",
        "openai_agents",
        "huggingface_hub",
    ],
    "Cloud": [
        "aws_lambda",
        "boto3",
        "chalice",
        "cloud_resource_context",
        "gcp",
    ],
    "DBs": [
        "asyncpg",
        "clickhouse_driver",
        "pymongo",
        "redis",
        "redis_py_cluster_legacy",
        "sqlalchemy",
    ],
    "Flags": [
        "launchdarkly",
        "openfeature",
        "statsig",
        "unleash",
    ],
    "Gevent": [
        "gevent",
    ],
    "GraphQL": [
        "ariadne",
        "gql",
        "graphene",
        "strawberry",
    ],
    "Network": [
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


ENV = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
)


def main(fail_on_changes):
    """Create one CI workflow for each framework defined in tox.ini."""
    if fail_on_changes:
        old_hash = get_files_hash()

    print("Parsing tox.ini...")
    py_versions = parse_tox()

    if fail_on_changes:
        print("Checking if all frameworks belong in a group...")
        missing_frameworks = find_frameworks_missing_from_groups(py_versions)
        if missing_frameworks:
            raise RuntimeError(
                "Please add the following frameworks to the corresponding group "
                "in `GROUPS` in `scripts/split_tox_gh_actions/split_tox_gh_actions.py: "
                + ", ".join(missing_frameworks)
            )

    print("Rendering templates...")
    for group, frameworks in GROUPS.items():
        contents = render_template(group, frameworks, py_versions)
        filename = write_file(contents, group)
        print(f"Created {filename}")

    if fail_on_changes:
        new_hash = get_files_hash()

        if old_hash != new_hash:
            raise RuntimeError(
                "The yaml configuration files have changed. This means that either `tox.ini` "
                "or one of the constants in `split_tox_gh_actions.py` has changed "
                "but the changes have not been propagated to the GitHub actions config files. "
                "Please run `python scripts/split_tox_gh_actions/split_tox_gh_actions.py` "
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

    py_versions = defaultdict(set)

    parsed_correctly = True

    for line in lines:
        # normalize lines
        line = line.strip().lower()

        try:
            # parse tox environment definition
            parsed = TOXENV_REGEX.match(line)
            if not parsed:
                print(f"ERROR reading line {line}")
                raise ValueError("Failed to parse tox environment definition")

            groups = parsed.groupdict()
            raw_python_versions = groups["py_versions"]
            framework = groups["framework"]

            # collect python versions to test the framework in
            raw_python_versions = set(raw_python_versions.split(","))
            py_versions[framework] |= raw_python_versions

        except Exception:
            print(f"ERROR reading line {line}")
            parsed_correctly = False

    if not parsed_correctly:
        raise RuntimeError("Failed to parse tox.ini")

    py_versions = _normalize_py_versions(py_versions)

    return py_versions


def find_frameworks_missing_from_groups(py_versions):
    frameworks_in_a_group = _union(GROUPS.values())
    all_frameworks = set(py_versions)
    return all_frameworks - frameworks_in_a_group


def _normalize_py_versions(py_versions):
    def replace_and_sort(versions):
        return sorted(
            [py.replace("py", "") for py in versions],
            key=lambda v: tuple(map(int, v.split("."))),
        )

    if isinstance(py_versions, dict):
        normalized = defaultdict(set)
        normalized |= {
            framework: replace_and_sort(versions)
            for framework, versions in py_versions.items()
        }

    elif isinstance(py_versions, set):
        normalized = replace_and_sort(py_versions)

    return normalized


def get_files_hash():
    """Calculate a hash of all the yaml configuration files"""
    hasher = hashlib.md5()
    path_pattern = (OUT_DIR / "test-integrations-*.yml").as_posix()
    for file in glob(path_pattern):
        with open(file, "rb") as f:
            buf = f.read()
            hasher.update(buf)

    return hasher.hexdigest()


def _union(seq):
    return reduce(lambda x, y: set(x) | set(y), seq)


def render_template(group, frameworks, py_versions):
    template = ENV.get_template("base.jinja")

    py_versions_final = set()
    for framework in frameworks:
        py_versions_final |= set(py_versions[framework])

    context = {
        "group": group,
        "frameworks": frameworks,
        "needs_clickhouse": bool(set(frameworks) & FRAMEWORKS_NEEDING_CLICKHOUSE),
        "needs_docker": bool(set(frameworks) & FRAMEWORKS_NEEDING_DOCKER),
        "needs_postgres": bool(set(frameworks) & FRAMEWORKS_NEEDING_POSTGRES),
        "needs_redis": bool(set(frameworks) & FRAMEWORKS_NEEDING_REDIS),
        "needs_java": bool(set(frameworks) & FRAMEWORKS_NEEDING_JAVA),
        "py_versions": [
            f'"{version}"' for version in _normalize_py_versions(py_versions_final)
        ],
    }

    rendered = template.render(context)
    rendered = postprocess_template(rendered)
    return rendered


def postprocess_template(rendered):
    return "\n".join([line for line in rendered.split("\n") if line.strip()]) + "\n"


def write_file(contents, group):
    group = group.lower().replace(" ", "-")
    outfile = OUT_DIR / f"test-integrations-{group}.yml"

    with open(outfile, "w") as file:
        file.write(contents)

    return outfile


if __name__ == "__main__":
    fail_on_changes = len(sys.argv) == 2 and sys.argv[1] == "--fail-on-changes"
    main(fail_on_changes)
