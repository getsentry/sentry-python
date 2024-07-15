"""
Separate module for tests that check backwards compatibility of the Hub API with 1.x.
These tests should be removed once we remove the Hub API, likely in the next major.

All tests in this module are run with hub isolation, provided by `isolate_hub` autouse
fixture, defined in `conftest.py`.
"""
