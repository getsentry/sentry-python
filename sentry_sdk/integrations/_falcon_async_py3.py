"""
This file was created so we could have a process_request_async method on our
SentryFalconMiddleware class, so we could properly support ASGI with Falcon.
However, since process_request_async needs to be an async function, we cannot
define it in Python 2.7, as this produces a syntax error.

So, we have split the process_request_async implementation into this separate
file, which can only be loaded in Python 3 and above.

TODO: If we ever remove Python 2.7 support, we should delete this file and move
the process_request_async implementation to the SentryFalconMiddleware class
in the falcon.py file.
"""

from typing import Any


class _SentryFalconMiddlewareBase:
    """
    The SentryFalconMiddleware should inherit from this class in Python 3. The only
    purpose of this class is to provide an implementation of process_request_async which
    simply calls process_request.
    """

    def process_request(self, req: Any, resp: Any, *args: Any, **kwargs: Any) -> None:
        """
        SentryFalconMiddleware will override this method and provide the actual implementation.
        """
        pass

    async def process_request_async(
        self, req: Any, resp: Any, *args: Any, **kwargs: Any
    ) -> None:
        self.process_request(req, resp, *args, **kwargs)
