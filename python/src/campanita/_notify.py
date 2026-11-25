"""El recorrido completo: armar el aviso, guardarlo y entregarlo."""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ._catalog import Catalog, build_notification
from ._channels import resolve_channels
from ._quiet import is_quiet, local_minute
from ._types import INBOX, EventRejected
from .ports import DeliveryChannel, DeliveryResult, NotificationStore, PreferenceStore, QuietHoursStore

if TYPE_CHECKING:
    from ._inbox import AddResult, Notification
    from ._policy import Policy

_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_OUTCOMES = frozenset({"sent", "expired", "failed", "skipped"})


class InvalidChannels(ValueError):
    """Los canales configurados no cumplen el spec. Se detecta al armar el `Notifier`."""


@dataclass(frozen=True, slots=True)
class NotifyResult:
    notification: Notification
    #: `False` cuando la clave ya existía: no se guardó nada nuevo ni se entregó.
    created: bool
    #: Las copias de un aviso de grupo, una por miembro y en su orden. `created`
    #: es `False` cuando el miembro ya tenía un aviso con esa clave.
    copies: Sequence[AddResult]
    #: Qué pasó en cada canal por el que el aviso iba a salir, en orden. Incluye
    #: los que no se intentaron por no molestar (`silenced`).
    deliveries: Mapping[str, DeliveryResult]


