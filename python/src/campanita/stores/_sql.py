"""Lo que comparten los almacenes SQL: columnas, filas y filtros de la bandeja.

Cada base pone su marcador de parámetro (`?` en SQLite, `%s` en psycopg) y su
forma de ejecutar; el resto del SQL es el mismo.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .._inbox import FeedQuery, Notification, from_ms, to_ms

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

COLUMNS = (
    "id, recipient_id, type, category, title, body, action_url, payload, scope, dedupe_key,"
    " created_at_ms, read_at_ms"
)


def insert_values(item: Notification) -> tuple[object, ...]:
    """Los valores de `COLUMNS` para un aviso nuevo, sin `read_at_ms`."""
    return (
        item.id,
        item.recipient_id,
        item.type,
        item.category,
        item.title,
        item.body,
        item.action_url,
        json.dumps(dict(item.payload), ensure_ascii=False),
        item.scope,
        item.dedupe_key,
        to_ms(item.created_at),
    )


def from_row(row: Sequence[object]) -> Notification:
    """Un aviso a partir de una fila con las columnas de `COLUMNS`."""
    (
        id,
        recipient_id,
        type_,
        category,
        title,
        body,
        action_url,
        payload,
        scope,
        dedupe_key,
        created,
        read,
    ) = row
    return Notification(
        id=str(id),
        recipient_id=str(recipient_id),
        type=str(type_),
        category=str(category),
        title=str(title),
        body=None if body is None else str(body),
        action_url=None if action_url is None else str(action_url),
        payload=json.loads(str(payload)),
        scope=None if scope is None else str(scope),
        dedupe_key=None if dedupe_key is None else str(dedupe_key),
        created_at=from_ms(int(str(created))),
        read_at=None if read is None else from_ms(int(str(read))),
    )


def visible(recipient_id: str, scope: str | None, ph: str) -> tuple[str, list[object]]:
    """Lo que ve una persona: sin ámbito, todo; con ámbito, lo de ese ámbito y lo que no tiene."""
    if scope is None:
        return f"recipient_id = {ph}", [recipient_id]
    return f"recipient_id = {ph} AND (scope IS NULL OR scope = {ph})", [recipient_id, scope]


def feed_filter(query: FeedQuery, ph: str) -> tuple[str, list[object]]:
    """El `WHERE` de una página de la bandeja, sin el orden ni el límite."""
    where, params = visible(query.recipient_id, query.scope, ph)
    if query.unread_only:
        where += " AND read_at_ms IS NULL"
    if query.category is not None:
        where += f" AND category = {ph}"
        params.append(query.category)
    if query.cursor is not None:
        where += f" AND (created_at_ms, id) < ({ph}, {ph})"
        params += [to_ms(query.cursor[0]), query.cursor[1]]
    return where, params


def read_all_filter(
    recipient_id: str, *, before: datetime, scope: str | None, category: str | None, ph: str
) -> tuple[str, list[object]]:
    """El `WHERE` de `mark_all_read`: lo visible, sin leer y anterior a `before`."""
    where, params = visible(recipient_id, scope, ph)
    where += f" AND read_at_ms IS NULL AND created_at_ms < {ph}"
    params.append(to_ms(before))
    if category is not None:
        where += f" AND category = {ph}"
        params.append(category)
    return where, params
