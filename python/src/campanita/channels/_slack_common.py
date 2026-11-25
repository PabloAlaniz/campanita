"""Lo de Slack que usan el canal y la conexión por OAuth."""

from __future__ import annotations

import re

_WEBHOOK = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{1,200}")
#: Los errores de Slack son una palabra en minúsculas: sólo eso se repite.
SLACK_ERROR = re.compile(r"[a-z0-9_]{1,60}")


def is_slack_webhook_url(url: object) -> bool:
    """¿Es la URL de un webhook de Slack?

    Estricta a propósito: sólo `https://hooks.slack.com/services/…`, sin
    usuario, puerto, consulta ni fragmento. Así una URL guardada no sirve para
    hacer pedidos a otro servidor.
    """
    return isinstance(url, str) and _WEBHOOK.fullmatch(url) is not None
