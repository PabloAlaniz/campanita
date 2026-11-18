"""Los puertos: lo que campanita necesita de afuera.

BORRADOR. El spec v0 no cubre todavía la ingesta, la bandeja ni las entregas, así
que estos contratos pueden cambiar. Están acá para fijar la forma: el núcleo no
conoce la base, el canal ni la superficie que muestra la bandeja.

Regla del proyecto: un puerto no se da por estable hasta tener dos adaptadores
reales (ver `docs/puertos.md`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from ._types import Change, Overrides


@dataclass(frozen=True, slots=True)
class Notification:
    """Un aviso para UNA persona. `read_at` es lo único que cambia después de creado."""

    id: str
    recipient_id: str
    category: str
    #: El tipo dentro del catálogo de quien integra (`invitations.received`).
    type: str
    title: str
    #: La pone quien guarda, nunca quien emite: la bandeja pagina por
    #: `(created_at, id)` y un reloj corrido insertaría filas en una página ya servida.
    created_at: datetime
    body: str | None = None
    action_url: str | None = None
    #: Las variables con las que se armó el título, para volver a renderizar.
    payload: Mapping[str, object] = field(default_factory=dict)
    #: Ámbito opcional (un tenant, un proyecto). `None` = aviso de cuenta, se ve siempre.
    scope: str | None = None
    #: Reenviar la misma clave no crea otro aviso.
    dedupe_key: str | None = None
    read_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FeedQuery:
    recipient_id: str
    #: Cuántos avisos como máximo. Lo decide quien muestra la bandeja.
    limit: int
    #: `(created_at, id)` del último aviso de la página anterior.
    cursor: tuple[datetime, str] | None = None
    unread_only: bool = False
    category: str | None = None
    #: Filtra `scope == X o scope es None`.
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class FeedPage:
    items: Sequence[Notification]
    next_cursor: tuple[datetime, str] | None


class NotificationStore(Protocol):
    """Cualquier base. Todas las lecturas piden `recipient_id`: es el único
    alcance de seguridad, y por eso es un parámetro obligatorio."""

    async def add_many(self, notifications: Sequence[Notification]) -> Sequence[Notification]:
        """Guarda un lote de forma atómica."""
        ...

    async def find_by_dedupe_key(self, recipient_id: str, dedupe_key: str) -> Notification | None: ...

    async def list_feed(self, query: FeedQuery) -> FeedPage: ...

    async def count_unread(
        self, recipient_id: str, *, scope: str | None = None, cap: int | None = None
    ) -> int:
        """Cuántos sin leer. Con `cap`, deja de contar al llegar a ese número:
        una interfaz que muestra "99+" no necesita el total exacto."""
        ...

    async def mark_read(self, notification_id: str, recipient_id: str, read_at: datetime) -> bool:
        """`True` sólo si hubo transición. Marcar dos veces no mueve la hora."""
        ...

    async def mark_all_read(
        self, recipient_id: str, *, before: datetime, scope: str | None = None, category: str | None = None
    ) -> int:
        """Marca lo anterior a `before`, para no tocar lo que llegó durante el click."""
        ...


class PreferenceStore(Protocol):
    """Guarda SÓLO los cambios del usuario. Sin filas = valores por defecto."""

    async def get_overrides(self, recipient_id: str, scope: str | None = None) -> Overrides: ...

    async def save_changes(
        self, recipient_id: str, changes: Sequence[Change], scope: str | None = None
    ) -> None: ...


#: `expired` = el destino ya no existe (una suscripción vencida) y hay que olvidarlo.
DeliveryOutcome = Literal["sent", "expired", "failed"]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    #: Identificador del mensaje en el proveedor, si lo dio.
    provider_message_id: str | None = None
    error: str | None = None


class DeliveryChannel(Protocol):
    """Cualquier canal de entrega (email, web push, APNs…).

    Cada adaptador resuelve sus propios destinos a partir de `recipient_id`.
    `send` NUNCA lanza: un destino roto no puede impedir avisarle al resto.
    """

    @property
    def id(self) -> str:
        """El identificador del canal en la política (`email`, `push`)."""
        ...

    async def send(self, notification: Notification) -> DeliveryResult: ...
