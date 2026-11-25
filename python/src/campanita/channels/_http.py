"""Lo que comparten los canales que hablan HTTP con la biblioteca estándar."""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

_HTTP_OK, _HTTP_REDIRECT = 200, 300

#: Cómo se hace un `POST`: `(url, cuerpo, cabeceras, segundos) -> (código, cuerpo)`.
#: Se puede reemplazar por un cliente HTTP propio.
HttpPost = Callable[[str, bytes, Mapping[str, str], float], "tuple[int, bytes]"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """No sigue redirecciones: una redirección reenviaría las cabeceras, con sus
    credenciales, a otro servidor."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def build_opener() -> urllib.request.OpenerDirector:
    """Un `opener` de `urllib` que no sigue redirecciones."""
    return urllib.request.build_opener(NoRedirect)


_OPENER = build_opener()


def is_success(status: int) -> bool:
    """Si un código HTTP es 2xx."""
    return _HTTP_OK <= status < _HTTP_REDIRECT


def http_post(url: str, data: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """El `POST` por omisión, con la biblioteca estándar. No sigue redirecciones.

    Sólo acepta URLs http o https: `urllib` también abriría `file:` y otros esquemas.
    """
    if urlsplit(url).scheme not in ("https", "http"):
        raise ValueError("`url` tiene que ser una URL http o https")
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")  # noqa: S310
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
