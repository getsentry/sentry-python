import re

from django.template import TemplateSyntaxError
from django import VERSION as DJANGO_VERSION

from sentry_sdk import _functools, Hub
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional
    from typing import Iterator
    from typing import Tuple
    from typing import Union

try:
    # support Django 1.9
    from django.template.base import Origin
except ImportError:
    # backward compatibility
    from django.template.loader import LoaderOrigin as Origin


def get_template_frame_from_exception(exc_value):
    # type: (Optional[BaseException]) -> Optional[Dict[str, Any]]

    # As of Django 1.9 or so the new template debug thing showed up.
    if hasattr(exc_value, "template_debug"):
        return _get_template_frame_from_debug(exc_value.template_debug)  # type: ignore

    # As of r16833 (Django) all exceptions may contain a
    # ``django_template_source`` attribute (rather than the legacy
    # ``TemplateSyntaxError.source`` check)
    if hasattr(exc_value, "django_template_source"):
        return _get_template_frame_from_source(
            exc_value.django_template_source  # type: ignore
        )

    if isinstance(exc_value, TemplateSyntaxError) and hasattr(exc_value, "source"):
        source = exc_value.source
        if isinstance(source, (tuple, list)) and isinstance(source[0], Origin):
            return _get_template_frame_from_source(source)  # type: ignore

    return None


def _get_template_name_description(template_name):
    # type: (str) -> str
    if isinstance(template_name, (list, tuple)):
        if template_name:
            return "[{}, ...]".format(template_name[0])
    else:
        return template_name


def _ireplace(old, repl, text, make_bytes):
    # type: (str, str,  Union[str, bytes], Callable[[str], bytes]) -> Union[str, bytes]
    """
    Find `old` in `text` and replace with `repl` ignoring case.
    Also providing a `<matched_string>` named group so in `repl` the matched string in `old` can be used.
    If the given text is bytes, `make_bytes` is used to convert the strings to bytes.
    """
    if isinstance(text, (bytes, bytearray)):
        return re.sub(
            make_bytes("(?P<matched_string>")
            + re.escape(make_bytes(old))
            + make_bytes(")"),
            make_bytes(repl),
            text,
            flags=re.IGNORECASE,
        )

    return re.sub(
        "(?P<matched_string>" + re.escape(old) + ")", repl, text, flags=re.IGNORECASE
    )


def enable_tracing_meta_tags():
    # type: () -> None
    """
    Inject the Sentry tracing meta tags into the HTML response.
    """
    from django.http.response import HttpResponse
    from sentry_sdk.integrations.django import DjangoIntegration

    original_content = HttpResponse.content

    # We need to specify both, the property and the setter for monkey patching a property.
    @property  # type: ignore
    def content(self):
        # type: (HttpResponse) -> Union[str, bytes]
        return original_content.fget(self)

    # We need to specify both, the property and the setter for monkey patching a property.
    @content.setter
    def content(self, value):
        # type: (HttpResponse, Union[str, bytes]) -> None
        hub = Hub.current
        integration = hub.get_integration(DjangoIntegration)
        if integration is None or integration.enable_tracing_meta_tags is False:
            return original_content.fset(self, value)

        meta_tags = hub.trace_propagation_meta()
        new_value = _ireplace(
            "</head>", "%s\\g<matched_string>" % meta_tags, value, self.make_bytes
        )
        return original_content.fset(self, new_value)

    HttpResponse.content = content


def patch_templates():
    # type: () -> None
    enable_tracing_meta_tags()

    from django.template.response import SimpleTemplateResponse
    from sentry_sdk.integrations.django import DjangoIntegration

    real_rendered_content = SimpleTemplateResponse.rendered_content

    @property  # type: ignore
    def rendered_content(self):
        # type: (SimpleTemplateResponse) -> str
        hub = Hub.current
        if hub.get_integration(DjangoIntegration) is None:
            return real_rendered_content.fget(self)

        with hub.start_span(
            op=OP.TEMPLATE_RENDER,
            description=_get_template_name_description(self.template_name),
        ) as span:
            span.set_data("context", self.context_data)
            return real_rendered_content.fget(self)

    SimpleTemplateResponse.rendered_content = rendered_content

    if DJANGO_VERSION < (1, 7):
        return
    import django.shortcuts

    real_render = django.shortcuts.render

    @_functools.wraps(real_render)
    def render(request, template_name, context=None, *args, **kwargs):
        # type: (django.http.HttpRequest, str, Optional[Dict[str, Any]], *Any, **Any) -> django.http.HttpResponse
        hub = Hub.current
        if hub.get_integration(DjangoIntegration) is None:
            return real_render(request, template_name, context, *args, **kwargs)

        with hub.start_span(
            op=OP.TEMPLATE_RENDER,
            description=_get_template_name_description(template_name),
        ) as span:
            span.set_data("context", context)
            return real_render(request, template_name, context, *args, **kwargs)

    django.shortcuts.render = render


def _get_template_frame_from_debug(debug):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    if debug is None:
        return None

    lineno = debug["line"]
    filename = debug["name"]
    if filename is None:
        filename = "<django template>"

    pre_context = []
    post_context = []
    context_line = None

    for i, line in debug["source_lines"]:
        if i < lineno:
            pre_context.append(line)
        elif i > lineno:
            post_context.append(line)
        else:
            context_line = line

    return {
        "filename": filename,
        "lineno": lineno,
        "pre_context": pre_context[-5:],
        "post_context": post_context[:5],
        "context_line": context_line,
        "in_app": True,
    }


def _linebreak_iter(template_source):
    # type: (str) -> Iterator[int]
    yield 0
    p = template_source.find("\n")
    while p >= 0:
        yield p + 1
        p = template_source.find("\n", p + 1)


def _get_template_frame_from_source(source):
    # type: (Tuple[Origin, Tuple[int, int]]) -> Optional[Dict[str, Any]]
    if not source:
        return None

    origin, (start, end) = source
    filename = getattr(origin, "loadname", None)
    if filename is None:
        filename = "<django template>"
    template_source = origin.reload()
    lineno = None
    upto = 0
    pre_context = []
    post_context = []
    context_line = None

    for num, next in enumerate(_linebreak_iter(template_source)):
        line = template_source[upto:next]
        if start >= upto and end <= next:
            lineno = num
            context_line = line
        elif lineno is None:
            pre_context.append(line)
        else:
            post_context.append(line)

        upto = next

    if context_line is None or lineno is None:
        return None

    return {
        "filename": filename,
        "lineno": lineno,
        "pre_context": pre_context[-5:],
        "post_context": post_context[:5],
        "context_line": context_line,
    }
