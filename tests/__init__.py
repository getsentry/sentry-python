import sys
import warnings

# This is used in _capture_internal_warnings. We need to run this at import
# time because that's where many deprecation warnings might get thrown.
#
# This lives in tests/__init__.py because apparently even tests/conftest.py
# gets loaded too late.
assert "sentry_sdk" not in sys.modules

_warning_recorder_mgr = warnings.catch_warnings(record=True)
_warning_recorder = _warning_recorder_mgr.__enter__()
