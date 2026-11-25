"""El canal de push: avisa en los dispositivos de cada persona.

Una persona tiene varios dispositivos (un teléfono, dos navegadores) y cada uno
se alcanza por un servicio distinto. `PushChannel` separa las dos cosas: le pide
los dispositivos a una función de quien integra, arma un mensaje y se lo pasa,
por cada dispositivo, al **transporte** que le corresponde. Un transporte es un
puerto más chico: `send(destino, mensaje)`.

campanita no guarda dispositivos: registrarlos y olvidarlos es de quien integra.

Este paquete trae el canal; los transportes incluidos (APNs y Web Push) están,
por ahora, sólo en el de TypeScript. Uno propio cumple `PushTransport`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .._inbox import Notification
from .._types import maybe_await
from ..ports import DeliveryOutcome, DeliveryResult, is_delivery_result
from ._text import CONTROL


@dataclass(frozen=True, slots=True)
class PushMessage:
    """Lo que un aviso dice en un dispositivo. Cada transporte lo traduce a su servicio."""

    title: str
    body: str | None = None
    #: A dónde lleva tocar el aviso. Es opaca: la interpreta quien lo recibe.
    url: str | None = None
    #: Los avisos con el mismo hilo se agrupan o se reemplazan, según el servicio.
    thread: str | None = None
    #: Datos que viajan con el aviso, para quien lo recibe.
    data: Mapping[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PushDevice:
    """Un dispositivo: por qué transporte se alcanza y lo que ese transporte
    necesita para llegar (un token, una suscripción). `target` es del
    transporte; el canal no lo mira.
    """

    #: La clave del transporte en `transports`.
    transport: str
    target: Any


class PushTransport(Protocol):
    """Cualquier servicio de push. Manda UN mensaje a UN destino y contesta
    como un canal: `sent`, `expired` (el destino ya no existe), `failed` o
    `skipped` (el destino no tiene la forma que el servicio espera).

    No debería lanzar. Si igual lanza, el canal lo cuenta como `failed`.
    """

    async def send(self, target: Any, message: PushMessage) -> DeliveryResult: ...


def render_push(notification: Notification) -> PushMessage:
    """El mensaje por omisión:

    - el título, con cada tramo de caracteres de control hecho un espacio;
    - el cuerpo y la dirección, si no están vacíos;
    - sin hilo;
    - en los datos, el identificador y el tipo del aviso, para que quien lo
      recibe pueda abrirlo o marcarlo como leído.

    El texto no se escapa: lo muestra el sistema, no un intérprete de marcas.
    """
    return PushMessage(
        title=CONTROL.sub(" ", notification.title).strip(" "),
        body=notification.body or None,
        url=notification.action_url or None,
        thread=None,
        data={"notification_id": notification.id, "type": notification.type},
    )


def summarize_push(outcomes: Sequence[DeliveryOutcome]) -> DeliveryOutcome:
    """De lo que pasó en cada dispositivo a lo que pasó con el aviso:

    - alguno lo recibió: `sent`;
    - ninguno, y alguno falló: `failed` (reintentar puede servir);
    - ninguno, y alguno venció: `expired`;
    - no había dispositivos, o ninguno tenía transporte: `skipped`.
    """
    order: tuple[DeliveryOutcome, ...] = ("sent", "failed", "expired")
    return next((outcome for outcome in order if outcome in outcomes), "skipped")


#: Del aviso a los dispositivos que lo tienen que recibir. La escribe quien
#: integra. Recibe el aviso entero: puede elegir según el destinatario, el
#: ámbito o la dirección a la que lleva. Puede ser `async`.
DeviceResolver = Callable[
    [Notification], "Sequence[PushDevice] | Awaitable[Sequence[PushDevice] | None] | None"
]

#: Qué pasó con cada dispositivo. Con `expired`, el dispositivo ya no existe y
#: hay que olvidarlo. Puede ser `async`; si lanza, no cambia el resultado.
ResultHook = Callable[[PushDevice, DeliveryResult, Notification], "Awaitable[None] | None"]


class PushChannel:
    """Avisa en los dispositivos de cada persona.

    Cumple `campanita.ports.DeliveryChannel`.

    Cada dispositivo se resuelve por separado: uno roto no impide avisar al
    resto. Un dispositivo cuya clave no está en `transports` se saltea: así un
    servicio sin configurar no impide avisar por los demás. El resultado del
    canal es el de `summarize_push`; el de cada dispositivo llega por
    `on_result`.
    """

    def __init__(
        self,
        *,
        transports: Mapping[str, PushTransport],
        resolve_devices: DeviceResolver,
        on_result: ResultHook | None = None,
        render: Callable[[Notification], PushMessage] = render_push,
        id: str = "push",
    ) -> None:
        if not callable(resolve_devices):
            raise TypeError("falta `resolve_devices`")
        if not isinstance(transports, Mapping):
            raise TypeError("falta `transports`")
        self.id = id
        self._transports = transports
        self._resolve = resolve_devices
        self._on_result = on_result
        self._render = render

    async def send(self, notification: Notification) -> DeliveryResult:
        try:
            found: Sequence[PushDevice] | None = await maybe_await(self._resolve(notification))
            devices = list(found or ())
        except Exception as error:
            return DeliveryResult(
                "failed", error=f"no se pudieron buscar los dispositivos: {type(error).__name__}"
            )
        if not devices:
            return DeliveryResult("skipped", error="la persona no tiene dispositivos")

        try:
            message = self._render(notification)
        except Exception as error:
            return DeliveryResult("failed", error=f"no se pudo armar el mensaje: {type(error).__name__}")

        outcomes = await asyncio.gather(*(self._deliver(device, message, notification) for device in devices))
        outcome = summarize_push(outcomes)
        if outcome == "sent":
            return DeliveryResult("sent")
        return DeliveryResult(outcome, error=f"push: no lo recibió ninguno de {len(devices)} dispositivos")

    async def _deliver(
        self, device: PushDevice, message: PushMessage, notification: Notification
    ) -> DeliveryOutcome:
        transport = self._transports.get(device.transport)
        if transport is None:
            result = DeliveryResult("skipped", error="no hay transporte para este dispositivo")
        else:
            try:
                result = await transport.send(device.target, message)
                if not is_delivery_result(result):
                    result = DeliveryResult("failed", error="el transporte no informó un resultado")
            except Exception as error:
                # Sólo el tipo: el mensaje de un error de red puede traer el destino.
                result = DeliveryResult("failed", error=f"el transporte lanzó: {type(error).__name__}")
        if self._on_result is not None:
            # Anotar el resultado es de quien integra: si falla, el aviso igual salió o no salió.
            with contextlib.suppress(Exception):
                await maybe_await(self._on_result(device, result, notification))
        return result.outcome
