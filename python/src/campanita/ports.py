"""Los puertos: lo que campanita necesita de afuera.

El núcleo no conoce la base, el canal ni la superficie que muestra la bandeja.

- `NotificationStore` y `PreferenceStore` están especificados: su
  comportamiento es el de las secuencias de `spec/vectors/bandeja/` y
  `spec/vectors/preferencias-guardadas/`, y `campanita.conformance` las corre
  contra cualquier adaptador.
- `DeliveryChannel` es un BORRADOR: el spec todavía no lo cubre y puede cambiar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ._catalog import Draft
from ._inbox import AddResult, FeedPage, FeedQuery, Notification
from ._types import Change, Overrides


class NotificationStore(Protocol):
    """Cualquier base. Todas las lecturas piden `recipient_id`: es el único
    alcance de seguridad, y por eso es un parámetro obligatorio."""

    async def add(self, draft: Draft, *, id: str, created_at: datetime) -> AddResult:
        """Guarda el aviso, salvo que su clave ya exista para esa persona.

        Revisar la clave y guardar tiene que ser atómico: dos pedidos a la vez
        con la misma clave dejan un solo aviso. Lanza `StoreError("duplicate_id")`.
        """
        ...

    async def list_feed(self, query: FeedQuery) -> FeedPage:
        """La bandeja, del más nuevo al más viejo. Lanza `StoreError("invalid_limit")`."""
        ...

    async def count_unread(
        self, recipient_id: str, *, scope: str | None = None, cap: int | None = None
    ) -> int:
        """Cuántos sin leer. Con `cap`, deja de contar al llegar a ese número."""
        ...

    async def mark_read(self, id: str, recipient_id: str, read_at: datetime) -> bool:
        """`True` sólo si hubo transición. Marcar dos veces no mueve la hora."""
        ...

    async def mark_all_read(
        self,
        recipient_id: str,
        *,
        before: datetime,
        read_at: datetime,
        scope: str | None = None,
        category: str | None = None,
    ) -> int:
        """Marca lo creado antes de `before`, para no tocar lo que llegó después
        de lo que la persona vio. Devuelve cuántos cambió."""
        ...


class PreferenceStore(Protocol):
    """Lo que cambió cada persona. Guarda SÓLO los cambios: sin nada guardado,
    valen los valores de la política.

    El almacén no conoce la política. Qué se puede cambiar lo decide
    `validate_patch` antes de guardar.
    """

    async def get_overrides(self, recipient_id: str, *, scope: str | None = None) -> Overrides:
        """Lo guardado para esa persona en ese ámbito. Los ámbitos no se heredan."""
        ...

    async def save_changes(
        self, recipient_id: str, changes: Sequence[Change], *, scope: str | None = None
    ) -> None:
        """Guarda los cambios, todos o ninguno. Si una celda se repite, vale la última."""
        ...


#: `expired` = el destino ya no existe (una suscripción vencida) y hay que olvidarlo.
DeliveryOutcome = Literal["sent", "expired", "failed"]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    #: Identificador del mensaje en el proveedor, si lo dio.
    provider_message_id: str | None = None
    error: str | None = None


class DeliveryChannel(Protocol):
    """BORRADOR. Cualquier canal de entrega.

    Cada adaptador resuelve sus propios destinos a partir de `recipient_id`.
    `send` NUNCA lanza: un destino roto no puede impedir avisarle al resto.
    """

    @property
    def id(self) -> str:
        """El identificador del canal en la política."""
        ...

    async def send(self, notification: Notification) -> DeliveryResult: ...
