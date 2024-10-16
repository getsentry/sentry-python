import io
import os
import urllib.parse
import urllib.request
import urllib.error
import urllib3

from itertools import chain

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional

from sentry_sdk.utils import logger, env_to_bool, capture_internal_exceptions
from sentry_sdk.envelope import Envelope


DEFAULT_SPOTLIGHT_URL = "http://localhost:8969/stream"
DJANGO_SPOTLIGHT_MIDDLEWARE_PATH = "sentry_sdk.spotlight.SpotlightMiddleware"


class SpotlightClient:
    def __init__(self, url):
        # type: (str) -> None
        self.url = url
        self.http = urllib3.PoolManager()
        self.tries = 0

    def capture_envelope(self, envelope):
        # type: (Envelope) -> None
        if self.tries > 3:
            logger.warning(
                "Too many errors sending to Spotlight, stop sending events there."
            )
            return
        body = io.BytesIO()
        envelope.serialize_into(body)
        try:
            req = self.http.request(
                url=self.url,
                body=body.getvalue(),
                method="POST",
                headers={
                    "Content-Type": "application/x-sentry-envelope",
                },
            )
            req.close()
        except Exception as e:
            self.tries += 1
            logger.warning(str(e))


try:
    from django.http import HttpResponseServerError
    from django.conf import settings

    class SpotlightMiddleware:
        def __init__(self, get_response):
            # type: (Any, Callable[..., Any]) -> None
            self.get_response = get_response

        def __call__(self, request):
            # type: (Any, Any) -> Any
            return self.get_response(request)

        def process_exception(self, _request, exception):
            # type: (Any, Any, Exception) -> Optional[HttpResponseServerError]
            if not settings.DEBUG:
                return None

            import sentry_sdk.api

            spotlight_client = sentry_sdk.api.get_client().spotlight
            if spotlight_client is None:
                return None

            # Spotlight URL has a trailing `/stream` part at the end so split it off
            spotlight_url = spotlight_client.url.rsplit("/", 1)[0]

            try:
                spotlight = urllib.request.urlopen(spotlight_url).read().decode("utf-8")
            except urllib.error.URLError:
                return None
            else:
                event_id = sentry_sdk.api.capture_exception(exception)
                return HttpResponseServerError(
                    spotlight.replace(
                        "<html>",
                        (
                            f'<html><base href="{spotlight_url}">'
                            '<script>window.__spotlight = {{ initOptions: {{ startFrom: "/errors/{event_id}" }}}};</script>'.format(
                                event_id=event_id
                            )
                        ),
                    )
                )

except ImportError:
    settings = None


def setup_spotlight(options):
    # type: (Dict[str, Any]) -> Optional[SpotlightClient]

    url = options.get("spotlight")

    if isinstance(url, str):
        pass
    elif url is True:
        url = DEFAULT_SPOTLIGHT_URL
    else:
        return None

    if (
        settings is not None
        and settings.DEBUG
        and env_to_bool(os.environ.get("SENTRY_SPOTLIGHT_ON_ERROR", "1"))
    ):
        with capture_internal_exceptions():
            middleware = settings.MIDDLEWARE
            if DJANGO_SPOTLIGHT_MIDDLEWARE_PATH not in middleware:
                settings.MIDDLEWARE = type(middleware)(
                    chain(middleware, (DJANGO_SPOTLIGHT_MIDDLEWARE_PATH,))
                )

    return SpotlightClient(url)
