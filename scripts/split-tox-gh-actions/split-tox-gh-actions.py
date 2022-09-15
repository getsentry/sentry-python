import configparser
from multiprocessing.sharedctypes import Value
from collections import defaultdict

OUT_DIR = "/Users/antonpirker/code/sentry-python/.github/workflows/"
TOX_FILE = "/Users/antonpirker/code/sentry-python/tox.ini"
TEMPLATE_FILE = (
    "/Users/antonpirker/code/sentry-python/scripts/split-tox-gh-actions/ci-template.yml"
)

FRAMEWORKS_NEEDING_POSTGRES = [
    "django",
]

MATRIX_DEFINITION = [
    "    strategy:",
    "\n      matrix:",
    '\n        python-version: ["{{ python-version }}"]',
    '\n        {{ framework }}-versions: ["{{ framework-version }}"]',
    "\n        os: [ubuntu-latest]",
]

SERVICE_POSTGRES = [
    "    services:",
    "\n      postgres:",
    "\n        image: postgres",
    "\n        env:",
    "\n          POSTGRES_PASSWORD: sentry",
    "\n        # Set health checks to wait until postgres has started",
    "\n        options: >-",
    "\n          --health-cmd pg_isready",
    "\n          --health-interval 10s",
    "\n          --health-timeout 5s",
    "\n          --health-retries 5",
    "\n        # Maps tcp port 5432 on service container to the host",
    "\n        ports:",
    "\n          - 5432:5432",
    "\n    env:",
    "\n      SENTRY_PYTHON_TEST_POSTGRES_USER: postgres",
    "\n      SENTRY_PYTHON_TEST_POSTGRES_PASSWORD: sentry",
    "\n      SENTRY_PYTHON_TEST_POSTGRES_NAME: ci_test",
]


def write_yaml_file(
    template,
    current_framework,
    matrix_includes,
    init_python_version,
    init_framework_version,
):
    # render template for print
    out_lines = []
    for template_line in template:
        if template_line == "{{ strategy_matrix }}\n":
            h = MATRIX_DEFINITION.copy()

            for l in h:
                l = (
                    l.replace("{{ framework }}", current_framework)
                    .replace(
                        "{{ python-version }}", init_python_version.replace("py", "")
                    )
                    .replace("{{ framework-version }}", init_framework_version)
                )
                out_lines.append(l)

            # write matrix includes
            if len(matrix_includes) > 0:
                out_lines.append("\n        include:")

            for matrix_include in matrix_includes:
                out_lines.append(matrix_include)
        elif template_line == "{{ services }}\n":
            if current_framework in FRAMEWORKS_NEEDING_POSTGRES:
                services = SERVICE_POSTGRES.copy()

                for l in services:
                    l = (
                        l.replace("{{ framework }}", current_framework)
                        .replace(
                            "{{ python-version }}",
                            init_python_version.replace("py", ""),
                        )
                        .replace("{{ framework-version }}", init_framework_version)
                    )
                    out_lines.append(l)

        else:
            out_lines.append(
                template_line.replace("{{ framework }}", current_framework)
            )

    # write rendered template
    outfile_name = OUT_DIR + f"test-integration-{current_framework}.yml"
    print(f"Writing {outfile_name}")
    f = open(outfile_name, "w")
    f.writelines(out_lines)
    f.close()


def main():
    print("Read GitHub actions config file template")
    f = open(TEMPLATE_FILE, "r")
    template = f.readlines()
    f.close()

    print("Read tox.ini")
    config = configparser.ConfigParser()
    config.read(TOX_FILE)
    lines = [x for x in config["tox"]["envlist"].split("\n") if len(x) > 0]

    everything = defaultdict(lambda: defaultdict(list))

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
                (raw_python_versions, framework, raw_framework_versions) = line.split(
                    "-"
                )
            except ValueError:
                (
                    raw_python_versions,
                    framework,
                ) = line.split("-")
                raw_framework_versions = ""

            # collect framework versions per python version
            for python_version in (
                raw_python_versions.replace("{", "").replace("}", "").split(",")
            ):
                if raw_framework_versions == "":
                    everything[framework][python_version].append("latest")
                    continue

                for framework_version in (
                    raw_framework_versions.replace("{", "").replace("}", "").split(",")
                ):
                    everything[framework][python_version].append(framework_version)

        except ValueError as err:
            print(f"ERROR reading line {line}")

    matrix_includes = []
    init_python_version = None
    init_framework_version = None

    for framework in everything:
        for py in everything[framework]:
            if not init_python_version and not init_framework_version:
                init_python_version = py
                init_framework_version = ",".join(everything[framework][py])

            else:
                fr_versions = ",".join(everything[framework][py])
                s = f'\n          - {{ python-version: "{py.replace("py", "")}", {framework}-versions: "{fr_versions}", os: "ubuntu-latest" }}'
                matrix_includes.append(s)

        write_yaml_file(
            template,
            framework,
            matrix_includes,
            init_python_version,
            init_framework_version,
        )

        matrix_includes = []
        init_python_version = None
        init_framework_version = None

    print("All done. Have a nice day!")


if __name__ == "__main__":
    main()
