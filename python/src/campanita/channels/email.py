"""El canal de email.

Una cosa es avisar por email y otra es con qué se manda. `EmailChannel` busca la
dirección de la persona y arma el mensaje; el envío lo hace un `EmailTransport`
(SMTP, un servicio por HTTP…), que se cambia sin tocar el resto.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .._inbox import Notification
from ..ports import DeliveryResult

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
#: Lo que una dirección no puede tener además de espacios, controles y no ASCII.
_FORBIDDEN = frozenset('<>,;:"()[]\\')
_MAX_ADDRESS = 254


def is_valid_address(address: object) -> bool:
    """La regla del spec. Es estricta a propósito: una dirección que pasa no
    puede partir una cabecera ni esconder un segundo destinatario."""
    if not isinstance(address, str) or not 3 <= len(address) <= _MAX_ADDRESS:
        return False
    if any(not "!" <= char <= "~" or char in _FORBIDDEN for char in address):
        return False
    local, at, domain = address.partition("@")
    return at == "@" and "@" not in domain and local != "" and domain != ""


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """Lo que un aviso dice por email."""

    subject: str
    text: str
    html: str | None = None


def render_email(notification: Notification) -> RenderedEmail:
    """El mensaje por omisión, el del spec.

    - asunto: el título, con cada tramo de caracteres de control hecho un espacio;
    - texto: el cuerpo y el enlace, con lo que haya; si no hay ninguno, el título.
    """
    parts = [part for part in (notification.body, notification.action_url) if part]
    return RenderedEmail(
        subject=_CONTROL.sub(" ", notification.title),
        text="\n\n".join(parts) if parts else notification.title,
    )


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Un mensaje listo para mandar. Es lo que recibe un transporte."""

    to: str
    sender: str
    subject: str
    text: str
    html: str | None = None
    #: El nombre que acompaña a la dirección del remitente.
    sender_name: str | None = None
    #: El identificador del aviso. Un transporte lo puede usar para que mandar
    #: dos veces el mismo mensaje no llegue dos veces.
    idempotency_key: str | None = None

    @property
    def formatted_sender(self) -> str:
        """`Nombre <dirección>`, o la dirección sola."""
        return self.sender if self.sender_name is None else f"{self.sender_name} <{self.sender}>"


class EmailTransport(Protocol):
    """Con qué se manda un mensaje.

    `send` no debería lanzar: informa `sent` o `failed`. Si igual lanza,
    `EmailChannel` lo anota como `failed`.
    """

    async def send(self, message: EmailMessage) -> DeliveryResult: ...


#: De un destinatario a su dirección, o `None` si no tiene. La escribe quien
#: integra: campanita no guarda direcciones. Puede ser una función común o `async`.
AddressResolver = Callable[[str], "str | Awaitable[str | None] | None"]

#: Lo mismo, a partir del aviso entero: sirve para elegir la dirección según el ámbito.
NotificationAddressResolver = Callable[[Notification], "str | Awaitable[str | None] | None"]


class EmailChannel:
    """Avisa por email. Cumple `campanita.ports.DeliveryChannel`."""

    def __init__(
        self,
        *,
        transport: EmailTransport,
        sender: str,
        resolve_address: AddressResolver | None = None,
        resolve_address_for: NotificationAddressResolver | None = None,
        sender_name: str | None = None,
        render: Callable[[Notification], RenderedEmail] = render_email,
        id: str = "email",
    ) -> None:
        if (resolve_address is None) == (resolve_address_for is None):
            raise TypeError("pasá `resolve_address` o `resolve_address_for`, uno de los dos")
        if not is_valid_address(sender):
            raise ValueError("`sender` no es una dirección válida")
        if sender_name is not None and (
            not sender_name.strip() or _CONTROL.search(sender_name) or _FORBIDDEN & set(sender_name)
        ):
            raise ValueError(
                "`sender_name` no puede estar vacío ni tener caracteres de control o de dirección"
            )
        self.id = id
        self._transport = transport
        self._sender = sender
        self._sender_name = sender_name
        self._resolve: NotificationAddressResolver = (
            resolve_address_for if resolve_address_for is not None else _by_recipient(resolve_address)  # type: ignore[arg-type]
        )
        self._render = render

    async def send(self, notification: Notification) -> DeliveryResult:
        try:
            found = self._resolve(notification)
            address = await found if inspect.isawaitable(found) else found
        except Exception as error:
            return DeliveryResult("failed", error=f"no se pudo buscar la dirección: {type(error).__name__}")
        if address is None:
            return DeliveryResult("skipped", error="la persona no tiene dirección de email")
        if not is_valid_address(address):
            return DeliveryResult("skipped", error="la dirección de email no es válida")

        try:
            rendered = self._render(notification)
            message = EmailMessage(
                to=address,
                sender=self._sender,
                sender_name=self._sender_name,
                # El asunto va a una cabecera: nunca sale con saltos de línea,
                # tampoco si lo armó una función propia.
                subject=_CONTROL.sub(" ", rendered.subject),
                text=rendered.text,
                html=rendered.html,
                idempotency_key=notification.id,
            )
            return await self._transport.send(message)
        except Exception as error:
            return DeliveryResult("failed", error=f"{type(error).__name__}: {error}")


def _by_recipient(resolve: AddressResolver) -> NotificationAddressResolver:
    return lambda notification: resolve(notification.recipient_id)
