import configparser
from collections import defaultdict
from multiprocessing.sharedctypes import Value


OUT_DIR = "/Users/antonpirker/code/sentry-python/.github/workflows/"
TOX_FILE = "/Users/antonpirker/code/sentry-python/tox.ini"
TEMPLATE_FILE = (
    "/Users/antonpirker/code/sentry-python/scripts/split-tox-gh-actions/ci-yaml.txt"
)
TEMPLATE_FILE_SERVICES = "/Users/antonpirker/code/sentry-python/scripts/split-tox-gh-actions/ci-yaml-services.txt"

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
            m = MATRIX_DEFINITION
            py_versions = [f'"{py.replace("py", "")}"' for py in python_versions]

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
    outfile_name = OUT_DIR + f"test-integration-{current_framework}.yml"
    print(f"Writing {outfile_name}")
    f = open(outfile_name, "w")
    f.writelines(out)
    f.close()


def main():
    """Create one CI workflow for each framework defined in tox.ini"""

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
