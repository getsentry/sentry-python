from hypothesis import given, assume, settings
import hypothesis.strategies as st

from sentry_sdk.utils import safe_repr
from sentry_sdk._compat import PY2, text_type

any_string = st.one_of(st.binary(), st.text())

@given(x=any_string)
@settings(max_examples=1000)
def test_safe_repr_never_broken_for_strings(x):
    r = safe_repr(x)
    assert isinstance(r, text_type)
    assert u'broken repr' not in r

@given(x=any_string)
@settings(max_examples=1000)
def test_safe_repr_never_leaves_escapes_in(x):
    assume('\\u' not in x and '\\x' not in x)
    r = safe_repr(x)
    assert u'\\u' not in r and u'\\x' not in r
