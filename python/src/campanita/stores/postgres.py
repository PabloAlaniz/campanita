"""Almacenes sobre Postgres, con psycopg 3 en modo asíncrono.

Reciben una `psycopg.AsyncConnection` o un `psycopg_pool.AsyncConnectionPool`
ya armados, y no los cierran. campanita no depende de psycopg: lo importa
recién cuando hace una consulta, así que quien usa estos almacenes ya lo tiene.

Con un pool, cada operación toma una conexión y la devuelve. Con una conexión,
cada operación corre en su propia transacción (o en un savepoint, si la
conexión ya está dentro de una), con autocommit o sin él. Una conexión hace una
cosa por vez: las operaciones de estos almacenes sobre la misma conexión se
turnan solas, pero para atender pedidos a la vez conviene un pool.

Las tablas se crean con `create_schema()` o copiando las sentencias de
`postgres_schema()` a una migración.
"""

from __future__ import annotations

import asyncio
import json
import weakref
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeAlias

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
from .._quiet import QuietHours
from .._types import Change, Overrides

if TYPE_CHECKING:
    from psycopg import AsyncConnection, AsyncCursor
    from psycopg_pool import AsyncConnectionPool

    #: Lo que reciben los almacenes: una conexión o un pool de psycopg 3.
    PostgresConnection: TypeAlias = AsyncConnection[Any] | AsyncConnectionPool[Any]

_NOTIFICATIONS = "campanita_notifications"
_PREFERENCES = "campanita_preferences"
_QUIET_HOURS = "campanita_quiet_hours"
_COLUMNS = (
    "id, recipient_id, type, category, title, body, action_url, payload, scope, dedupe_key,"
    " created_at_ms, read_at_ms"
)


#: Postgres corta los nombres en 63 bytes sin avisar. Con el sufijo más largo
#: de los índices (`_dedupe`) el nombre tiene que entrar entero: si no, el
#: índice cortado choca con otro y `IF NOT EXISTS` lo saltea en silencio.
_MAX_TABLE = 63 - len("_dedupe")


def _check_table(table: str) -> str:
    # Sólo letras ASCII, dígitos y guiones bajos: el nombre va pegado al SQL.
    if not (table.isidentifier() and table.isascii() and len(table) <= _MAX_TABLE):
        raise ValueError(
            f"el nombre de la tabla tiene que ser un identificador de hasta {_MAX_TABLE} caracteres"
        )
    return table


# Una conexión no puede tener dos transacciones abiertas a la vez: si dos
# operaciones se pisan, psycopg corta con `OutOfOrderTransactionNesting` y la
# conexión queda adentro de una transacción. Por eso las operaciones sobre una
# misma conexión se turnan, también entre almacenes distintos.
_turns: weakref.WeakKeyDictionary[AsyncConnection[Any], asyncio.Lock] = weakref.WeakKeyDictionary()


def _turn(conn: AsyncConnection[Any]) -> asyncio.Lock:
    lock = _turns.get(conn)
    if lock is None:
        lock = _turns[conn] = asyncio.Lock()
    return lock


def postgres_schema(
    *,
    notifications: str = _NOTIFICATIONS,
    preferences: str = _PREFERENCES,
    quiet_hours: str = _QUIET_HOURS,
) -> list[str]:
    """Las sentencias que crean las tablas y los índices, para copiar a una migración.

    El `id` usa la colación "C", que compara bytes: en UTF-8 es el orden por
    punto de código que pide el spec. Con la colación de la base (en_US y
    parecidas) el orden de la bandeja sale distinto.
    """
    n = _check_table(notifications)
    p = _check_table(preferences)
    q = _check_table(quiet_hours)
    return [
        f"""CREATE TABLE IF NOT EXISTS {n} (
    id            TEXT COLLATE "C" PRIMARY KEY,
    recipient_id  TEXT NOT NULL,
    type          TEXT NOT NULL,
    category      TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT,
    action_url    TEXT,
    payload       TEXT NOT NULL,
    scope         TEXT,
    dedupe_key    TEXT,
    created_at_ms BIGINT NOT NULL,
    read_at_ms    BIGINT
)""",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {n}_dedupe ON {n} (recipient_id, dedupe_key)"
        " WHERE dedupe_key IS NOT NULL",
        f"CREATE INDEX IF NOT EXISTS {n}_feed ON {n} (recipient_id, created_at_ms DESC, id DESC)",
        # "Sin ámbito" y "ámbito de texto vacío" son cosas distintas, y un NULL
        # no entra en una clave primaria: `has_scope` los separa.
        f"""CREATE TABLE IF NOT EXISTS {p} (
    recipient_id TEXT NOT NULL,
    has_scope    BOOLEAN NOT NULL,
    scope        TEXT NOT NULL,
    category     TEXT NOT NULL,
    channel      TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL,
    PRIMARY KEY (recipient_id, has_scope, scope, category, channel)
)""",
        f"""CREATE TABLE IF NOT EXISTS {q} (
    recipient_id TEXT PRIMARY KEY,
    enabled      BOOLEAN NOT NULL,
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    timezone     TEXT NOT NULL
)""",
    ]


