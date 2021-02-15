import os
import subprocess
import tempfile
import shutil

from sentry_sdk.consts import VERSION as SDK_VERSION


class PackageBuilder:
    def __init__(
        self,
        base_dir,  # type: str
        pkg_parent_dir,  # type: str
        dist_rel_path,  # type: str
    ):
        # type: (...) -> None
        self.base_dir = base_dir
        self.pkg_parent_dir = pkg_parent_dir
        self.dist_rel_path = dist_rel_path
        self.packages_dir = self.get_relative_path_of(pkg_parent_dir)

    def make_directories(self):
        # type: (...) -> None
        os.makedirs(self.packages_dir)

    def install_python_binaries(self):
        # type: (...) -> None
        wheels_filepath = os.path.join(
            self.dist_rel_path, f"sentry_sdk-{SDK_VERSION}-py2.py3-none-any.whl"
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

    def zip(
        self, filename  # type: str
    ):
        # type: (...) -> None
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

    def get_relative_path_of(
        self, subfile  # type: str
    ):
        # type: (...) -> str
        return os.path.join(self.base_dir, subfile)


# Ref to `pkg_parent_dir` Top directory in the ZIP file.
# Placing the Sentry package in `/python` avoids
# creating a directory for a specific version. For more information, see
#  https://docs.aws.amazon.com/lambda/latest/dg/configuration-layers.html#configuration-layers-path
def build_packaged_zip(
    dist_rel_path="dist",
    dest_zip_filename=f"sentry-python-serverless-{SDK_VERSION}.zip",
    pkg_parent_dir="python",
    dest_abs_path=None,
):
    # type: (...) -> None
    if not dest_abs_path:
        dest_abs_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../..", dist_rel_path)
        )
    with tempfile.TemporaryDirectory() as tmp_dir:
        package_builder = PackageBuilder(tmp_dir, pkg_parent_dir, dist_rel_path)
        package_builder.make_directories()
        package_builder.install_python_binaries()
        package_builder.zip(dest_zip_filename)
        if not os.path.exists(dist_rel_path):
            os.makedirs(dist_rel_path)
        shutil.copy(
            package_builder.get_relative_path_of(dest_zip_filename), dest_abs_path
        )


if __name__ == "__main__":
    build_packaged_zip()
