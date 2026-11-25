"""El canal de Slack, por «incoming webhook».

Cada persona conecta un canal de Slack y el aviso sale ahí. La conexión se hace
con OAuth (`slack_authorize_url` y `exchange_slack_code`): la persona elige el
canal en la pantalla de Slack, entre los que puede usar, y nunca ve ni pega un
token. Lo que queda es una URL secreta por persona; la guarda quien integra, y
`SlackChannel` se la pide cuando hay algo que entregar.

https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .._inbox import Notification
from .._types import maybe_await
from ..ports import DeliveryResult
from ._http import HttpPost, http_post, is_success
from ._slack_common import SLACK_ERROR, is_slack_webhook_url
from ._text import CONTROL

# Lo de OAuth vive en `slack_oauth`; se sigue pudiendo importar desde acá.
from .slack_oauth import (
    SlackConnection,
    SlackOAuthError,
    exchange_slack_code,
    parse_slack_oauth_response,
    slack_authorize_url,
)

#: Un enlace que Slack puede mostrar: http o https, sin lo que cierra o parte la marca.
_LINK = re.compile(r"https?://[^\x00-\x20\x7f<>|]+")


def escape_slack_text(text: str) -> str:
    """Lo que Slack pide escapar en un texto: `&`, `<` y `>`."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_slack(notification: Notification) -> dict[str, Any]:
    """El mensaje por omisión (el cuerpo JSON del webhook), una línea por parte:

    - el título en negrita, con cada tramo de caracteres de control hecho un espacio;
    - el cuerpo, si hay;
    - el enlace, si es una URL http o https que Slack puede mostrar.

    El texto del aviso sale escapado: no puede armar menciones ni enlaces.
    """
    title = escape_slack_text(CONTROL.sub(" ", notification.title).strip(" "))
    lines = [f"*{title}*"] if title else []
    if notification.body:
        lines.append(escape_slack_text(notification.body))
    url = notification.action_url
    if isinstance(url, str) and _LINK.fullmatch(url):
        lines.append(f"<{url.replace('&', '&amp;')}>")
    return {"text": "\n".join(lines)}


#: De un destinatario a la URL de su webhook, o `None` si no conectó Slack. La
#: escribe quien integra: campanita no guarda conexiones. Puede ser `async`.
WebhookResolver = Callable[[str], "str | Awaitable[str | None] | None"]

#: Lo mismo, a partir del aviso entero: sirve para elegir el destino según el
#: ámbito, cuando una persona o un grupo tiene un canal distinto en cada uno.
NotificationWebhookResolver = Callable[[Notification], "str | Awaitable[str | None] | None"]


class SlackChannel:
    """Avisa en el canal de Slack que conectó cada persona.

    Cumple `campanita.ports.DeliveryChannel`.

    - Sin conexión, o con una URL que no es de Slack: `skipped`.
    - Slack acepta el mensaje: `sent`.
    - Slack contesta 404 o 410 (el webhook se quitó, el canal se archivó):
      `expired`. La conexión ya no sirve y conviene pedirle a la persona que
      conecte de nuevo.
    - Cualquier otra cosa: `failed`.

    Ningún error repite la URL del webhook: es un secreto.
    """

    def __init__(
        self,
        *,
        resolve_webhook: WebhookResolver | None = None,
        resolve_webhook_for: NotificationWebhookResolver | None = None,
        render: Callable[[Notification], Mapping[str, Any]] = render_slack,
        id: str = "slack",
        timeout: float = 10.0,
        post: HttpPost = http_post,
    ) -> None:
        if (resolve_webhook is None) == (resolve_webhook_for is None):
            raise TypeError("pasá `resolve_webhook` o `resolve_webhook_for`, uno de los dos")
        self.id = id
        self._resolve: NotificationWebhookResolver = (
            resolve_webhook_for if resolve_webhook_for is not None else _by_recipient(resolve_webhook)  # type: ignore[arg-type]
        )
        self._render = render
        self._timeout = timeout
        self._post = post

    async def send(self, notification: Notification) -> DeliveryResult:
        try:
            found = self._resolve(notification)
            url = await maybe_await(found)
        except Exception as error:
            return DeliveryResult(
                "failed", error=f"no se pudo buscar la conexión de Slack: {type(error).__name__}"
            )
        if url is None:
            return DeliveryResult("skipped", error="la persona no conectó Slack")
        if not is_slack_webhook_url(url):
            return DeliveryResult("skipped", error="la URL guardada no es un webhook de Slack")

        try:
            data = json.dumps(self._render(notification), ensure_ascii=False).encode("utf-8")
        except Exception as error:
            return DeliveryResult("failed", error=f"no se pudo armar el mensaje: {type(error).__name__}")

        try:
            status, raw = await asyncio.to_thread(
                self._post, url, data, {"Content-Type": "application/json"}, self._timeout
            )
        except Exception as error:
            # Sólo el tipo: el mensaje de un error de red puede traer la URL.
            return DeliveryResult("failed", error=f"Slack: {type(error).__name__}")
        return _webhook_result(status, raw)


def _webhook_result(status: int, raw: bytes) -> DeliveryResult:
    if is_success(status):
        return DeliveryResult("sent")
    text = raw.decode("utf-8", "replace").strip(" \t\r\n")
    detail = f" {text}" if SLACK_ERROR.fullmatch(text) else ""
    return DeliveryResult("expired" if status in (404, 410) else "failed", error=f"Slack {status}{detail}")


def _by_recipient(resolve: WebhookResolver) -> NotificationWebhookResolver:
    return lambda notification: resolve(notification.recipient_id)


__all__ = [
    "HttpPost",
    "NotificationWebhookResolver",
    "SlackChannel",
    "SlackConnection",
    "SlackOAuthError",
    "WebhookResolver",
    "escape_slack_text",
    "exchange_slack_code",
    "http_post",
    "is_slack_webhook_url",
    "parse_slack_oauth_response",
    "render_slack",
    "slack_authorize_url",
]
