"""Conectar Slack con OAuth: la persona elige un canal y queda una URL de webhook.

La persona elige el canal en la pantalla de Slack, entre los que puede usar, y nunca
ve ni pega un token. Lo que queda es una URL secreta por persona, que guarda quien
integra y `SlackChannel` usa para entregar.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlencode

from ._http import HttpPost, http_post, is_success
from ._slack_common import SLACK_ERROR, is_slack_webhook_url

_SCOPE = "incoming-webhook"
_AUTHORIZE = "https://slack.com/oauth/v2/authorize"
_ACCESS = "https://slack.com/api/oauth.v2.access"


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
        if isinstance(reason, str) and SLACK_ERROR.fullmatch(reason):
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
    # Es el plazo de cada pedido HTTP, que corre en un hilo; no el de la corrutina.
    timeout: float = 10.0,  # noqa: ASYNC109
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
    if not is_success(status):
        raise SlackOAuthError("http")
    try:
        parsed = json.loads(raw)
    except Exception:
        raise SlackOAuthError("invalid_response") from None
    return parse_slack_oauth_response(parsed)