class _Base:
    def __init__(self, db: PostgresConnection, table: str) -> None:
        self._db = db
        self._table = _check_table(table)

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[AsyncCursor[tuple[Any, ...]]]:
        """Un cursor que devuelve tuplas, dentro de una transacción."""
        # Se importa acá: quien llega hasta una consulta ya tiene psycopg.
        from psycopg import AsyncConnection, AsyncCursor
        from psycopg.rows import tuple_row

        # El cursor se arma a mano y no con `conn.cursor()`, para no heredar el
        # `row_factory` ni el `cursor_factory` que la conexión tenga configurados.
        if isinstance(self._db, AsyncConnection):
            conn = self._db
            async with (
                _turn(conn),
                conn.transaction(),
                AsyncCursor(conn, row_factory=tuple_row) as cursor,
            ):
                yield cursor
        else:
            async with (
                self._db.connection() as conn,
                conn.transaction(),
                AsyncCursor(conn, row_factory=tuple_row) as cursor,
            ):
                yield cursor

    async def _create(self, statements: Sequence[str]) -> None:
        async with self._cursor() as cursor:
            for sql in statements:
                await cursor.execute(sql)


class PostgresStore(_Base):
    """Los avisos, sobre Postgres."""

    def __init__(self, db: PostgresConnection, *, table: str = _NOTIFICATIONS) -> None:
        super().__init__(db, table)

    async def create_schema(self) -> None:
        """Crea la tabla y los índices si no existen."""
        await self._create(postgres_schema(notifications=self._table)[:3])

    async def add(self, draft: Draft, *, id: str, created_at: datetime) -> AddResult:
        item = Notification.from_draft(draft, id=id, created_at=created_at)
        async with self._cursor() as cursor:
            # Una sola sentencia: si el identificador o la clave ya existen, no
            # inserta. Si otro pedido está guardando la misma clave, Postgres
            # espera a que termine, así que la búsqueda de abajo encuentra su aviso.
            await cursor.execute(
                f"INSERT INTO {self._table} ({_COLUMNS})"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL) ON CONFLICT DO NOTHING",
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
            if cursor.rowcount > 0:
                return AddResult(item, created=True)
            # No insertó. La clave repetida se revisa antes que el identificador.
            if draft.dedupe_key is not None:
                await cursor.execute(
                    f"SELECT {_COLUMNS} FROM {self._table} WHERE recipient_id = %s AND dedupe_key = %s",
                    (draft.recipient_id, draft.dedupe_key),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    return AddResult(_row(existing), created=False)
        raise StoreError("duplicate_id")

    async def list_feed(self, query: FeedQuery) -> FeedPage:
        limit = check_limit(query.limit)
        where, params = _visible(query.recipient_id, query.scope)
        if query.unread_only:
            where += " AND read_at_ms IS NULL"
        if query.category is not None:
            where += " AND category = %s"
            params.append(query.category)
        if query.cursor is not None:
            where += " AND (created_at_ms, id) < (%s, %s)"
            params += [to_ms(query.cursor[0]), query.cursor[1]]
        async with self._cursor() as cursor:
            # Uno de más, para saber si queda otra página sin contar.
            await cursor.execute(
                f"SELECT {_COLUMNS} FROM {self._table} WHERE {where}"
                " ORDER BY created_at_ms DESC, id DESC LIMIT %s",
                (*params, limit + 1),
            )
            rows = await cursor.fetchall()
        page = tuple(_row(row) for row in rows[:limit])
        more = len(rows) > limit
        return FeedPage(page, (page[-1].created_at, page[-1].id) if page and more else None)

    async def count_unread(
        self, recipient_id: str, *, scope: str | None = None, cap: int | None = None
    ) -> int:
        cap = check_cap(cap)
        where, params = _visible(recipient_id, scope)
        inner = f"SELECT 1 FROM {self._table} WHERE {where} AND read_at_ms IS NULL"
        if cap is not None:
            inner += " LIMIT %s"
            params.append(cap)
        async with self._cursor() as cursor:
            await cursor.execute(f"SELECT COUNT(*) FROM ({inner}) AS unread", params)
            found = await cursor.fetchone()
        return 0 if found is None else int(found[0])

    async def mark_read(self, id: str, recipient_id: str, read_at: datetime) -> bool:
        async with self._cursor() as cursor:
            await cursor.execute(
                f"UPDATE {self._table} SET read_at_ms = %s"
                " WHERE id = %s AND recipient_id = %s AND read_at_ms IS NULL",
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
        where, params = _visible(recipient_id, scope)
        where += " AND read_at_ms IS NULL AND created_at_ms < %s"
        params.append(to_ms(before))
        if category is not None:
            where += " AND category = %s"
            params.append(category)
        async with self._cursor() as cursor:
            await cursor.execute(
                f"UPDATE {self._table} SET read_at_ms = %s WHERE {where}",
                (to_ms(read_at), *params),
            )
            return cursor.rowcount


def _visible(recipient_id: str, scope: str | None) -> tuple[str, list[object]]:
    if scope is None:
        return "recipient_id = %s", [recipient_id]
    return "recipient_id = %s AND (scope IS NULL OR scope = %s)", [recipient_id, scope]


def _row(row: tuple[Any, ...]) -> Notification:
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
        id=id,
        recipient_id=recipient_id,
        type=type_,
        category=category,
        title=title,
        body=body,
        action_url=action_url,
        payload=json.loads(payload),
        scope=scope,
        dedupe_key=dedupe_key,
        created_at=from_ms(int(created)),
        read_at=None if read is None else from_ms(int(read)),
    )


class PostgresPreferenceStore(_Base):
    """Las preferencias guardadas, sobre Postgres."""

    def __init__(self, db: PostgresConnection, *, table: str = _PREFERENCES) -> None:
        super().__init__(db, table)

    async def create_schema(self) -> None:
        """Crea la tabla si no existe."""
        await self._create(postgres_schema(preferences=self._table)[3:4])

    async def get_overrides(self, recipient_id: str, *, scope: str | None = None) -> Overrides:
        async with self._cursor() as cursor:
            await cursor.execute(
                f"SELECT category, channel, enabled FROM {self._table}"
                " WHERE recipient_id = %s AND has_scope = %s AND scope = %s",
                (recipient_id, scope is not None, scope or ""),
            )
            rows = await cursor.fetchall()
        out: dict[str, dict[str, bool]] = {}
        for category, channel, enabled in rows:
            out.setdefault(category, {})[channel] = bool(enabled)
        return out

    async def save_changes(
        self, recipient_id: str, changes: Sequence[Change], *, scope: str | None = None
    ) -> None:
        if not changes:
            return
        # Si una celda se repite vale la última. Postgres no deja que una misma
        # sentencia actualice dos veces la misma fila, así que se resuelve acá.
        cells = {(change.category, change.channel): change for change in changes}
        last = list(cells.values())
        async with self._cursor() as cursor:
            # Una sola sentencia: se guardan todos los cambios o ninguno. Las filas
            # van en un orden fijo para que dos pedidos que tocan las mismas celdas
            # las bloqueen en el mismo orden y no se traben entre sí.
            await cursor.execute(
                f"INSERT INTO {self._table}"
                " (recipient_id, has_scope, scope, category, channel, enabled)"
                " SELECT %s, %s, %s, category, channel, enabled"
                " FROM unnest(%s::text[], %s::text[], %s::boolean[]) AS cell (category, channel, enabled)"
                " ORDER BY category, channel"
                " ON CONFLICT (recipient_id, has_scope, scope, category, channel)"
                " DO UPDATE SET enabled = excluded.enabled",
                (
                    recipient_id,
                    scope is not None,
                    scope or "",
                    [change.category for change in last],
                    [change.channel for change in last],
                    [change.enabled for change in last],
                ),
            )


class PostgresQuietHoursStore(_Base):
    """Los horarios de no molestar, sobre Postgres."""

    def __init__(self, db: PostgresConnection, *, table: str = _QUIET_HOURS) -> None:
        super().__init__(db, table)

    async def create_schema(self) -> None:
        """Crea la tabla si no existe."""
        await self._create(postgres_schema(quiet_hours=self._table)[4:5])

    async def get_quiet_hours(self, recipient_id: str) -> QuietHours | None:
        async with self._cursor() as cursor:
            await cursor.execute(
                f"SELECT enabled, start_time, end_time, timezone FROM {self._table} WHERE recipient_id = %s",
                (recipient_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        enabled, start, end, timezone = row
        return QuietHours(enabled=bool(enabled), start=start, end=end, timezone=timezone)

    async def set_quiet_hours(self, recipient_id: str, quiet_hours: QuietHours) -> None:
        async with self._cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {self._table}"
                " (recipient_id, enabled, start_time, end_time, timezone) VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (recipient_id) DO UPDATE SET enabled = excluded.enabled,"
                " start_time = excluded.start_time, end_time = excluded.end_time,"
                " timezone = excluded.timezone",
                (
                    recipient_id,
                    quiet_hours.enabled,
                    quiet_hours.start,
                    quiet_hours.end,
                    quiet_hours.timezone,
                ),
            )
