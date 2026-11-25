"""Un almacén sobre SQLite, con el módulo `sqlite3` de la biblioteca estándar.

Las consultas corren en el mismo hilo y bloquean mientras duran. Alcanza para
aplicaciones chicas, herramientas y tests; para una base con mucha concurrencia
conviene un adaptador sobre un driver asíncrono, que tiene que pasar las mismas
secuencias (`campanita.conformance`).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .._inbox import (
    AddResult,
    FeedPage,
    FeedQuery,
    Notification,
    StoreError,
    check_cap,
    check_limit,
    from_ms,
    to_ms,
)
from .._quiet import QuietHours

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import datetime

    from .._catalog import Draft
    from .._types import Change, Overrides

# El texto se compara byte a byte (la colación BINARY de SQLite), que en UTF-8
# coincide con el orden por punto de código que pide el spec.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    id            TEXT PRIMARY KEY,
    recipient_id  TEXT NOT NULL,
    type          TEXT NOT NULL,
    category      TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT,
    action_url    TEXT,
    payload       TEXT NOT NULL,
    scope         TEXT,
    dedupe_key    TEXT,
    created_at_ms INTEGER NOT NULL,
    read_at_ms    INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS {table}_dedupe
    ON {table} (recipient_id, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS {table}_feed
    ON {table} (recipient_id, created_at_ms DESC, id DESC);
"""
_COLUMNS = (
    "id, recipient_id, type, category, title, body, action_url, payload, scope, dedupe_key,"
    " created_at_ms, read_at_ms"
)


@contextmanager
def _atomic(db: sqlite3.Connection) -> Iterator[None]:
    """Todo o nada, sin tocar la transacción de quien llama.

    Si la conexión ya está en una transacción, se usa un `SAVEPOINT`: lo hecho acá
    se confirma o se deshace con la de quien llama, y un error sólo deshace lo
    nuestro. Si no, `BEGIN IMMEDIATE` toma el bloqueo de escritura antes de leer.
    """
    if db.in_transaction:
        db.execute("SAVEPOINT campanita")
        try:
            yield
        except BaseException:
            db.execute("ROLLBACK TO campanita")
            db.execute("RELEASE campanita")
            raise
        db.execute("RELEASE campanita")
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


