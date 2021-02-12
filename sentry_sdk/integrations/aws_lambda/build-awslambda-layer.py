import os
import subprocess
import tempfile
import shutil

from sentry_sdk.consts import VERSION as SDK_VERSION


class PackageBuilder:
    def __init__(self, base_dir, pkg_parent_dir, dist_dir_name) -> None:
        self.base_dir = base_dir
        self.pkg_parent_dir = pkg_parent_dir
        self.dist_dir_name = dist_dir_name
        self.packages_dir = self.get_relative_path_of(pkg_parent_dir)

    def make_directories(self):
        os.makedirs(self.packages_dir)

    def install_python_binaries(self):
        wheels_filepath = os.path.join(
            self.dist_dir_name, f"sentry_sdk-{SDK_VERSION}-py2.py3-none-any.whl"
        )
        subprocess.run(
            [
                "pip",
                "install",
                "--no-cache-dir",  # Disables the cache -> always accesses PyPI
                "-q",  # Quiet
                wheels_filepath,  # Copied to the target directory before installation
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
                self.pkg_parent_dir,  # Files to be zipped
            ],
            cwd=self.base_dir,
            check=True,  # Raises CalledProcessError if exit status is non-zero
        )

    def get_relative_path_of(self, subfile):
        return os.path.join(self.base_dir, subfile)


# Ref to `pkg_parent_dir` Top directory in the ZIP file.
# Placing the Sentry package in `/python` avoids
# creating a directory for a specific version. For more information, see
#  https://docs.aws.amazon.com/lambda/latest/dg/configuration-layers.html#configuration-layers-path
def build_packaged_zip(
    dist_dirname='dist',
    dest_rel_path="dist-serverless",
    dest_zip_filename=f"sentry-python-serverless-{SDK_VERSION}.zip",
    pkg_parent_dir="python"
):
    dest_abs_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../..", dest_rel_path)
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        package_builder = PackageBuilder(tmp_dir, pkg_parent_dir, dist_dirname)
        package_builder.make_directories()
        package_builder.install_python_binaries()
        package_builder.zip(dest_zip_filename)
        if not os.path.exists(dest_rel_path):
            os.makedirs(dest_rel_path)
        shutil.copy(
            package_builder.get_relative_path_of(dest_zip_filename),
            dest_abs_path
        )


if __name__ == '__main__':
    build_packaged_zip()
