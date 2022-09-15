import sys


def main():
    python_version = sys.argv[1]
    framework = sys.argv[2] if len(sys.argv) >= 3 else ""
    framework_version = sys.argv[3] if len(sys.argv) >= 4 else ""

    py_version = ""
    fr_version = ""

    py_version = "py" + python_version

    if framework_version == "latest" or framework_version == "":
        fr_version = ""
    else:
        fr_version = "-" + framework_version

    if framework == "":
        fr_name = ""
    else:
        fr_name = "-" + framework

    print(py_version + fr_name + fr_version)


if __name__ == "__main__":
    main()
