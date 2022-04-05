import os
import subprocess
import tempfile
import shutil

from sentry_sdk.consts import VERSION as SDK_VERSION
from sentry_sdk._types import MYPY

if MYPY:
    from typing import Union


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

    def create_init_serverless_sdk_package(self):
        # type: (...) -> None
        """
        Method that creates the init_serverless_sdk pkg in the
        sentry-python-serverless zip
        """
        serverless_sdk_path = (
            f"{self.packages_dir}/sentry_sdk/" f"integrations/init_serverless_sdk"
        )
        if not os.path.exists(serverless_sdk_path):
            os.makedirs(serverless_sdk_path)
        shutil.copy(
            "scripts/init_serverless_sdk.py", f"{serverless_sdk_path}/__init__.py"
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
def build_layer_dir(
    dist_rel_path="dist",  # type: str
    pkg_parent_dir="python",  # type: str
    dest_abs_path=None,  # type: Union[str, None]
):
    # type: (...) -> None
    if dest_abs_path is None:
        dest_abs_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", dist_rel_path)
        )
    with tempfile.TemporaryDirectory() as tmp_dir:
        package_builder = PackageBuilder(tmp_dir, pkg_parent_dir, dist_rel_path)
        package_builder.make_directories()
        package_builder.install_python_binaries()
        package_builder.create_init_serverless_sdk_package()
        if not os.path.exists(dist_rel_path):
            os.makedirs(dist_rel_path)


if __name__ == "__main__":
    build_layer_dir()
