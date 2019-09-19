import pytest
import inspect

pytest.importorskip("apache_beam")

import dill

from sentry_sdk.integrations.spark import (
    SparkIntegration,
    patch_pyspark_java_gateway
)

def test_java_gateway():
    patch_pyspark_java_gateway()
