"""Los almacenes de Postgres contra un Postgres de verdad, con las mismas
secuencias del spec que corren los demás almacenes.

Necesitan `CAMPANITA_PG_URL`; sin esa variable se saltean.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, AsyncNullConnectionPool
from test_http import Stores, run_http_vector

from campanita import Change, Draft, FeedQuery, StoreError
from campanita.conformance import run_preference_vector, run_quiet_hours_vector, run_store_vector
from campanita.stores import (
    PostgresPreferenceStore,
    PostgresQuietHoursStore,
    PostgresStore,
    postgres_schema,
)

URL = os.environ.get("CAMPANITA_PG_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="sin CAMPANITA_PG_URL")

SPEC = Path(__file__).resolve().parents[2] / "spec" / "vectors"
T0 = datetime(2030, 1, 1, tzinfo=UTC)
#: Un esquema propio por corrida, para no pisar nada de la base.
SCHEMA = f"campanita_test_{os.getpid()}_{time.time_ns()}"
_tables = itertools.count(1)


def vectors(folder: str) -> list[Path]:
    return sorted((SPEC / folder).glob("*.json"))


def table() -> str:
    """Una tabla nueva por test: cada secuencia necesita un almacén vacío."""
    return f"t_{next(_tables)}"


@pytest.fixture(scope="module", autouse=True)
def schema() -> Iterator[None]:
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {SCHEMA}")
    yield
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


# Cada forma de conexión que los almacenes aceptan. Todas abren y cierran
# dentro del mismo loop de asyncio que corre el test.
Opener = Callable[[], AbstractAsyncContextManager[Any]]


@asynccontextmanager
async def autocommit() -> AsyncIterator[Any]:
    conn = await psycopg.AsyncConnection.connect(URL, autocommit=True, options=f"-c search_path={SCHEMA}")
    async with conn:
        yield conn


@asynccontextmanager
async def transactional() -> AsyncIterator[Any]:
    # La conexión por omisión de psycopg, que abre transacciones sola.
    conn = await psycopg.AsyncConnection.connect(URL, options=f"-c search_path={SCHEMA}")
    async with conn:
        yield conn


@asynccontextmanager
async def pool() -> AsyncIterator[Any]:
    async with AsyncConnectionPool(
        URL,
        kwargs={"options": f"-c search_path={SCHEMA}"},
        min_size=1,
        max_size=5,
        open=False,
    ) as made:
        yield made


@asynccontextmanager
async def null_pool() -> AsyncIterator[Any]:
    # Un pool que abre una conexión nueva en cada pedido.
    async with AsyncNullConnectionPool(
        URL, kwargs={"options": f"-c search_path={SCHEMA}"}, max_size=5, open=False
    ) as made:
        yield made


@asynccontextmanager
async def configured() -> AsyncIterator[Any]:
    # Una conexión que devuelve diccionarios y usa cursores sin `%s`: los
    # almacenes no tienen que heredar nada de eso.
    conn = await psycopg.AsyncConnection.connect(
        URL,
        autocommit=True,
        row_factory=dict_row,
        cursor_factory=psycopg.AsyncRawCursor,
        options=f"-c search_path={SCHEMA}",
    )
    async with conn:
        yield conn


OPENERS: dict[str, Opener] = {
    "autocommit": autocommit,
    "transacciones": transactional,
    "configurada": configured,
    "pool": pool,
    "pool sin conexiones": null_pool,
}


def run(opener: Opener, body: Callable[[Any], Awaitable[None]]) -> None:
    async def go() -> None:
        async with opener() as db:
            await body(db)

    asyncio.run(go())


async def new_store(db: Any) -> PostgresStore:
    made = PostgresStore(db, table=table())
    await made.create_schema()
    return made


async def new_preferences(db: Any) -> PostgresPreferenceStore:
    made = PostgresPreferenceStore(db, table=table())
    await made.create_schema()
    return made


async def new_quiet_hours(db: Any) -> PostgresQuietHoursStore:
    made = PostgresQuietHoursStore(db, table=table())
    await made.create_schema()
    return made


def draft(**changes: Any) -> Draft:
    base: dict[str, Any] = {
        "recipient_id": "ana",
        "type": "nota",
        "category": "activity",
        "title": "Aviso",
        "body": None,
        "action_url": None,
        "payload": {},
        "scope": None,
        "dedupe_key": None,
    }
    return Draft(**{**base, **changes})


# --- las secuencias del spec ------------------------------------------------


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
@pytest.mark.parametrize("path", vectors("bandeja"), ids=lambda p: p.stem)
def test_secuencia(path: Path, opener: Opener) -> None:
    vector = json.loads(path.read_text(encoding="utf-8"))
    run(opener, lambda db: run_store_vector(vector, lambda: new_store(db)))


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
@pytest.mark.parametrize("path", vectors("preferencias-guardadas"), ids=lambda p: p.stem)
def test_secuencia_de_preferencias(path: Path, opener: Opener) -> None:
    vector = json.loads(path.read_text(encoding="utf-8"))
    run(opener, lambda db: run_preference_vector(vector, lambda: new_preferences(db)))


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
@pytest.mark.parametrize("path", vectors("silencio-guardado"), ids=lambda p: p.stem)
def test_secuencia_de_horarios(path: Path, opener: Opener) -> None:
    vector = json.loads(path.read_text(encoding="utf-8"))
    run(opener, lambda db: run_quiet_hours_vector(vector, lambda: new_quiet_hours(db)))


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
@pytest.mark.parametrize("path", vectors("http"), ids=lambda p: p.stem)
def test_secuencia_http(path: Path, opener: Opener) -> None:
    vector = json.loads(path.read_text(encoding="utf-8"))

    async def body(db: Any) -> None:
        stores = Stores(await new_store(db), await new_preferences(db), await new_quiet_hours(db))
        await run_http_vector(vector, lambda: stores)

    run(opener, body)


def test_hay_secuencias() -> None:
    assert len(vectors("bandeja")) >= 8
    assert len(vectors("http")) >= 3


# --- lo propio de Postgres --------------------------------------------------


def test_veinte_pedidos_a_la_vez_con_la_misma_clave_dejan_un_solo_aviso() -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        results = await asyncio.gather(
            *(
                store.add(draft(dedupe_key="k", title=f"intento {i}"), id=f"n{i}", created_at=T0)
                for i in range(20)
            )
        )
        assert sum(result.created for result in results) == 1
        assert len({result.notification.id for result in results}) == 1
        assert await store.count_unread("ana") == 1

    run(pool, body)


def test_dos_pedidos_a_la_vez_con_el_mismo_identificador() -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        results = await asyncio.gather(
            store.add(draft(title="uno"), id="igual", created_at=T0),
            store.add(draft(title="dos"), id="igual", created_at=T0),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        assert len(errors) == 1
        assert isinstance(errors[0], StoreError) and errors[0].reason == "duplicate_id"
        assert await store.count_unread("ana") == 1

    run(pool, body)


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
def test_a_igual_fecha_ordena_el_id_por_punto_de_codigo(opener: Opener) -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        # Por punto de código: "B" < "a" < "b" < "ä" < "😀". Con en_US saldría a, ä, b, B, ...
        for id in ("a", "B", "ä", "b", "😀"):
            await store.add(draft(), id=id, created_at=T0)
        first = await store.list_feed(FeedQuery("ana", limit=2))
        rest = await store.list_feed(FeedQuery("ana", limit=5, cursor=first.next_cursor))
        assert [item.id for item in (*first.items, *rest.items)] == ["😀", "ä", "b", "a", "B"]

    run(opener, body)


def test_el_payload_vuelve_igual() -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        payload = {
            "texto": "ñandú 🔔",
            "nulo": "\u0000",
            "lista": [1, 2.5, None, True],
            "anidado": {"a": {"b": []}},
            "grande": 2**70,
        }
        await store.add(draft(payload=payload), id="n1", created_at=T0)
        page = await store.list_feed(FeedQuery("ana", limit=5))
        assert dict(page.items[0].payload) == payload

    run(autocommit, body)


def test_las_fechas_conservan_los_milisegundos() -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        at = datetime(2999, 12, 31, 23, 59, 59, 999000, tzinfo=UTC)
        await store.add(draft(), id="n1", created_at=at)
        await store.mark_read("n1", "ana", datetime(3000, 1, 1, 0, 0, 0, 1000, tzinfo=UTC))
        (item,) = (await store.list_feed(FeedQuery("ana", limit=5))).items
        assert item.created_at == at
        assert item.read_at == datetime(3000, 1, 1, 0, 0, 0, 1000, tzinfo=UTC)

    run(autocommit, body)


def test_create_schema_se_puede_correr_dos_veces() -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        await store.create_schema()
        await store.add(draft(), id="n1", created_at=T0)
        await store.create_schema()
        assert await store.count_unread("ana") == 1

    run(transactional, body)


def test_la_conexion_sin_autocommit_no_queda_en_una_transaccion() -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        await store.add(draft(), id="n1", created_at=T0)
        await store.count_unread("ana")
        assert db.info.transaction_status == psycopg.pq.TransactionStatus.IDLE

    run(transactional, body)


def test_el_nombre_de_la_tabla_se_valida() -> None:
    with pytest.raises(ValueError, match="identificador"):
        PostgresStore(None, table="x; DROP TABLE y")
    with pytest.raises(ValueError, match="identificador"):
        PostgresPreferenceStore(None, table="a b")
    with pytest.raises(ValueError, match="identificador"):
        postgres_schema(notifications="x-y")


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
def test_guarda_todos_los_cambios_o_ninguno(opener: Opener) -> None:
    async def body(db: Any) -> None:
        store = await new_preferences(db)
        await store.save_changes("ana", [Change("activity", "email", True)])
        roto = [Change("digest", "email", False), Change("digest", None, True)]
        with pytest.raises(psycopg.errors.NotNullViolation):
            await store.save_changes("ana", roto)
        assert await store.get_overrides("ana") == {"activity": {"email": True}}

    run(opener, body)


def test_si_una_celda_se_repite_vale_la_ultima() -> None:
    async def body(db: Any) -> None:
        store = await new_preferences(db)
        await store.save_changes(
            "ana",
            [
                Change("activity", "email", True),
                Change("activity", "sms", True),
                Change("activity", "email", False),
            ],
        )
        assert await store.get_overrides("ana") == {"activity": {"email": False, "sms": True}}

    run(autocommit, body)


@pytest.mark.parametrize("opener", [autocommit, transactional], ids=["autocommit", "transacciones"])
def test_una_misma_conexion_atiende_operaciones_a_la_vez(opener: Opener) -> None:
    async def body(db: Any) -> None:
        store = PostgresStore(db, table=table())
        preferences = PostgresPreferenceStore(db, table=table())
        await store.create_schema()
        await preferences.create_schema()
        await asyncio.gather(
            *(store.add(draft(title=f"aviso {i}"), id=f"n{i}", created_at=T0) for i in range(10)),
            *(preferences.save_changes(f"p{i}", [Change("activity", "email", True)]) for i in range(10)),
        )
        assert await store.count_unread("ana") == 10
        assert db.info.transaction_status == psycopg.pq.TransactionStatus.IDLE

    run(opener, body)


def test_dentro_de_una_transaccion_del_llamador_usa_un_savepoint() -> None:
    async def body(db: Any) -> None:
        store = await new_store(db)
        # Lo que hace el almacén se deshace con la transacción de quien llama.
        with pytest.raises(RuntimeError):
            async with db.transaction():
                await store.add(draft(), id="n1", created_at=T0)
                raise RuntimeError("se cae")
        assert await store.count_unread("ana") == 0
        # Y si el almacén falla, la transacción de quien llama sigue viva.
        async with db.transaction():
            await store.add(draft(), id="n2", created_at=T0)
            with pytest.raises(StoreError):
                await store.add(draft(), id="n2", created_at=T0)
            await store.add(draft(), id="n3", created_at=T0)
        assert await store.count_unread("ana") == 2

    run(transactional, body)


def test_el_nombre_de_la_tabla_no_puede_pasar_de_56_caracteres() -> None:
    assert postgres_schema(notifications="a" * 56)
    with pytest.raises(ValueError, match="identificador"):
        postgres_schema(notifications="a" * 57)
