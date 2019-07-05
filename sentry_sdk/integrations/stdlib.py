import os
import subprocess
import sys
import platform

from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.tracing import EnvironHeaders, record_http_request

try:
    from httplib import HTTPConnection  # type: ignore
except ImportError:
    from http.client import HTTPConnection

_RUNTIME_CONTEXT = {
    "name": platform.python_implementation(),
    "version": "%s.%s.%s" % (sys.version_info[:3]),
    "build": sys.version,
}


class StdlibIntegration(Integration):
    identifier = "stdlib"

    @staticmethod
    def setup_once():
        # type: () -> None
        _install_httplib()
        _install_subprocess()

        @add_global_event_processor
        def add_python_runtime_context(event, hint):
            if Hub.current.get_integration(StdlibIntegration) is not None:
                contexts = event.setdefault("contexts", {})
                if isinstance(contexts, dict) and "runtime" not in contexts:
                    contexts["runtime"] = _RUNTIME_CONTEXT

            return event


def _install_httplib():
    # type: () -> None
    real_putrequest = HTTPConnection.putrequest
    real_getresponse = HTTPConnection.getresponse

    def putrequest(self, method, url, *args, **kwargs):
        hub = Hub.current
        if hub.get_integration(StdlibIntegration) is None:
            return real_putrequest(self, method, url, *args, **kwargs)

        host = self.host
        port = self.port
        default_port = self.default_port

        real_url = url
        if not real_url.startswith(("http://", "https://")):
            real_url = "%s://%s%s%s" % (
                default_port == 443 and "https" or "http",
                host,
                port != default_port and ":%s" % port or "",
                url,
            )

        recorder = record_http_request(hub, real_url, method)
        data_dict = recorder.__enter__()

        try:
            rv = real_putrequest(self, method, url, *args, **kwargs)

            for key, value in hub.iter_trace_propagation_headers():
                self.putheader(key, value)
        except Exception:
            recorder.__exit__(*sys.exc_info())
            raise

        self._sentrysdk_recorder = recorder
        self._sentrysdk_data_dict = data_dict

        return rv

    def getresponse(self, *args, **kwargs):
        recorder = getattr(self, "_sentrysdk_recorder", None)

        if recorder is None:
            return real_getresponse(self, *args, **kwargs)

        data_dict = getattr(self, "_sentrysdk_data_dict", None)

        try:
            rv = real_getresponse(self, *args, **kwargs)

            if data_dict is not None:
                data_dict["httplib_response"] = rv
                data_dict["status_code"] = rv.status
                data_dict["reason"] = rv.reason
        except TypeError:
            # python-requests provokes a typeerror to discover py3 vs py2 differences
            #
            # > TypeError("getresponse() got an unexpected keyword argument 'buffering'")
            raise
        except Exception:
            recorder.__exit__(*sys.exc_info())
            raise
        else:
            recorder.__exit__(None, None, None)

        return rv

    HTTPConnection.putrequest = putrequest
    HTTPConnection.getresponse = getresponse


def _get_argument(args, kwargs, name, position, setdefault=None):
    if name in kwargs:
        rv = kwargs[name]
        if rv is None and setdefault is not None:
            rv = kwargs[name] = setdefault
    elif position < len(args):
        rv = args[position]
        if rv is None and setdefault is not None:
            rv = args[position] = setdefault
    else:
        rv = kwargs[name] = setdefault

    return rv


def _install_subprocess():
    old_popen_init = subprocess.Popen.__init__

    def sentry_patched_popen_init(self, *a, **kw):
        hub = Hub.current
        if hub.get_integration(StdlibIntegration) is None:
            return old_popen_init(self, *a, **kw)

        # do not setdefault! args is required by Popen, doing setdefault would
        # make invalid calls valid
        args = _get_argument(a, kw, "args", 0) or []
        cwd = _get_argument(a, kw, "cwd", 10)

        for k, v in hub.iter_trace_propagation_headers():
            env = _get_argument(a, kw, "env", 11, {})
            env["SUBPROCESS_" + k.upper().replace("-", "_")] = v

        with hub.span(op="subprocess", description=" ".join(map(str, args))) as span:
            span.set_tag("subprocess.cwd", cwd)

            return old_popen_init(self, *a, **kw)

    subprocess.Popen.__init__ = sentry_patched_popen_init  # type: ignore


def get_subprocess_traceparent_headers():
    return EnvironHeaders(os.environ, prefix="SUBPROCESS_")
