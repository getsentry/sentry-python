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
    "\n        python-version: [{{ python-version }}]",
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
    python_versions,
):
    # render template for print
    out_lines = []
    for template_line in template:
        if template_line == "{{ strategy_matrix }}\n":
            h = MATRIX_DEFINITION.copy()

            for l in h:
                output_array = [f'"{py.replace("py", "")}"' for py in python_versions]
                output_string = ",".join(output_array)

                l = l.replace("{{ framework }}", current_framework).replace(
                    "{{ python-version }}", output_string
                )
                out_lines.append(l)

        elif template_line == "{{ services }}\n":
            if current_framework in FRAMEWORKS_NEEDING_POSTGRES:
                out_lines += SERVICE_POSTGRES

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
        write_yaml_file(
            template,
            framework,
            python_versions[framework],
        )

    print("All done. Have a nice day!")


if __name__ == "__main__":
    main()
