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
import inspect
import json
import re
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from .._inbox import Notification
from ..ports import DeliveryResult

_WEBHOOK = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{1,200}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
#: Un enlace que Slack puede mostrar: http o https, sin lo que cierra o parte la marca.
_LINK = re.compile(r"https?://[^\x00-\x20\x7f<>|]+")
#: Los errores de Slack son una palabra en minúsculas: sólo eso se repite.
_SLACK_ERROR = re.compile(r"[a-z0-9_]{1,60}")
_SCOPE = "incoming-webhook"
_AUTHORIZE = "https://slack.com/oauth/v2/authorize"
_ACCESS = "https://slack.com/api/oauth.v2.access"

#: Cómo se hace un `POST`: `(url, cuerpo, cabeceras, segundos) -> (código, cuerpo)`.
#: Se puede reemplazar por un cliente HTTP propio.
HttpPost = Callable[[str, bytes, Mapping[str, str], float], "tuple[int, bytes]"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_post(url: str, data: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """El `POST` por omisión, con la biblioteca estándar. No sigue redirecciones."""
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def is_slack_webhook_url(url: object) -> bool:
    """¿Es la URL de un webhook de Slack?

    Estricta a propósito: sólo `https://hooks.slack.com/services/…`, sin
    usuario, puerto, consulta ni fragmento. Así una URL guardada no sirve para
    hacer pedidos a otro servidor.
    """
    return isinstance(url, str) and _WEBHOOK.fullmatch(url) is not None


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
    title = escape_slack_text(_CONTROL.sub(" ", notification.title).strip(" "))
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
        resolve_webhook: WebhookResolver,
        render: Callable[[Notification], Mapping[str, Any]] = render_slack,
        id: str = "slack",
        timeout: float = 10.0,
        post: HttpPost = http_post,
    ) -> None:
        self.id = id
        self._resolve_webhook = resolve_webhook
        self._render = render
        self._timeout = timeout
        self._post = post

    async def send(self, notification: Notification) -> DeliveryResult:
        try:
            found = self._resolve_webhook(notification.recipient_id)
            url = await found if inspect.isawaitable(found) else found
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
        if 200 <= status < 300:
            return DeliveryResult("sent")

        text = raw.decode("utf-8", "replace").strip(" \t\r\n")
        detail = f" {text}" if _SLACK_ERROR.fullmatch(text) else ""
        return DeliveryResult(
            "expired" if status in (404, 410) else "failed", error=f"Slack {status}{detail}"
        )


# --- conectar con OAuth -------------------------------------------------------


def slack_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """La URL de Slack a la que se manda a la persona para que elija un canal.

    `state` es un valor de quien integra, atado a la sesión de la persona e
    imposible de adivinar: al volver hay que comprobar que es el mismo. Sin eso,
    otra persona podría conectar su canal a una cuenta ajena.
    """
    for name, value in (("client_id", client_id), ("redirect_uri", redirect_uri), ("state", state)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"falta `{name}`")
    query = urlencode({"client_id": client_id, "scope": _SCOPE, "redirect_uri": redirect_uri, "state": state})
    return f"{_AUTHORIZE}?{query}"


@dataclass(frozen=True, slots=True)
class SlackConnection:
    """Lo que queda de una conexión. `webhook_url` es un secreto."""

    webhook_url: str
    #: El nombre del canal elegido, como lo muestra Slack (`#general`).
    channel: str | None = None
    channel_id: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    #: Dónde la persona administra o quita el webhook en Slack.
    configuration_url: str | None = None

    def __repr__(self) -> str:
        return f"SlackConnection(channel={self.channel!r}, team_name={self.team_name!r})"


class SlackOAuthError(Exception):
    """No se pudo terminar la conexión.

    `reason` es el error que dio Slack (`invalid_code`, `access_denied`…) o uno
    propio: `network`, `http`, `invalid_response` o `no_webhook`.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"no se pudo conectar con Slack: {reason}")
        self.reason = reason


def _text(container: object, key: str) -> str | None:
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, str) and value else None


def parse_slack_oauth_response(response: object) -> SlackConnection:
    """De la respuesta de `oauth.v2.access` a una conexión. Lanza `SlackOAuthError`."""
    if not isinstance(response, dict):
        raise SlackOAuthError("invalid_response")
    if response.get("ok") is not True:
        reason = response.get("error")
        if isinstance(reason, str) and _SLACK_ERROR.fullmatch(reason):
            raise SlackOAuthError(reason)
        raise SlackOAuthError("invalid_response")
    webhook = response.get("incoming_webhook")
    url = webhook.get("url") if isinstance(webhook, dict) else None
    if not isinstance(url, str) or not is_slack_webhook_url(url):
        raise SlackOAuthError("no_webhook")
    team = response.get("team")
    return SlackConnection(
        webhook_url=url,
        channel=_text(webhook, "channel"),
        channel_id=_text(webhook, "channel_id"),
        team_id=_text(team, "id"),
        team_name=_text(team, "name"),
        configuration_url=_text(webhook, "configuration_url"),
    )


async def exchange_slack_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    timeout: float = 10.0,
    post: HttpPost = http_post,
) -> SlackConnection:
    """Cambia el `code` con el que Slack devuelve a la persona por su conexión.

    `redirect_uri` tiene que ser el mismo que se usó en `slack_authorize_url`.
    Lanza `SlackOAuthError`; ningún error repite el secreto ni el código.
    """
    fields = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    for name, value in fields.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"falta `{name}`")
    try:
        status, raw = await asyncio.to_thread(
            post,
            _ACCESS,
            urlencode(fields).encode("ascii"),
            {"Content-Type": "application/x-www-form-urlencoded"},
            timeout,
        )
    except Exception:
        raise SlackOAuthError("network") from None
    if not 200 <= status < 300:
        raise SlackOAuthError("http")
    try:
        parsed = json.loads(raw)
    except Exception:
        raise SlackOAuthError("invalid_response") from None
    return parse_slack_oauth_response(parsed)