class Notifier:
    """Junta la política, el catálogo, los almacenes y los canales de una instalación.

    Es lo único que hace falta para avisar: `await notifier.notify(evento)`.
    """

    def __init__(  # noqa: PLR0913 — dependencias inyectadas, solo por nombre
        self,
        *,
        policy: Policy,
        catalog: Catalog,
        store: NotificationStore,
        preferences: PreferenceStore,
        channels: Iterable[DeliveryChannel] = (),
        available: Callable[[str, str | None], Iterable[str] | Awaitable[Iterable[str]]] | None = None,
        quiet_hours: QuietHoursStore | None = None,
        local_minute: Callable[[str, datetime], int | None] = local_minute,
        new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._policy = policy
        self._catalog = catalog
        self._store = store
        self._preferences = preferences
        self._channels = check_channels(channels)
        #: Qué canales tiene cada destinatario (y ámbito). Sin función, todos los
        #: configurados. Un canal que devuelve pero no está configurado no cuenta.
        self._available = available
        #: Sin almacén de horarios, nadie está en no molestar.
        self._quiet_hours = quiet_hours
        #: De una zona horaria y un instante al minuto local. Se puede reemplazar
        #: si la base de zonas de la plataforma no alcanza.
        self._local_minute = local_minute
        self._new_id = new_id
        self._clock = clock

    async def notify(
        self,
        event: Mapping[str, object],
        *,
        id: str | None = None,
        created_at: datetime | None = None,
        members: Sequence[Mapping[str, object]] | None = None,
    ) -> NotifyResult:
        """Arma el aviso del evento, lo guarda y lo entrega.

        `members` sólo vale para un tipo de audiencia `group`: a cada uno,
        `{"recipient_id": ..., "id": ...}`, le queda una copia en su bandeja con
        ese `id`. Las copias no se entregan: el aviso sale una vez, para el grupo.

        Lanza `EventRejected` si el evento o los miembros no sirven (y entonces
        no se guardó nada) y `StoreError` si el almacén rechaza el aviso o una
        copia. Una entrega que falla nunca lanza: queda anotada en `deliveries`.
        """
        draft = build_notification(self._catalog, event)
        copies_to = _check_members(members, group=self._catalog[draft.type].audience == "group")
        at = self._clock() if created_at is None else created_at
        added = await self._store.add(draft, id=self._new_id() if id is None else id, created_at=at)
        if not added.created:
            # El aviso ya existía: no se copia ni se vuelve a entregar.
            # Reintentar una entrega fallida es decisión de quien llama (`deliver`).
            return NotifyResult(added.notification, created=False, copies=(), deliveries={})
        copies = [
            await self._store.add(replace(draft, recipient_id=recipient_id), id=copy_id, created_at=at)
            for recipient_id, copy_id in copies_to
        ]

        deliveries = await self._deliver(added.notification, added.notification.created_at)
        return NotifyResult(added.notification, created=True, copies=copies, deliveries=deliveries)

    async def deliver(self, notification: Notification) -> Mapping[str, DeliveryResult]:
        """Entrega un aviso que ya está guardado, sin guardar nada.

        Sirve para avisos que guardó otro (un trigger de la base, otro servicio)
        y para reintentar una entrega que falló. No mira la `dedupe_key` ni hace
        copias, y usa las preferencias, la disponibilidad y el horario de no
        molestar de ahora.

        Lanza `EventRejected` (`invalid_field`, `category`) si la política no
        tiene la categoría del aviso. Una entrega que falla nunca lanza.
        """
        if notification.category not in self._policy:
            raise EventRejected("invalid_field", "category")
        return await self._deliver(notification, self._clock())

    async def _deliver(self, notification: Notification, moment: datetime) -> dict[str, DeliveryResult]:
        """Los pasos 6 a 9 del recorrido. `moment` es el instante para no molestar."""
        overrides = await self._preferences.get_overrides(notification.recipient_id, scope=notification.scope)
        targets = resolve_channels(
            self._policy, notification.category, overrides, await self._usable(notification)
        )
        silent = bool(targets) and await self._is_quiet(notification.recipient_id, moment)
        cells = self._policy[notification.category]
        deliveries: dict[str, DeliveryResult] = {}
        # `sorted` sobre `str` ordena por punto de código: el orden del spec.
        for channel_id in sorted(targets):
            if silent and not cells[channel_id].urgent:
                deliveries[channel_id] = DeliveryResult("silenced")
                continue
            deliveries[channel_id] = await _deliver(self._channels[channel_id], notification)
        return deliveries

    async def _usable(self, notification: Notification) -> list[str]:
        """Los canales configurados que están disponibles para el destinatario del aviso."""
        if self._available is None:
            return list(self._channels)
        value = self._available(notification.recipient_id, notification.scope)
        theirs = set(await value if inspect.isawaitable(value) else value)
        return [channel_id for channel_id in self._channels if channel_id in theirs]

    async def _is_quiet(self, recipient_id: str, moment: datetime) -> bool:
        """Si el destinatario está en su horario de no molestar en ese instante."""
        if self._quiet_hours is None:
            return False
        quiet_hours = await self._quiet_hours.get_quiet_hours(recipient_id)
        if quiet_hours is None or not quiet_hours.enabled:
            return False
        minute = self._local_minute(quiet_hours.timezone, moment)
        # Zona desconocida: ante la duda, se entrega.
        return minute is not None and is_quiet(quiet_hours, minute)


def _check_members(members: Sequence[Mapping[str, object]] | None, *, group: bool) -> list[tuple[str, str]]:
    """Los miembros como `(recipient_id, id)`, o `EventRejected` con `members` de detalle."""
    if members is None:
        return []
    if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
        raise EventRejected("invalid_field", "members")
    if members and not group:
        raise EventRejected("invalid_field", "members")
    out: list[tuple[str, str]] = []
    for member in members:
        if not isinstance(member, Mapping) or set(member.keys()) != {"recipient_id", "id"}:
            raise EventRejected("invalid_field", "members")
        recipient_id, copy_id = member["recipient_id"], member["id"]
        if (
            not isinstance(recipient_id, str)
            or not recipient_id
            or not isinstance(copy_id, str)
            or not copy_id
        ):
            raise EventRejected("invalid_field", "members")
        out.append((recipient_id, copy_id))
    if len({r for r, _ in out}) != len(out) or len({i for _, i in out}) != len(out):
        raise EventRejected("invalid_field", "members")
    return out


def check_channels(channels: Iterable[DeliveryChannel]) -> dict[str, DeliveryChannel]:
    """Los canales por identificador, o `InvalidChannels`."""
    by_id: dict[str, DeliveryChannel] = {}
    for channel in channels:
        channel_id = channel.id
        if not isinstance(channel_id, str) or not _ID.fullmatch(channel_id):
            raise InvalidChannels(f"canal {channel_id!r}: no es un identificador")
        if channel_id == INBOX:
            raise InvalidChannels(f"`{INBOX}` es la bandeja, no un canal de entrega")
        if channel_id in by_id:
            raise InvalidChannels(f"canal `{channel_id}` repetido")
        by_id[channel_id] = channel
    return by_id


async def _deliver(channel: DeliveryChannel, notification: Notification) -> DeliveryResult:
    """Un canal no debería lanzar, pero si lanza no puede cortar a los demás."""
    try:
        result = await channel.send(notification)
    # Un canal de terceros puede fallar de cualquier forma: el error queda como `failed`.
    except Exception as error:  # noqa: BLE001
        return DeliveryResult("failed", error=f"{type(error).__name__}: {error}")
    if not isinstance(result, DeliveryResult) or result.outcome not in _OUTCOMES:
        return DeliveryResult("failed", error="el canal no devolvió un resultado válido")
    return result
