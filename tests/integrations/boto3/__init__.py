import os

import pytest

pytest.importorskip("boto3")
xml_fixture_path = os.path.dirname(os.path.abspath(__file__))


def read_fixture(name):
    with open(os.path.join(xml_fixture_path, name), "rb") as f:
        return f.read()
