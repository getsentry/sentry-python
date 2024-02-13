import os
import sys
import pytest

pytest.importorskip("asyncpg")
pytest.importorskip("pytest_asyncio")

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
