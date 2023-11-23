import sys
from functools import partial

import pytest

from sentry_sdk.utils import transaction_from_function

try:
    from functools import partialmethod
except ImportError:
    pass

def test_send_to_spotlight():
    