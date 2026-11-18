"""Un almacén sobre SQLite, con el módulo `sqlite3` de la biblioteca estándar.

Las consultas corren en el mismo hilo y bloquean mientras duran. Alcanza para
aplicaciones chicas, herramientas y tests; para una base con mucha concurrencia
conviene un adaptador sobre un driver asíncrono, que tiene que pasar las mismas
secuencias (`campanita.conformance`).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .._catalog import Draft
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
        # BEGIN IMMEDIATE toma el bloqueo de escritura antes de mirar la clave:
        # revisar y guardar es una sola operación.
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if draft.dedupe_key is not None:
                existing = self._db.execute(
                    f"SELECT {_COLUMNS} FROM {self._table} WHERE recipient_id = ? AND dedupe_key = ?",
                    (draft.recipient_id, draft.dedupe_key),
                ).fetchone()
                if existing is not None:
                    self._db.execute("ROLLBACK")
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
                self._db.execute("ROLLBACK")
                raise StoreError("duplicate_id") from error
            self._db.execute("COMMIT")
        except sqlite3.Error:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
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
        with self._db:
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
        with self._db:
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
