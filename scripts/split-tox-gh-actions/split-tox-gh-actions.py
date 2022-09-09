import configparser
from multiprocessing.sharedctypes import Value

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
    "\n        os: [ubuntu-latest]",
    '\n        python-version: ["{{ python-version }}"]',
    '\n        {{ framework }}-version: ["{{ framework-version }}"]',
    "\n        include:    ",
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
    gh_frameworks,
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
                        "{{ python-version }}",
                        init_python_version.replace("py", "")
                        if init_python_version != "pypy"
                        else "pypy2.7",
                    )
                    .replace("{{ framework-version }}", init_framework_version)
                )
                out_lines.append(l)

            # write gh frameworks
            for fr in gh_frameworks:
                out_lines.append(fr)
        elif template_line == "{{ services }}\n":
            if current_framework in FRAMEWORKS_NEEDING_POSTGRES:
                services = SERVICE_POSTGRES.copy()

                for l in services:
                    l = (
                        l.replace("{{ framework }}", current_framework)
                        .replace(
                            "{{ python-version }}",
                            init_python_version.replace("py", "")
                            if init_python_version != "pypy"
                            else "pypy2.7",
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
    print(f"Splitting tox.init into separate GitHub actions workflows")

    print("Read template")
    f = open(TEMPLATE_FILE, "r")
    template = f.readlines()
    f.close()

    print("Read tox.ini")
    # read tox.ini
    config = configparser.ConfigParser()
    config.read(TOX_FILE)
    lines = [x for x in config["tox"]["envlist"].split("\n") if len(x) > 0]

    current_framework = None
    python_versions = []
    framework_versions = []
    gh_frameworks = []

    init_python_version = None
    init_framework_version = None

    for line in lines:
        # normalize lines
        line = line.strip().lower()

        # ignore comments
        if line.startswith("#"):
            continue

        try:
            # parse tox environment
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

            if current_framework is None:
                current_framework = framework

            if current_framework != framework:
                write_yaml_file(
                    template,
                    current_framework,
                    gh_frameworks,
                    init_python_version,
                    init_framework_version,
                )

                current_framework = framework
                python_versions = []
                framework_versions = []
                gh_frameworks = []
                init_python_version = None
                init_framework_version = None

            # collect environments
            for python_version in (
                raw_python_versions.replace("{", "").replace("}", "").split(",")
            ):
                if python_version not in python_versions:
                    python_versions.append(python_version)
                if not init_python_version:
                    init_python_version = python_version

            for framework_version in (
                raw_framework_versions.replace("{", "").replace("}", "").split(",")
            ):
                if framework_version not in framework_versions:
                    framework_versions.append(framework_version)
                if not init_framework_version:
                    init_framework_version = framework_version
            if not init_framework_version:
                init_framework_version = "latest"

            for fr in framework_versions:
                for py in python_versions:
                    py = py.replace("py", "") if py != "pypy" else "pypy2.7"
                    s = f'\n          - {{ {current_framework}-version: "{fr or "latest"}", python-version: "{py}", os: "ubuntu-latest" }}'
                    gh_frameworks.append(s)
            python_versions = []
            framework_versions = []

        except ValueError as err:
            print(f"ERROR reading line {line}")

    write_yaml_file(
        template,
        current_framework,
        gh_frameworks,
        init_python_version,
        init_framework_version,
    )

    print("All done. Have a nice day!")


if __name__ == "__main__":
    main()
