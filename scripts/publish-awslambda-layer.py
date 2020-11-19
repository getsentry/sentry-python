import argparse
import os

import boto3
from botocore.config import Config

from sentry_sdk import VERSION as SENTRY_SDK_VERSION

# This scripts publishes current lambda layer zip bundle to AWS and sets layer permission to public.
# To run you'll probably need to set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables.
#
# The file dist/sentry-python-awslambda-layer-A.B.C-pyX.Y.zip MUST exist before publishing.
# You could get it using `python scripts/build-awslambda-layer.py` or just `make awslambda-layer-build`.

all_regions = [
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "ap-south-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-northeast-1",
    "ap-northeast-2",
    "ca-central-1",
    "eu-central-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "sa-east-1",
]

arg_parser = argparse.ArgumentParser()
arg_parser.add_argument(
    "--regions",
    "-r",
    nargs="+",
    action="extend",
    help="If not specified, layer will be published to all supported regions",
)
arg_parser.add_argument(
    "--python", "-p", action="store", choices=["2.7", "3.6", "3.7", "3.8"]
)
args = arg_parser.parse_args()

if args.python == "2.7":
    layer_name = "SentryPythonSDK27"
    zip_file_name = "sentry-python-awslambda-layer-{}-py2.7.zip"
    compatible_runtime = "python2.7"
elif args.python == "3.6":
    layer_name = "SentryPythonSDK36"
    zip_file_name = "sentry-python-awslambda-layer-{}-py3.6.zip"
    compatible_runtime = "python3.6"
elif args.python == "3.7":
    layer_name = "SentryPythonSDK37"
    zip_file_name = "sentry-python-awslambda-layer-{}-py3.7.zip"
    compatible_runtime = "python3.7"
elif args.python == "3.8":
    layer_name = "SentryPythonSDK38"
    zip_file_name = "sentry-python-awslambda-layer-{}-py3.8.zip"
    compatible_runtime = "python3.8"

zip_file_name = zip_file_name.format(SENTRY_SDK_VERSION)

work_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))

with open(os.path.join(work_dir, "dist", zip_file_name), "rb") as fd:
    layer_contents = fd.read()

print(
    "Publishing a layer for SDK version {} and Python {}\n".format(
        SENTRY_SDK_VERSION, args.python
    )
)

regions = args.regions
if regions is None:
    regions = all_regions

for region in regions:
    awslambda = boto3.client("lambda", config=Config(region_name=region))
    response = awslambda.publish_layer_version(
        LayerName=layer_name,
        Content={
            "ZipFile": layer_contents,
        },
        CompatibleRuntimes=[compatible_runtime],
        LicenseInfo="BSD",
    )
    awslambda.add_layer_version_permission(
        LayerName=layer_name,
        VersionNumber=response["Version"],
        StatementId="public",
        Action="lambda:GetLayerVersion",
        Principal="*",
    )
    print(response["LayerVersionArn"])
