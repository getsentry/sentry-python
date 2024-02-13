import os
import sys
import pytest

pytest.importorskip("sqlalchemy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
