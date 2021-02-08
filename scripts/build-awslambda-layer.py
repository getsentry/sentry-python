import argparse
import os
import subprocess
import tempfile
import shutil


SDK_VERSION = "0.19.5"
DIST_DIRNAME = "dist"
DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", DIST_DIRNAME))


def add_arguments(arg_parser):
    arg_parser.add_argument(
        "python",
        action="store",
        nargs=1,  # Default behaviour: will make a list of 1 item
        choices=["2.7", "3.6", "3.7", "3.8"],
        help="Selects the Python runtime version to build the ZIP file for.",
        metavar="python-version",
    )
    return arg_parser


class PackageBuilder:
    def __init__(self, base_dir, packages_dir) -> None:
        self.base_dir = base_dir
        self.packages_dir = packages_dir
        self.packages_inner_dir = os.path.join(self.base_dir, self.packages_dir)
        self.binaries_installation_path = os.path.join(base_dir, packages_dir)

    def make_directories(self):
        os.makedirs(self.packages_inner_dir)

    def install_python_binaries(self):
        wheels_filepath = os.path.join(
            DIST_DIRNAME, f"sentry_sdk-{SDK_VERSION}-py2.py3-none-any.whl"
        )
        subprocess.run(
            [
                "pip",
                "install",
                "--no-cache-dir",  # Disables the cache -> always accesses PyPI
                "-q",  # Quiet
                wheels_filepath,  # Copied to the target directory before installation
                "-t",  # Target directory flag
                self.packages_inner_dir,
            ],
            check=True,
        )

    def zip(self, filename):
        subprocess.run(
            [
                "zip",
                "-q",  # Quiet
                "-x",  # Exclude files
                "**/__pycache__/*",  # Files to be excluded
                "-r",  # Recurse paths
                filename,  # Output filename
                self.packages_dir,  # Files to be zipped
            ],
            cwd=self.base_dir,
            check=True,  # Raises CalledProcessError if exit status is non-zero
        )

    def get_relative_path_of(self, subfile):
        return os.path.join(self.base_dir, subfile)


def build_packaged_zip_for_runtime(runtime, dst_filename):
    print(f"Zipping files for SDK v{SDK_VERSION} with {runtime}")

    packages_dir = os.path.join("python", "lib", runtime, "site-packages")
    with tempfile.TemporaryDirectory() as tmp_dir:
        package_builder = PackageBuilder(tmp_dir, packages_dir)
        package_builder.make_directories()
        package_builder.install_python_binaries()
        package_builder.zip(dst_filename)
        shutil.copy(package_builder.get_relative_path_of(dst_filename), DIST_DIR)


def main():
    arg_parser = argparse.ArgumentParser()
    add_arguments(arg_parser)
    python_version = arg_parser.parse_args().python[0]

    runtime = f"python{python_version}"
    dest_zip_filename = (
        f"sentry-python-awslambda-layer-{SDK_VERSION}-py{python_version}.zip"
    )
    build_packaged_zip_for_runtime(runtime, dest_zip_filename)


main()
