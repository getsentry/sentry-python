import sys


def main():
    python_version = sys.argv[1]
    framework = sys.argv[2]
    framework_version = sys.argv[3]

    py_version = ""
    fr_version = ""

    if python_version == "pypy2.7":
        py_version = "pypy"
    else:
        py_version = "py" + python_version

    if framework_version == "latest" or framework_version == "":
        fr_version = ""
    else:
        fr_version = "-" + framework_version

    print(py_version + "-" + framework + fr_version)


if __name__ == "__main__":
    main()
