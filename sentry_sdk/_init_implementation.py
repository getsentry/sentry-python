import re
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.utils import logger, parse_version

if TYPE_CHECKING:
    from typing import Any, Optional

    import sentry_sdk.consts


def _check_version_deprecations() -> None:
    try:
        import gevent

        gevent_version = tuple(
            int(part) for part in re.split(r"a|b|rc|\.", gevent.__version__)[:2]
        )
        if gevent_version < (20, 9):
            logger.warning(
                "sentry-sdk 3.x supports gevent 20.9.0 or newer. "
                "Please upgrade gevent or downgrade to sentry-sdk 2.x."
            )
    except Exception:
        pass

    try:
        import greenlet

        greenlet_version = parse_version(greenlet.__version__)
        if greenlet_version is not None and greenlet_version < (0, 4, 17):
            logger.warning(
                "sentry-sdk 3.x supports greenlet 0.4.17 or newer. "
                "Please upgrade greenlet or downgrade to sentry-sdk 2.x."
            )
    except Exception:
        pass


def _init(*args: "Optional[str]", **kwargs: "Any") -> None:
    """Initializes the SDK and optionally integrations.

    This takes the same arguments as the client constructor.
    """
    client = sentry_sdk.Client(*args, **kwargs)
    sentry_sdk.get_global_scope().set_client(client)
    _check_version_deprecations()


if TYPE_CHECKING:
    # Make mypy, PyCharm and other static analyzers think `init` is a type to
    # have nicer autocompletion for params.
    #
    # Use `ClientConstructor` to define the argument types of `init`.

    class init(sentry_sdk.consts.ClientConstructor):  # noqa: N801
        pass

else:
    # Alias `init` for actual usage. Go through the lambda indirection to throw
    # PyCharm off of the weakly typed signature (it would otherwise discover
    # both the weakly typed signature of `_init` and our faked `init` type).

    init = (lambda: _init)()
