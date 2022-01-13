from sentry_sdk._types import MYPY

if MYPY:
    from typing import Callable, Union, Tuple
    from django.urls.resolvers import URLPattern, URLResolver

    CustomUrlconf = Union[str, Callable[[], str], Tuple[URLPattern, URLPattern, URLResolver], Tuple[URLPattern]]
