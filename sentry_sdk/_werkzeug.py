"""
Copyright (c) 2007 by the Pallets team.

Some rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

* Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the
  documentation and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE AND DOCUMENTATION IS PROVIDED BY THE COPYRIGHT HOLDERS AND
CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING,
BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE AND DOCUMENTATION, EVEN IF ADVISED OF THE POSSIBILITY OF
SUCH DAMAGE.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict
    from typing import Iterator
    from typing import Tuple


#
# `get_headers` comes from `werkzeug.datastructures.headers.__iter__`
# https://github.com/pallets/werkzeug/blob/3.1.3/src/werkzeug/datastructures/headers.py#L644
#
# We need this function because Django does not give us a "pure" http header
# dict. So we might as well use it for all WSGI integrations.
#
def _get_headers(environ: "Dict[str, str]") -> "Iterator[Tuple[str, str]]":
    for key, value in environ.items():
        if key.startswith("HTTP_") and key not in {
            "HTTP_CONTENT_TYPE",
            "HTTP_CONTENT_LENGTH",
        }:
            yield key[5:].replace("_", "-").title(), value
        elif key in {"CONTENT_TYPE", "CONTENT_LENGTH"} and value:
            yield key.replace("_", "-").title(), value


#
# `get_host` comes from `werkzeug.wsgi.get_host`
# https://github.com/pallets/werkzeug/blob/3.1.3/src/werkzeug/wsgi.py#L86
#
def get_host(environ: "Dict[str, str]", use_x_forwarded_for: bool = False) -> str:
    """
    Return the host for the given WSGI environment.
    """
    return _get_host(
        environ["wsgi.url_scheme"],
        (
            environ["HTTP_X_FORWARDED_HOST"]
            if use_x_forwarded_for and environ.get("HTTP_X_FORWARDED_HOST")
            else environ.get("HTTP_HOST")
        ),
        _get_server(environ),
    )


# `_get_host` comes from `werkzeug.sansio.utils`
# https://github.com/pallets/werkzeug/blob/3.1.3/src/werkzeug/sansio/utils.py#L49
def _get_host(
    scheme,
    host_header,
    server=None,
):
    # type: (str, str | None, Tuple[str, int | None] | None) -> str
    """
    Return the host for the given parameters.
    """
    host = ""

    if host_header is not None:
        host = host_header
    elif server is not None:
        host = server[0]

        # If SERVER_NAME is IPv6, wrap it in [] to match Host header.
        # Check for : because domain or IPv4 can't have that.
        if ":" in host and host[0] != "[":
            host = f"[{host}]"

        if server[1] is not None:
            host = f"{host}:{server[1]}"  # noqa: E231

    if scheme in {"http", "ws"} and host.endswith(":80"):
        host = host[:-3]
    elif scheme in {"https", "wss"} and host.endswith(":443"):
        host = host[:-4]

    return host


def _get_server(environ):
    # type: (Dict[str, str]) -> Tuple[str, int | None] | None
    name = environ.get("SERVER_NAME")

    if name is None:
        return None

    try:
        port = int(environ.get("SERVER_PORT", None))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # unix socket
        port = None

    return name, port
