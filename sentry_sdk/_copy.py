"""
A modified version of Python 3.11's copy.deepcopy (found in Python's 'cpython/Lib/copy.py')
that falls back to repr for non-datastrucure types that we use for extracting frame local variables
in a safe way without holding references to the original objects.

https://github.com/python/cpython/blob/v3.11.7/Lib/copy.py#L128-L241

Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation;

All Rights Reserved


PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
--------------------------------------------

1. This LICENSE AGREEMENT is between the Python Software Foundation
("PSF"), and the Individual or Organization ("Licensee") accessing and
otherwise using this software ("Python") in source or binary form and
its associated documentation.

2. Subject to the terms and conditions of this License Agreement, PSF hereby
grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
analyze, test, perform and/or display publicly, prepare derivative works,
distribute, and otherwise use Python alone or in any derivative version,
provided, however, that PSF's License Agreement and PSF's notice of copyright,
i.e., "Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation;
All Rights Reserved" are retained in Python alone or in any derivative version
prepared by Licensee.

3. In the event Licensee prepares a derivative work that is based on
or incorporates Python or any part thereof, and wants to make
the derivative work available to others as provided herein, then
Licensee hereby agrees to include in any such work a brief summary of
the changes made to Python.

4. PSF is making Python available to Licensee on an "AS IS"
basis.  PSF MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PSF MAKES NO AND
DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON WILL NOT
INFRINGE ANY THIRD PARTY RIGHTS.

5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS
A RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON,
OR ANY DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.

6. This License Agreement will automatically terminate upon a material
breach of its terms and conditions.

7. Nothing in this License Agreement shall be deemed to create any
relationship of agency, partnership, or joint venture between PSF and
Licensee.  This License Agreement does not grant permission to use PSF
trademarks or trade name in a trademark sense to endorse or promote
products or services of Licensee, or any third party.

8. By copying, installing or otherwise using Python, Licensee
agrees to be bound by the terms and conditions of this License
Agreement.

"""

import types
import weakref

from sentry_sdk.utils import safe_repr
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Callable


def deepcopy_fallback_repr(x, memo=None, _nil=[]):  # noqa: B006
    # type: (Any, Optional[dict[int, Any]], Any) -> Any
    """Deep copy like operation on arbitrary Python objects that falls back to repr
    for non-datastructure like objects.
    """

    if memo is None:
        memo = {}

    d = id(x)
    y = memo.get(d, _nil)
    if y is not _nil:
        return y

    cls = type(x)

    copier = _deepcopy_dispatch.get(cls)
    if copier is not None:
        y = copier(x, memo)
    elif issubclass(cls, type):
        y = _deepcopy_atomic(x, memo)
    else:
        y = safe_repr(x)

    # If is its own copy, don't memoize.
    if y is not x:
        memo[d] = y
        _keep_alive(x, memo)  # Make sure x lives at least as long as d
    return y


_deepcopy_dispatch = d = {}  # type: dict[Any, Any]


def _deepcopy_atomic(x, memo):
    # type: (Any, dict[int, Any]) -> Any
    return x


d[type(None)] = _deepcopy_atomic
d[type(Ellipsis)] = _deepcopy_atomic
d[type(NotImplemented)] = _deepcopy_atomic
d[int] = _deepcopy_atomic
d[float] = _deepcopy_atomic
d[bool] = _deepcopy_atomic
d[complex] = _deepcopy_atomic
d[bytes] = _deepcopy_atomic
d[str] = _deepcopy_atomic
d[types.CodeType] = _deepcopy_atomic
d[type] = _deepcopy_atomic
d[range] = _deepcopy_atomic
d[types.BuiltinFunctionType] = _deepcopy_atomic
d[types.FunctionType] = _deepcopy_atomic
d[weakref.ref] = _deepcopy_atomic
d[property] = _deepcopy_atomic


def _deepcopy_list(x, memo, deepcopy=deepcopy_fallback_repr):
    # type: (list[Any], dict[int, Any], Callable[..., Any]) -> list[Any]
    y = []  # type: list[Any]
    memo[id(x)] = y
    append = y.append
    for a in x:
        append(deepcopy(a, memo))
    return y


d[list] = _deepcopy_list


def _deepcopy_tuple(x, memo, deepcopy=deepcopy_fallback_repr):
    # type: (tuple[Any, ...], dict[int, Any], Callable[..., Any]) -> tuple[Any, ...]
    z = [deepcopy(a, memo) for a in x]
    # We're not going to put the tuple in the memo, but it's still important we
    # check for it, in case the tuple contains recursive mutable structures.
    try:
        return memo[id(x)]
    except KeyError:
        pass
    for k, j in zip(x, z):
        if k is not j:
            y = tuple(z)
            break
    else:
        y = x
    return y


d[tuple] = _deepcopy_tuple


def _deepcopy_dict(x, memo, deepcopy=deepcopy_fallback_repr):
    # type: (dict[Any, Any], dict[int, Any], Callable[..., Any]) -> dict[Any, Any]
    y = {}  # type: dict[Any, Any]
    memo[id(x)] = y
    for key, value in x.items():
        y[deepcopy(key, memo)] = deepcopy(value, memo)
    return y


d[dict] = _deepcopy_dict


def _deepcopy_method(x, memo):  # Copy instance methods
    # type: (types.MethodType, dict[int, Any]) -> types.MethodType
    return type(x)(x.__func__, deepcopy_fallback_repr(x.__self__, memo))


d[types.MethodType] = _deepcopy_method

del d


def _keep_alive(x, memo):
    # type: (Any, dict[int, Any]) -> None
    """Keeps a reference to the object x in the memo.

    Because we remember objects by their id, we have
    to assure that possibly temporary objects are kept
    alive by referencing them.
    We store a reference at the id of the memo, which should
    normally not be used unless someone tries to deepcopy
    the memo itself...
    """
    try:
        memo[id(memo)].append(x)
    except KeyError:
        # aha, this is the first one :-)
        memo[id(memo)] = [x]
