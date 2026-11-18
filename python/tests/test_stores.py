"""Las secuencias de `spec/vectors/bandeja/` contra cada almacén incluido."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from campanita import Change, Draft, FeedQuery, StoreError
from campanita.conformance import run_preference_vector, run_store_vector
from campanita.ports import NotificationStore, PreferenceStore
from campanita.stores import MemoryPreferenceStore, MemoryStore, SqlitePreferenceStore, SqliteStore

VECTORS = sorted((Path(__file__).resolve().parents[2] / "spec" / "vectors" / "bandeja").glob("*.json"))


def sqlite_store() -> SqliteStore:
    # `isolation_level=None`: las transacciones las maneja el almacén.
    store = SqliteStore(sqlite3.connect(":memory:", isolation_level=None))
    store.create_schema()
    return store


STORES: dict[str, Callable[[], NotificationStore]] = {"memory": MemoryStore, "sqlite": sqlite_store}


def test_hay_secuencias() -> None:
    assert len(VECTORS) >= 8


@pytest.mark.parametrize("make_store", STORES.values(), ids=STORES.keys())
@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_secuencia(path: Path, make_store: Callable[[], NotificationStore]) -> None:
    asyncio.run(run_store_vector(json.loads(path.read_text(encoding="utf-8")), make_store))


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


T0 = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("make_store", STORES.values(), ids=STORES.keys())
def test_el_corredor_avisa_en_que_paso_falla(make_store: Callable[[], NotificationStore]) -> None:
    vector = {
        "id": "prueba",
        "kind": "bandeja",
        "steps": [
            {
                "note": "contar",
                "op": "count_unread",
                "recipient_id": "ana",
                "scope": None,
                "cap": None,
                "expect": 7,
            }
        ],
    }
    with pytest.raises(AssertionError, match=r"prueba paso 0 \(contar\), operación `count_unread`"):
        asyncio.run(run_store_vector(vector, make_store))


@pytest.mark.parametrize("make_store", STORES.values(), ids=STORES.keys())
def test_las_fechas_se_guardan_con_milisegundos_y_en_utc(make_store: Callable[[], NotificationStore]) -> None:
    async def run() -> None:
        store = make_store()
        local = timezone(timedelta(hours=-3))
        moment = datetime(2030, 1, 1, 9, 0, 0, 123_987, tzinfo=local)
        result = await store.add(draft(), id="n1", created_at=moment)
        assert result.notification.created_at == datetime(2030, 1, 1, 12, 0, 0, 123_000, tzinfo=UTC)
        page = await store.list_feed(FeedQuery("ana", limit=5))
        assert page.items[0].created_at == result.notification.created_at

    asyncio.run(run())


@pytest.mark.parametrize("make_store", STORES.values(), ids=STORES.keys())
def test_una_fecha_sin_zona_se_rechaza(make_store: Callable[[], NotificationStore]) -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        asyncio.run(make_store().add(draft(), id="n1", created_at=datetime(2030, 1, 1)))


@pytest.mark.parametrize("make_store", STORES.values(), ids=STORES.keys())
def test_tocar_el_payload_despues_no_cambia_lo_guardado(make_store: Callable[[], NotificationStore]) -> None:
    async def run() -> None:
        store = make_store()
        payload: dict[str, Any] = {"lista": [1, 2]}
        await store.add(draft(payload=payload), id="n1", created_at=T0)
        payload["lista"].append(3)
        page = await store.list_feed(FeedQuery("ana", limit=5))
        assert page.items[0].payload == {"lista": [1, 2]}

    asyncio.run(run())


@pytest.mark.parametrize("make_store", STORES.values(), ids=STORES.keys())
def test_un_limite_que_no_es_entero_se_rechaza(make_store: Callable[[], NotificationStore]) -> None:
    for limit in (True, 2.0, "3"):
        with pytest.raises(StoreError) as rejected:
            asyncio.run(make_store().list_feed(FeedQuery("ana", limit=limit)))  # type: ignore[arg-type]
        assert rejected.value.reason == "invalid_limit"


def test_sqlite_dos_conexiones_con_la_misma_clave_dejan_un_solo_aviso(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "campanita.db"
        first = SqliteStore(sqlite3.connect(path, isolation_level=None))
        first.create_schema()
        second = SqliteStore(sqlite3.connect(path, isolation_level=None))
        a = await first.add(draft(dedupe_key="k"), id="n1", created_at=T0)
        b = await second.add(draft(dedupe_key="k", title="Otro"), id="n2", created_at=T0)
        assert (a.created, b.created, b.notification.id) == (True, False, "n1")
        assert await second.count_unread("ana") == 1

    asyncio.run(run())


def test_sqlite_el_nombre_de_la_tabla_se_valida() -> None:
    with pytest.raises(ValueError, match="identificador"):
        SqliteStore(sqlite3.connect(":memory:"), table="x; DROP TABLE y")


# --- preferencias guardadas -------------------------------------------------

PREFERENCE_VECTORS = sorted(
    (Path(__file__).resolve().parents[2] / "spec" / "vectors" / "preferencias-guardadas").glob("*.json")
)


def sqlite_preferences() -> SqlitePreferenceStore:
    store = SqlitePreferenceStore(sqlite3.connect(":memory:", isolation_level=None))
    store.create_schema()
    return store


PREFERENCE_STORES: dict[str, Callable[[], PreferenceStore]] = {
    "memory": MemoryPreferenceStore,
    "sqlite": sqlite_preferences,
}


def test_hay_secuencias_de_preferencias() -> None:
    assert len(PREFERENCE_VECTORS) >= 2


@pytest.mark.parametrize("make_store", PREFERENCE_STORES.values(), ids=PREFERENCE_STORES.keys())
@pytest.mark.parametrize("path", PREFERENCE_VECTORS, ids=lambda p: p.stem)
def test_secuencia_de_preferencias(path: Path, make_store: Callable[[], PreferenceStore]) -> None:
    asyncio.run(run_preference_vector(json.loads(path.read_text(encoding="utf-8")), make_store))


@pytest.mark.parametrize("make_store", PREFERENCE_STORES.values(), ids=PREFERENCE_STORES.keys())
def test_tocar_lo_devuelto_no_cambia_las_preferencias(make_store: Callable[[], PreferenceStore]) -> None:
    async def run() -> None:
        store = make_store()
        await store.save_changes("ana", [Change("activity", "email", True)])
        found = await store.get_overrides("ana")
        found["activity"]["email"] = False  # type: ignore[index]
        assert await store.get_overrides("ana") == {"activity": {"email": True}}

    asyncio.run(run())


def test_sqlite_guarda_todos_los_cambios_o_ninguno() -> None:
    async def run() -> None:
        store = sqlite_preferences()
        await store.save_changes("ana", [Change("activity", "email", True)])
        roto = [Change("digest", "email", False), Change("digest", None, True)]  # type: ignore[arg-type]
        with pytest.raises(sqlite3.IntegrityError):
            await store.save_changes("ana", roto)
        assert await store.get_overrides("ana") == {"activity": {"email": True}}

    asyncio.run(run())


def test_las_preferencias_guardadas_sirven_para_resolver_canales() -> None:
    from campanita import parse_policy, resolve_channels, validate_patch

    async def run() -> None:
        policy = parse_policy(
            {
                "security": {
                    "inapp": {"default": True, "locked": True},
                    "email": {"default": True, "locked": True},
                },
                "activity": {"inapp": {"default": True, "locked": True}, "email": {"default": False}},
            }
        )
        store = MemoryPreferenceStore()
        await store.save_changes("ana", validate_patch(policy, {"activity": {"email": True}}, ["email"]))
        overrides = await store.get_overrides("ana")
        assert resolve_channels(policy, "activity", overrides, ["email"]) == {"email"}
        assert resolve_channels(policy, "activity", await store.get_overrides("beto"), ["email"]) == set()

    asyncio.run(run())
