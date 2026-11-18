"""La bandeja: el aviso guardado y lo que se le puede pedir a un almacén."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from ._catalog import Draft

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


def to_ms(moment: datetime) -> int:
    """Milisegundos desde 1970, sin pasar por decimales. Las fechas del spec
    tienen esa resolución: lo que sobra se descarta."""
    if moment.tzinfo is None:
        raise ValueError("la fecha tiene que tener zona horaria")
    return (moment - _EPOCH) // _MS


def from_ms(ms: int) -> datetime:
    return _EPOCH + ms * _MS


@dataclass(frozen=True, slots=True)
class Notification:
    """Un aviso guardado. `read_at` es lo único que cambia después de creado."""

    id: str
    recipient_id: str
    type: str
    category: str
    title: str
    body: str | None
    action_url: str | None
    payload: Mapping[str, object]
    scope: str | None
    dedupe_key: str | None
    created_at: datetime
    read_at: datetime | None = None

    @classmethod
    def from_draft(cls, draft: Draft, *, id: str, created_at: datetime) -> Notification:
        return cls(
            id=id,
            recipient_id=draft.recipient_id,
            type=draft.type,
            category=draft.category,
            title=draft.title,
            body=draft.body,
            action_url=draft.action_url,
            payload=draft.payload,
            scope=draft.scope,
            dedupe_key=draft.dedupe_key,
            created_at=from_ms(to_ms(created_at)),
        )


@dataclass(frozen=True, slots=True)
class AddResult:
    notification: Notification
    #: `False` cuando la clave ya existía: `notification` es el aviso que había.
    created: bool


#: La posición del último aviso de una página: `(created_at, id)`.
Cursor = tuple[datetime, str]


@dataclass(frozen=True, slots=True)
class FeedQuery:
    recipient_id: str
    #: Cuántos avisos como máximo. Lo decide quien muestra la bandeja.
    limit: int
    cursor: Cursor | None = None
    unread_only: bool = False
    category: str | None = None
    #: Con ámbito: lo de ese ámbito y lo que no tiene ámbito. Sin ámbito: todo.
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class FeedPage:
    items: tuple[Notification, ...]
    #: `None` cuando no queda nada más.
    next_cursor: Cursor | None


#: Por qué un almacén rechaza una operación. Son los literales del spec.
StoreReason = Literal["duplicate_id", "invalid_limit", "invalid_cap"]

_MOTIVOS: dict[StoreReason, str] = {
    "duplicate_id": "ya hay un aviso con ese identificador",
    "invalid_limit": "el límite tiene que ser un entero mayor que cero",
    "invalid_cap": "el tope tiene que ser un entero mayor que cero",
}


class StoreError(ValueError):
    """El almacén rechazó la operación. No cambió nada."""

    def __init__(self, reason: StoreReason) -> None:
        super().__init__(_MOTIVOS[reason])
        self.reason: StoreReason = reason


def check_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise StoreError("invalid_limit")
    return limit


def check_cap(cap: object) -> int | None:
    if cap is None:
        return None
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise StoreError("invalid_cap")
    return cap
