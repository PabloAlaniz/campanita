"""Las secuencias de `spec/vectors/http/` contra `InboxApp`, con cada almacén incluido."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import urlencode

import pytest

from campanita import Draft, parse_policy
from campanita._inbox import from_ms
from campanita.http import InboxApp, decode_cursor, decode_cursor_parts, encode_cursor, from_iso, to_iso
from campanita.stores import (
    MemoryPreferenceStore,
    MemoryQuietHoursStore,
    MemoryStore,
    SqlitePreferenceStore,
    SqliteQuietHoursStore,
    SqliteStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

    from campanita.ports import NotificationStore, PreferenceStore, QuietHoursStore

VECTORS = sorted((Path(__file__).resolve().parents[2] / "spec" / "vectors" / "http").glob("*.json"))
ROOT = "/api/avisos"


class Stores(NamedTuple):
    store: NotificationStore
    preferences: PreferenceStore
    quiet_hours: QuietHoursStore


def memory() -> Stores:
    return Stores(MemoryStore(), MemoryPreferenceStore(), MemoryQuietHoursStore())


def sqlite() -> Stores:
    db = sqlite3.connect(":memory:", isolation_level=None)
    store, preferences, quiet_hours = SqliteStore(db), SqlitePreferenceStore(db), SqliteQuietHoursStore(db)
    for made in (store, preferences, quiet_hours):
        made.create_schema()
    return Stores(store, preferences, quiet_hours)


STORES: dict[str, Callable[[], Stores]] = {"memory": memory, "sqlite": sqlite}


class Reply(NamedTuple):
    status: int
    headers: dict[str, str]
    body: Any


async def call(
    app: InboxApp,
    method: str,
    path: str,
    *,
    query: list[tuple[str, str]] | None = None,
    body: bytes = b"",
    root_path: str = ROOT,
    raw_path: bytes | None = None,
    extra: dict[str, Any] | None = None,
) -> Reply:
    """Un servidor ASGI mínimo: un pedido entero, una respuesta entera."""
    full = root_path + path
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "root_path": root_path,
        # `path` va decodificado; el adaptador mira `raw_path`, como llegó.
        "path": full.replace("%2F", "/").replace("%20", " "),
        "raw_path": full.encode("ascii") if raw_path is None else raw_path,
        "query_string": urlencode(query or []).encode("ascii"),
        "headers": [],
        **(extra or {}),
    }
    pending = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start, content = sent
    assert start["type"] == "http.response.start"
    assert content["type"] == "http.response.body"
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    assert headers["content-length"] == str(len(content["body"]))
    return Reply(start["status"], headers, json.loads(content["body"]))


async def run_http_vector(vector: dict[str, Any], make: Callable[[], Stores]) -> None:
    clock = [0]
    stores = make()
    available = vector["available"]
    app = InboxApp(
        store=stores.store,
        resolve_recipient=lambda scope: scope["campanita.test.as"],
        policy=parse_policy(vector["policy"]),
        preferences=stores.preferences,
        available=lambda _who, _scope: available,
        quiet_hours=stores.quiet_hours,
        now=lambda: from_ms(clock[0]),
    )
    for index, step in enumerate(vector["steps"]):
        if step["op"] == "add":
            await stores.store.add(Draft(**step["draft"]), id=step["id"], created_at=from_ms(step["at_ms"]))
            continue
        clock[0] = step["at_ms"]
        if "raw_body" in step:
            body = step["raw_body"].encode()
        elif "body" in step:
            body = json.dumps(step["body"]).encode()
        else:
            body = b""
        reply = await call(
            app,
            step["method"],
            step["path"],
            query=[(key, value) for key, value in step["query"]],
            body=body,
            extra={"campanita.test.as": step["as"]},
        )
        assert reply.headers["content-type"] == "application/json; charset=utf-8"
        assert reply.headers["cache-control"] == "no-store"
        expect = step["expect"]
        got: Any
        if expect is None:
            # Una aplicación entera no deja pasar el pedido: contesta 404.
            got = None if (reply.status, reply.body["error"]["code"]) == (404, "not_found") else reply
        elif expect["status"] >= 400 and isinstance(reply.body.get("error"), dict):
            error = reply.body["error"]
            assert isinstance(error["message"], str) and error["message"]
            compared = {"code": error["code"]}
            for key in ("category", "channel"):
                if key in expect["body"]["error"]:
                    compared[key] = error.get(key)
            got = {"status": reply.status, "body": {"error": compared}}
        else:
            got = {"status": reply.status, "body": reply.body}
        if got != expect:
            raise AssertionError(
                f"{vector['id']} paso {index} ({step['note']}), {step['method']} {step['path']}:\n"
                f"  se esperaba: {expect!r}\n"
                f"  se obtuvo:   {got!r}"
            )


def test_hay_secuencias_http() -> None:
    assert len(VECTORS) >= 3


@pytest.mark.parametrize("make", STORES.values(), ids=STORES.keys())
@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_secuencia_http(path: Path, make: Callable[[], Stores]) -> None:
    asyncio.run(run_http_vector(json.loads(path.read_text(encoding="utf-8")), make))


# ── El cursor y las fechas ──────────────────────────────────────────


def test_el_cursor_va_y_vuelve_con_ids_que_no_son_ascii() -> None:
    moment = datetime(2026, 11, 20, 13, 5, 0, 123_000, tzinfo=UTC)
    for id in ("ñandú:1", "通知/7", "🔔 a:b:c", "", "x/y z"):
        text = encode_cursor((moment, id))
        assert "=" not in text and "+" not in text and "/" not in text
        assert decode_cursor(text) == (moment, id)


def test_el_cursor_es_canonico() -> None:
    assert decode_cursor_parts("ODY0MDAwMDAwMDAwMDAwMDpuNQ") == (8_640_000_000_000_000, "n5")
    assert decode_cursor_parts("ODY0MDAwMDAwMDAwMDAwMTpuNQ") is None  # uno más que el tope
    assert decode_cursor("NTAwMDpuNQ=") is None  # con relleno
    assert decode_cursor("NTAwMDpuNR") is None  # bits de sobra en el último carácter
    assert decode_cursor("_w") is None  # no es UTF-8


def test_las_fechas_son_las_de_javascript() -> None:
    moment = datetime(2026, 11, 20, 13, 5, 0, 123_999, tzinfo=UTC)
    assert to_iso(moment) == "2026-11-20T13:05:00.123Z"
    assert from_iso("2026-11-20T13:05:00.123Z") == moment.replace(microsecond=123_000)
    assert to_iso(from_ms(0)) == "1970-01-01T00:00:00.000Z"
    for bad in ("2026-02-29T00:00:00.000Z", "2026-11-20T24:00:00.000Z", "1969-12-31T23:59:59.999Z", 5):
        assert from_iso(bad) is None


# ── ASGI ────────────────────────────────────────────────────────────


def app_with(**options: Any) -> InboxApp:
    return InboxApp(store=MemoryStore(), resolve_recipient=lambda _scope: "ana", **options)


def test_lifespan() -> None:
    async def run() -> list[MutableMapping[str, Any]]:
        incoming = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent: list[MutableMapping[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return incoming.pop(0)

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        await app_with()({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
        return sent

    assert asyncio.run(run()) == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]


def test_sin_preferencias_ni_horario_esas_rutas_no_existen() -> None:
    async def run() -> None:
        app = app_with()
        for path in ("/preferences", "/quiet-hours"):
            reply = await call(app, "GET", path)
            assert (reply.status, reply.body["error"]["code"]) == (404, "not_found")
        assert (await call(app, "GET", "/feed")).status == 200

    asyncio.run(run())


def test_resolve_recipient_y_available_pueden_ser_async() -> None:
    async def who(_scope: Any) -> str:
        return "ana"

    async def channels(_who: str, _scope: str | None) -> list[str]:
        return ["email"]

    async def run() -> None:
        policy = parse_policy(
            {"activity": {"inapp": {"default": True, "locked": True}, "email": {"default": False}}}
        )
        app = InboxApp(
            store=MemoryStore(),
            resolve_recipient=who,
            policy=policy,
            preferences=MemoryPreferenceStore(),
            available=channels,
        )
        reply = await call(app, "PATCH", "/preferences", body=b'{"activity": {"email": true}}')
        assert reply.body == {
            "categories": {
                "activity": {
                    "inapp": {"enabled": True, "locked": True},
                    "email": {"enabled": True, "locked": False},
                }
            }
        }

    asyncio.run(run())


def test_la_ruta_se_mira_debajo_de_root_path() -> None:
    async def run() -> None:
        app = app_with()
        # Un servidor que ya sacó `root_path` de `path` y de `raw_path`.
        stripped = await call(app, "GET", "", raw_path=b"/feed", extra={"path": "/feed"})
        assert stripped.status == 200
        # Sin `raw_path`, se usa `path`.
        reply = await call(app, "GET", "/unread-count", extra={"raw_path": None})
        assert reply.body == {"count": 0, "capped": False}
        # Montada en la raíz.
        assert (await call(app, "GET", "/feed", root_path="")).status == 200
        # El prefijo solo no es una ruta.
        assert (await call(app, "GET", "")).status == 404

    asyncio.run(run())


def test_un_id_mal_codificado_no_es_una_ruta() -> None:
    async def run() -> None:
        app = app_with()
        for segment in ("%zz", "%C3", "%"):
            reply = await call(app, "POST", f"/feed/{segment}/read")
            assert (reply.status, reply.body["error"]["code"]) == (404, "not_found")
        # Un id con bytes UTF-8 sin codificar en `raw_path` se lee igual que codificado.
        store = MemoryStore()
        draft = Draft("ana", "nota", "activity", "Aviso", None, None, {}, None, None)
        await store.add(draft, id="ñ", created_at=from_ms(0))
        app = InboxApp(store=store, resolve_recipient=lambda _scope: "ana")
        raw = (ROOT + "/feed/ñ/read").encode()
        assert (await call(app, "POST", "/feed/%C3%B1/read", raw_path=raw)).body == {"changed": True}

    asyncio.run(run())


def test_un_cuerpo_con_nan_no_es_json() -> None:
    async def run() -> None:
        reply = await call(app_with(), "POST", "/feed/read-all", body=b'{"before": NaN}')
        assert (reply.status, reply.body["error"]["code"]) == (400, "invalid_body")

    asyncio.run(run())


def test_available_recibe_el_ambito_de_la_consulta() -> None:
    """La misma función de disponibilidad que usa el Notifier: persona y ámbito."""
    asked: list[tuple[str, str | None]] = []

    def channels(who: str, scope: str | None) -> list[str]:
        asked.append((who, scope))
        return ["email"] if scope == "acme" else []

    async def run() -> None:
        policy = parse_policy(
            {"activity": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}}
        )
        app = InboxApp(
            store=MemoryStore(),
            resolve_recipient=lambda _scope: "ana",
            policy=policy,
            preferences=MemoryPreferenceStore(),
            available=channels,
        )
        with_scope = await call(app, "GET", "/preferences", query=[("scope", "acme")])
        without = await call(app, "GET", "/preferences")
        assert with_scope.body["categories"]["activity"]["email"] == {"enabled": True, "locked": False}
        assert without.body["categories"]["activity"]["email"] == {"enabled": True, "locked": True}
        assert asked == [("ana", "acme"), ("ana", None)]

    asyncio.run(run())
