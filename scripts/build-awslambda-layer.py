import os
import subprocess
import tempfile
import shutil
from sentry_sdk.consts import VERSION as SDK_VERSION


DIST_DIRNAME = "dist"
DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", DIST_DIRNAME))
DEST_ZIP_FILENAME = f"sentry-python-serverless-{SDK_VERSION}.zip"
WHEELS_FILEPATH = os.path.join(
    DIST_DIRNAME, f"sentry_sdk-{SDK_VERSION}-py2.py3-none-any.whl"
)

# Top directory in the ZIP file. Placing the Sentry package in `/python` avoids
# creating a directory for a specific version. For more information, see
# https://docs.aws.amazon.com/lambda/latest/dg/configuration-layers.html#configuration-layers-path
PACKAGE_PARENT_DIRECTORY = "python"


class PackageBuilder:
    def __init__(self, base_dir) -> None:
        self.base_dir = base_dir
        self.packages_dir = self.get_relative_path_of(PACKAGE_PARENT_DIRECTORY)

    def make_directories(self):
        os.makedirs(self.packages_dir)

    def install_python_binaries(self):
        subprocess.run(
            [
                "pip",
                "install",
                "--no-cache-dir",  # Disables the cache -> always accesses PyPI
                "-q",  # Quiet
                WHEELS_FILEPATH,  # Copied to the target directory before installation
                "-t",  # Target directory flag
                self.packages_dir,
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
                PACKAGE_PARENT_DIRECTORY,  # Files to be zipped
            ],
            cwd=self.base_dir,
            check=True,  # Raises CalledProcessError if exit status is non-zero
        )

    def get_relative_path_of(self, subfile):
        return os.path.join(self.base_dir, subfile)


def build_packaged_zip():
    with tempfile.TemporaryDirectory() as tmp_dir:
        package_builder = PackageBuilder(tmp_dir)
        package_builder.make_directories()
        package_builder.install_python_binaries()
        package_builder.zip(DEST_ZIP_FILENAME)
        shutil.copy(package_builder.get_relative_path_of(DEST_ZIP_FILENAME), DIST_DIR)


build_packaged_zip()
