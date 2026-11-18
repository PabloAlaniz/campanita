"""Transportes de email por HTTP, con la biblioteca estándar."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from ..ports import DeliveryResult
from .email import EmailMessage


def default_body(message: EmailMessage) -> dict[str, Any]:
    """`{from, to, subject, text}` y `html` si hay."""
    body: dict[str, Any] = {
        "from": message.formatted_sender,
        "to": message.to,
        "subject": message.subject,
        "text": message.text,
    }
    if message.html is not None:
        body["html"] = message.html
    return body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Una redirección se informa como error en vez de seguirse: seguirla
    reenviaría las cabeceras, con sus credenciales, a otro servidor."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpEmailTransport:
    """Manda el mensaje como JSON por `POST` a un servicio de email.

    Sirve para cualquier servicio con una API HTTP: se le dice la URL, las
    cabeceras (ahí van las credenciales) y, si el servicio espera otra forma,
    cómo armar el cuerpo.

    - `build_body(mensaje)`: el JSON a mandar. Por omisión, `default_body`.
    - `build_headers(mensaje)`: cabeceras que dependen del mensaje.
    - `read_id(respuesta)`: de la respuesta JSON al identificador del mensaje.

    Una respuesta 2xx es `sent`; cualquier otra cosa, `failed`.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        build_body: Callable[[EmailMessage], Mapping[str, Any]] = default_body,
        build_headers: Callable[[EmailMessage], Mapping[str, str]] | None = None,
        read_id: Callable[[Any], str | None] | None = None,
        timeout: float = 10.0,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.netloc:
            raise ValueError("`url` tiene que ser una URL http o https")
        self._url = url
        self._headers = dict(headers or {})
        self._build_body = build_body
        self._build_headers = build_headers
        self._read_id = read_id
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    async def send(self, message: EmailMessage) -> DeliveryResult:
        return await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> DeliveryResult:
        try:
            headers = {**self._headers, **(self._build_headers(message) if self._build_headers else {})}
            headers["Content-Type"] = "application/json"
            data = json.dumps(self._build_body(message), ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(self._url, data=data, headers=headers, method="POST")
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            # Sólo el código: el cuerpo del error es del servicio y puede repetir datos del pedido.
            return DeliveryResult("failed", error=f"HTTP {error.code}")
        except Exception as error:
            return DeliveryResult("failed", error=f"HTTP: {type(error).__name__}")
        return DeliveryResult("sent", provider_message_id=self._message_id(raw))

    def _message_id(self, raw: bytes) -> str | None:
        if self._read_id is None:
            return None
        try:
            found = self._read_id(json.loads(raw))
        except Exception:
            return None
        return found if isinstance(found, str) else None


class ResendTransport(HttpEmailTransport):
    """Manda por la API de Resend (https://resend.com/docs/api-reference/emails/send-email).

    Usa el identificador del aviso como `Idempotency-Key`: reintentar un envío
    no hace llegar el mensaje dos veces.
    """

    def __init__(
        self, api_key: str, *, base_url: str = "https://api.resend.com", timeout: float = 10.0
    ) -> None:
        if not api_key:
            raise ValueError("falta `api_key`")
        super().__init__(
            f"{base_url.rstrip('/')}/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            build_body=lambda message: {**default_body(message), "to": [message.to]},
            build_headers=lambda message: (
                {} if message.idempotency_key is None else {"Idempotency-Key": message.idempotency_key}
            ),
            read_id=lambda response: response.get("id") if isinstance(response, dict) else None,
            timeout=timeout,
        )
