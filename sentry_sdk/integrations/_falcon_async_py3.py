"""
This file was created so we could have a process_request_async method on our
SentryFalconMiddleware class, so we could properly support ASGI with Falcon.
However, since process_request_async needs to be an async function, we cannot
define it in Python 2.7, as this produces a syntax error.

So, we have split the process_request_async implementation into this separate
file, which can only be loaded in Python 3 and above.
"""

from abc import ABC, abstractmethod
from typing import Any


class SentryFalconMiddlewareBase(ABC):
    @abstractmethod
    def process_request(self, req: Any, resp: Any, *args: Any, **kwargs: Any) -> None:
        pass

    async def process_request_async(
        self, req: Any, resp: Any, *args: Any, **kwargs: Any
    ) -> None:
        self.process_request(req, resp, *args, **kwargs)
