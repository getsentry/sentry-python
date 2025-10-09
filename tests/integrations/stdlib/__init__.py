import os
import sys

# Load `asyncpg_helpers` into the module search path to test query source path names relative to module. See
# `test_query_source_with_module_in_search_path`
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