class SqliteStore:
    """Usa la conexión que le pasan y no la cierra."""

    def __init__(self, connection: sqlite3.Connection, *, table: str = "campanita_notifications") -> None:
        if not table.isidentifier():
            raise ValueError("el nombre de la tabla tiene que ser un identificador")
        self._db = connection
        self._table = table

    def create_schema(self) -> None:
        """Crea la tabla y los índices si no existen."""
        self._db.executescript(_SCHEMA.format(table=self._table))

    async def add(self, draft: Draft, *, id: str, created_at: datetime) -> AddResult:
        # Revisar la clave y guardar es una sola operación.
        with _atomic(self._db):
            if draft.dedupe_key is not None:
                existing = self._db.execute(
                    f"SELECT {_COLUMNS} FROM {self._table} WHERE recipient_id = ? AND dedupe_key = ?",
                    (draft.recipient_id, draft.dedupe_key),
                ).fetchone()
                if existing is not None:
                    return AddResult(_row(existing), created=False)
            item = Notification.from_draft(draft, id=id, created_at=created_at)
            try:
                self._db.execute(
                    f"INSERT INTO {self._table} ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
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
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StoreError("duplicate_id") from error
        return AddResult(item, created=True)

    async def list_feed(self, query: FeedQuery) -> FeedPage:
        limit = check_limit(query.limit)
        where, params = self._visible(query.recipient_id, query.scope)
        if query.unread_only:
            where += " AND read_at_ms IS NULL"
        if query.category is not None:
            where += " AND category = ?"
            params.append(query.category)
        if query.cursor is not None:
            where += " AND (created_at_ms, id) < (?, ?)"
            params += [to_ms(query.cursor[0]), query.cursor[1]]
        # Uno de más, para saber si queda otra página sin contar.
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM {self._table} WHERE {where}"
            " ORDER BY created_at_ms DESC, id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        page = tuple(_row(row) for row in rows[:limit])
        more = len(rows) > limit
        return FeedPage(page, (page[-1].created_at, page[-1].id) if page and more else None)

    async def count_unread(
        self, recipient_id: str, *, scope: str | None = None, cap: int | None = None
    ) -> int:
        cap = check_cap(cap)
        where, params = self._visible(recipient_id, scope)
        inner = f"SELECT 1 FROM {self._table} WHERE {where} AND read_at_ms IS NULL"
        if cap is not None:
            inner += " LIMIT ?"
            params.append(cap)
        (total,) = self._db.execute(f"SELECT COUNT(*) FROM ({inner})", params).fetchone()
        return int(total)

    async def mark_read(self, id: str, recipient_id: str, read_at: datetime) -> bool:
        with _atomic(self._db):
            cursor = self._db.execute(
                f"UPDATE {self._table} SET read_at_ms = ?"
                " WHERE id = ? AND recipient_id = ? AND read_at_ms IS NULL",
                (to_ms(read_at), id, recipient_id),
            )
        return cursor.rowcount == 1

    async def mark_all_read(
        self,
        recipient_id: str,
        *,
        before: datetime,
        read_at: datetime,
        scope: str | None = None,
        category: str | None = None,
    ) -> int:
        where, params = self._visible(recipient_id, scope)
        where += " AND read_at_ms IS NULL AND created_at_ms < ?"
        params.append(to_ms(before))
        if category is not None:
            where += " AND category = ?"
            params.append(category)
        with _atomic(self._db):
            cursor = self._db.execute(
                f"UPDATE {self._table} SET read_at_ms = ? WHERE {where}", (to_ms(read_at), *params)
            )
        return cursor.rowcount

    @staticmethod
    def _visible(recipient_id: str, scope: str | None) -> tuple[str, list[object]]:
        if scope is None:
            return "recipient_id = ?", [recipient_id]
        return "recipient_id = ? AND (scope IS NULL OR scope = ?)", [recipient_id, scope]


def _row(row: tuple[object, ...]) -> Notification:
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


# "Sin ámbito" y "ámbito de texto vacío" son cosas distintas, y en SQL un NULL
# no entra en una clave primaria. `has_scope` los separa sin inventar un valor
# reservado.
_PREFERENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    recipient_id TEXT NOT NULL,
    has_scope    INTEGER NOT NULL,
    scope        TEXT NOT NULL,
    category     TEXT NOT NULL,
    channel      TEXT NOT NULL,
    enabled      INTEGER NOT NULL,
    PRIMARY KEY (recipient_id, has_scope, scope, category, channel)
) WITHOUT ROWID;
"""


class SqlitePreferenceStore:
    """Las preferencias guardadas, sobre SQLite. Usa la conexión que le pasan y no la cierra."""

    def __init__(self, connection: sqlite3.Connection, *, table: str = "campanita_preferences") -> None:
        if not table.isidentifier():
            raise ValueError("el nombre de la tabla tiene que ser un identificador")
        self._db = connection
        self._table = table

    def create_schema(self) -> None:
        """Crea la tabla si no existe."""
        self._db.executescript(_PREFERENCES_SCHEMA.format(table=self._table))

    async def get_overrides(self, recipient_id: str, *, scope: str | None = None) -> Overrides:
        rows = self._db.execute(
            f"SELECT category, channel, enabled FROM {self._table}"
            " WHERE recipient_id = ? AND has_scope = ? AND scope = ?",
            (recipient_id, *_scope_key(scope)),
        ).fetchall()
        out: dict[str, dict[str, bool]] = {}
        for category, channel, enabled in rows:
            out.setdefault(str(category), {})[str(channel)] = bool(enabled)
        return out

    async def save_changes(
        self, recipient_id: str, changes: Sequence[Change], *, scope: str | None = None
    ) -> None:
        if not changes:
            return
        key = _scope_key(scope)
        # Se guardan todos los cambios o ninguno.
        with _atomic(self._db):
            self._db.executemany(
                f"INSERT INTO {self._table} (recipient_id, has_scope, scope, category, channel, enabled)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (recipient_id, has_scope, scope, category, channel)"
                " DO UPDATE SET enabled = excluded.enabled",
                [
                    (recipient_id, *key, change.category, change.channel, int(change.enabled))
                    for change in changes
                ],
            )


def _scope_key(scope: str | None) -> tuple[int, str]:
    return (0, "") if scope is None else (1, scope)


_QUIET_HOURS_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    recipient_id TEXT PRIMARY KEY,
    enabled      INTEGER NOT NULL,
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    timezone     TEXT NOT NULL
) WITHOUT ROWID;
"""


class SqliteQuietHoursStore:
    """Los horarios de no molestar, sobre SQLite. Usa la conexión que le pasan y no la cierra."""

    def __init__(self, connection: sqlite3.Connection, *, table: str = "campanita_quiet_hours") -> None:
        if not table.isidentifier():
            raise ValueError("el nombre de la tabla tiene que ser un identificador")
        self._db = connection
        self._table = table

    def create_schema(self) -> None:
        """Crea la tabla si no existe."""
        self._db.executescript(_QUIET_HOURS_SCHEMA.format(table=self._table))

    async def get_quiet_hours(self, recipient_id: str) -> QuietHours | None:
        row = self._db.execute(
            f"SELECT enabled, start_time, end_time, timezone FROM {self._table} WHERE recipient_id = ?",
            (recipient_id,),
        ).fetchone()
        if row is None:
            return None
        enabled, start, end, timezone = row
        return QuietHours(enabled=bool(enabled), start=str(start), end=str(end), timezone=str(timezone))

    async def set_quiet_hours(self, recipient_id: str, quiet_hours: QuietHours) -> None:
        with _atomic(self._db):
            self._db.execute(
                f"INSERT INTO {self._table} (recipient_id, enabled, start_time, end_time, timezone)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (recipient_id) DO UPDATE SET enabled = excluded.enabled,"
                " start_time = excluded.start_time, end_time = excluded.end_time,"
                " timezone = excluded.timezone",
                (
                    recipient_id,
                    int(quiet_hours.enabled),
                    quiet_hours.start,
                    quiet_hours.end,
                    quiet_hours.timezone,
                ),
            )
