"""Los puertos: lo que campanita necesita de afuera.

El núcleo no conoce la base, el canal ni la superficie que muestra la bandeja.

- `NotificationStore`, `PreferenceStore` y `QuietHoursStore` están
  especificados: su comportamiento es el de las secuencias de
  `spec/vectors/bandeja/`, `spec/vectors/preferencias-guardadas/` y
  `spec/vectors/silencio-guardado/`, y `campanita.conformance` las corre contra
  cualquier adaptador.
- `DeliveryChannel` se usa en el recorrido completo, que está especificado
  (`spec/vectors/recorrido/`). Los canales incluidos son email y Slack
  (`campanita.channels`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ._catalog import Draft
from ._inbox import AddResult, FeedPage, FeedQuery, Notification
from ._quiet import QuietHours
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


class QuietHoursStore(Protocol):
    """El horario de no molestar de cada persona. Es de la persona, no de un ámbito.

    El almacén guarda el horario que le pasen: lo valida `parse_quiet_hours`
    antes. Su comportamiento es el de `spec/vectors/silencio-guardado/`.
    """

    async def get_quiet_hours(self, recipient_id: str) -> QuietHours | None:
        """El horario de esa persona, o `None` si nunca definió uno."""
        ...

    async def set_quiet_hours(self, recipient_id: str, quiet_hours: QuietHours) -> None:
        """Lo guarda, reemplazando el que hubiera."""
        ...


#: - `sent`: el proveedor aceptó el aviso.
#: - `expired`: el destino ya no existe y el adaptador lo tiene que olvidar.
#: - `failed`: no se pudo entregar.
#: - `skipped`: no había a dónde entregar. Reintentar no sirve.
#: - `silenced`: no lo contesta un canal, lo anota `Notifier`: el canal no se
#:   intentó porque la persona está en su horario de no molestar.
DeliveryOutcome = Literal["sent", "expired", "failed", "skipped", "silenced"]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    #: Identificador del mensaje en el proveedor, si lo dio.
    provider_message_id: str | None = None
    error: str | None = None


class DeliveryChannel(Protocol):
    """Cualquier canal de entrega fuera de la bandeja.

    Cada adaptador resuelve sus propios destinos a partir de `recipient_id`: a
    qué dirección o a qué dispositivos va un aviso es asunto suyo.

    `send` no debería lanzar: informa uno de los resultados de
    `DeliveryOutcome`. Si igual lanza, `Notifier` lo anota como `failed` y sigue
    con los demás canales.

    El recorrido está especificado (`spec/vectors/recorrido/`). Los canales
    incluidos están en `campanita.channels`.
    """

    @property
    def id(self) -> str:
        """El identificador del canal en la política."""
        ...

    async def send(self, notification: Notification) -> DeliveryResult: ...
