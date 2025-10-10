import os
import sys
import pytest

pytest.importorskip("httpx")

# Load `httpx_helpers` into the module search path to test request source path names relative to module. See
# `test_request_source_with_module_in_search_path`
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
