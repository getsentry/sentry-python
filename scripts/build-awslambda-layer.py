import argparse
import os
import os.path
import shutil
import subprocess
import tempfile

from sentry_sdk import VERSION as SENTRY_SDK_VERSION

# This script builds dist/sentry-python-awslambda-layer-A.B.C-pyX.Y.zip layer
# that could to be uploaded as AWS Lambda layer.
#
# To run this script, dist/sentry_sdk-A.B.C-py2.py3-none-any.whl must exist.
# You can build a wheel file using `make dist` command.

work_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))

arg_parser = argparse.ArgumentParser()
arg_parser.add_argument(
    "--python", "-p", action="store", choices=["2.7", "3.6", "3.7", "3.8"]
)
args = arg_parser.parse_args()

if args.python == "2.7":
    zip_file_name = "sentry-python-awslambda-layer-{}-py2.7.zip"
    site_dir = "python2.7"
elif args.python == "3.6":
    zip_file_name = "sentry-python-awslambda-layer-{}-py3.6.zip"
    site_dir = "python3.6"
elif args.python == "3.7":
    zip_file_name = "sentry-python-awslambda-layer-{}-py3.7.zip"
    site_dir = "python3.7"
elif args.python == "3.8":
    zip_file_name = "sentry-python-awslambda-layer-{}-py3.8.zip"
    site_dir = "python3.8"

zip_file_name = zip_file_name.format(SENTRY_SDK_VERSION)

print(
    "Building a layer for SDK version {} and Python {}\n".format(
        SENTRY_SDK_VERSION, args.python
    )
)

with tempfile.TemporaryDirectory() as tmpdir:
    target_rel_path = os.path.join("python", "lib", site_dir, "site-packages")
    target_path = os.path.join(tmpdir, target_rel_path)
    os.makedirs(target_path)
    subprocess.run(
        [
            "pip",
            "install",
            "--no-cache-dir",
            "-q",
            os.path.join(
                "dist", "sentry_sdk-{}-py2.py3-none-any.whl".format(SENTRY_SDK_VERSION)
            ),
            "-t",
            target_path,
        ],
        check=True,
    )
    subprocess.run(
        [
            "zip",
            "-q",
            "-x",
            "**/__pycache__/*",
            "-r",
            zip_file_name,
            target_rel_path,
        ],
        cwd=tmpdir,
        check=True,
    )
    dist_path = os.path.join(work_dir, "dist")
    shutil.copy(os.path.join(tmpdir, zip_file_name), dist_path)
