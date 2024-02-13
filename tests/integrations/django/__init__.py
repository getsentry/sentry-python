import os
import sys
import pytest

pytest.importorskip("django")

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
