"""La bandeja por HTTP como aplicación ASGI 3, con la biblioteca estándar."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, TypeAlias, TypeVar, cast
from urllib.parse import parse_qsl, unquote, unquote_to_bytes

from .._channels import effective_matrix, validate_patch
from .._inbox import Cursor, FeedQuery, from_ms
from .._quiet import InvalidQuietHours, QuietHours, local_minute, parse_quiet_hours
from .._types import PatchRejected
from ._wire import _MAX_DATETIME_MS, decode_cursor_parts, encode_cursor, from_iso, item_to_wire

if TYPE_CHECKING:
    from .._policy import Policy
    from ..ports import NotificationStore, PreferenceStore, QuietHoursStore

#: Los tipos de ASGI 3, sin depender de un paquete que los defina.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_T = TypeVar("_T")
MaybeAwaitable: TypeAlias = _T | Awaitable[_T]

_MENSAJES: Final[dict[str, str]] = {
    "not_found": "esa ruta no existe",
    "method_not_allowed": "esa ruta no acepta ese método",
    "unauthenticated": "no se sabe quién hace el pedido",
    "invalid_limit": "el límite tiene que ser un entero de 1 a 100",
    "invalid_cursor": "el cursor no es uno de los que devuelve la bandeja",
    "invalid_cap": "el tope tiene que ser un entero de 1 a 1000",
    "invalid_query": "la consulta tiene un parámetro inválido o repetido",
    "invalid_body": "el cuerpo del pedido no tiene la forma esperada",
    "invalid_quiet_hours": "el horario no es válido",
    "unknown_timezone": "la zona horaria no existe",
}

_HEADERS: Final = [
    (b"content-type", b"application/json; charset=utf-8"),
    (b"cache-control", b"no-store"),
]

_DECIMAL = re.compile(r"[1-9][0-9]*", re.ASCII)
_READ = re.compile(r"/feed/([^/]+)/read")
_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_READ_ALL_KEYS = frozenset({"before", "scope", "category"})
_NO_BODY: Final = object()
_ASCII_END: Final = 0x80


class _Response:
    __slots__ = ("body", "status")

    def __init__(self, status: int, body: Mapping[str, Any]) -> None:
        self.status = status
        self.body = body


def _ok(body: Mapping[str, Any]) -> _Response:
    return _Response(200, body)


def _fail(status: int, code: str, message: str | None = None, **where: Any) -> _Response:
    return _Response(status, {"error": {"code": code, "message": message or _MENSAJES[code], **where}})


async def _resolve(value: MaybeAwaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        return await cast("Awaitable[_T]", value)
    return value


def _bounded_int(text: str | None, fallback: int, maximum: int) -> int | None:
    """Un entero de 1 a `maximum`, en decimal y sin ceros a la izquierda."""
    if text is None:
        return fallback
    return int(text) if _DECIMAL.fullmatch(text) and int(text) <= maximum else None


def _relative_path(scope: Scope) -> str:
    """La ruta debajo de `root_path`, todavía codificada.

    Se mira la ruta como llegó (`raw_path`) para que un `%2F` dentro de un id no
    sea un separador. Según la versión de ASGI y del servidor, `path` y `raw_path`
    traen `root_path` adelante o no: si lo traen, se saca.
    """
    raw_path = scope.get("raw_path")
    if isinstance(raw_path, bytes | bytearray):
        raw = bytes(raw_path).split(b"?", 1)[0]
        # Un byte que no es ASCII llegó sin codificar: se codifica, como haría un cliente.
        path = "".join(chr(byte) if byte < _ASCII_END else f"%{byte:02X}" for byte in raw)
    else:
        path = str(scope.get("path", ""))
    root = str(scope.get("root_path", "")).rstrip("/")
    if not root:
        return path
    root_segments = root.split("/")
    segments = path.split("/")
    prefix = segments[: len(root_segments)]
    if [unquote(segment) for segment in prefix] != root_segments:
        return path
    rest = segments[len(root_segments) :]
    return "/" + "/".join(rest) if rest else ""


def _decode_segment(segment: str) -> str | None:
    """Un segmento de la ruta decodificado como `decodeURIComponent`: un `%` suelto
    o bytes que no son UTF-8 lo invalidan."""
    if _PERCENT.search(segment):
        return None
    try:
        return unquote_to_bytes(segment).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} no es JSON")


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _NO_BODY


def _json_default(value: object) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"{type(value).__name__} no se puede escribir como JSON")


class InboxApp:
    """La bandeja por HTTP: una aplicación ASGI 3 que se monta bajo un prefijo.

        app.mount("/api/avisos", InboxApp(store=store, resolve_recipient=quien_pide))

    El contrato está en la sección "La bandeja por HTTP" del spec. Como es una
    aplicación entera, una ruta que no es del contrato contesta `404 not_found`.
    """

    def __init__(  # noqa: PLR0913 — dependencias inyectadas, solo por nombre
        self,
        *,
        store: NotificationStore,
        resolve_recipient: Callable[[Scope], MaybeAwaitable[str | None]],
        policy: Policy | None = None,
        preferences: PreferenceStore | None = None,
        available: Callable[[str], MaybeAwaitable[Iterable[str]]] | None = None,
        quiet_hours: QuietHoursStore | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """- `store`: de dónde se lee la bandeja.
        - `resolve_recipient`: quién hace el pedido, a partir del `scope` de ASGI;
          `None` si no se sabe (responde 401). Es la única fuente del destinatario.
        - `policy` y `preferences`: con los dos, se sirve `/preferences`.
        - `available`: los canales disponibles para esa persona. Por omisión,
          todos los de la política.
        - `quiet_hours`: con almacén de horarios, se sirve `/quiet-hours`.
        - `now`: la hora del servidor, con zona horaria. Para pruebas.
        """
        self._store = store
        self._resolve_recipient = resolve_recipient
        self._policy = policy
        self._preferences = preferences
        self._available = available
        self._quiet_hours = quiet_hours
        self._now = now or (lambda: datetime.now(UTC))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind == "http":
            response = await self._handle(scope, receive)
            payload = json.dumps(
                response.body,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                default=_json_default,
            ).encode("utf-8")
            headers = [*_HEADERS, (b"content-length", str(len(payload)).encode("ascii"))]
            await send({"type": "http.response.start", "status": response.status, "headers": headers})
            await send({"type": "http.response.body", "body": payload})
        elif kind == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif kind == "websocket":
            # No hay rutas de websocket: se rechaza la conexión.
            await receive()
            await send({"type": "websocket.close", "code": 1000})
        else:
            raise ValueError(f"InboxApp no atiende el tipo de ASGI {kind!r}")

    # TODO(sprint-3): una tabla de rutas reemplaza esta cadena de if y saca el noqa.
    async def _handle(self, scope: Scope, receive: Receive) -> _Response:  # noqa: C901, PLR0911, PLR0912
        path = _relative_path(scope)
        method = str(scope.get("method", "GET")).upper()

        # 1. La ruta. 2. El método.
        read_id: str | None = None
        match = _READ.fullmatch(path)
        if match is not None:
            read_id = _decode_segment(match.group(1))
            if read_id is None:
                return _fail(404, "not_found")
            allowed: tuple[str, ...] = ("POST",)
        elif path in ("/feed", "/unread-count"):
            allowed = ("GET",)
        elif path == "/feed/read-all":
            allowed = ("POST",)
        elif path == "/preferences" and self._policy is not None and self._preferences is not None:
            allowed = ("GET", "PATCH")
        elif path == "/quiet-hours" and self._quiet_hours is not None:
            allowed = ("GET", "PUT")
        else:
            return _fail(404, "not_found")
        if method not in allowed:
            return _fail(405, "method_not_allowed")

        # 3. Quién pide.
        who = await _resolve(self._resolve_recipient(scope))
        if who is None:
            return _fail(401, "unauthenticated")

        # 4 y 5. La consulta, el cuerpo y la operación.
        query_string = bytes(scope.get("query_string", b"")).decode("utf-8", "replace")
        params: dict[str, str] = {}
        for key, value in parse_qsl(query_string, keep_blank_values=True):
            if key in params:
                return _fail(400, "invalid_query")
            params[key] = value

        if read_id is not None:
            return _ok({"changed": await self._store.mark_read(read_id, who, self._now())})
        if path == "/feed":
            return await self._feed(who, params)
        if path == "/unread-count":
            return await self._count(who, params)
        if path == "/feed/read-all":
            return await self._read_all(who, await _read_body(receive))
        if path == "/preferences":
            body = await _read_body(receive) if method == "PATCH" else _NO_BODY
            return await self._prefs(who, method, body, params.get("scope"))
        body = await _read_body(receive) if method == "PUT" else _NO_BODY
        return await self._quiet(who, method, body)

    async def _feed(self, who: str, params: Mapping[str, str]) -> _Response:
        limit = _bounded_int(params.get("limit"), 20, 100)
        if limit is None:
            return _fail(400, "invalid_limit")
        unread_only = params.get("unread_only", "false")
        if unread_only not in ("true", "false"):
            return _fail(400, "invalid_query")
        cursor: Cursor | None = None
        if "cursor" in params:
            parts = decode_cursor_parts(params["cursor"])
            if parts is None:
                return _fail(400, "invalid_cursor")
            ms, id = parts
            # Más allá de lo que entra en un `datetime` no se pudo guardar nada:
            # todo lo guardado está después de ese cursor.
            cursor = None if ms > _MAX_DATETIME_MS else (from_ms(ms), id)
        page = await self._store.list_feed(
            FeedQuery(
                recipient_id=who,
                limit=limit,
                cursor=cursor,
                unread_only=unread_only == "true",
                category=params.get("category"),
                scope=params.get("scope"),
            )
        )
        return _ok(
            {
                "items": [item_to_wire(item) for item in page.items],
                "next_cursor": None if page.next_cursor is None else encode_cursor(page.next_cursor),
            }
        )

    async def _count(self, who: str, params: Mapping[str, str]) -> _Response:
        cap = _bounded_int(params.get("cap"), 99, 1000)
        if cap is None:
            return _fail(400, "invalid_cap")
        n = await self._store.count_unread(who, scope=params.get("scope"), cap=cap + 1)
        return _ok({"count": min(n, cap), "capped": n > cap})

    async def _read_all(self, who: str, body: Any) -> _Response:
        if not isinstance(body, dict) or not body.keys() <= _READ_ALL_KEYS:
            return _fail(400, "invalid_body")
        before = from_iso(body.get("before"))
        scope, category = body.get("scope"), body.get("category")
        if before is None or not all(v is None or isinstance(v, str) for v in (scope, category)):
            return _fail(400, "invalid_body")
        changed = await self._store.mark_all_read(
            who, before=before, read_at=self._now(), scope=scope, category=category
        )
        return _ok({"changed": changed})

    async def _channels_for(self, who: str) -> list[str]:
        if self._available is not None:
            return list(await _resolve(self._available(who)))
        channels: dict[str, None] = {}
        for cells in (self._policy or {}).values():
            channels.update(dict.fromkeys(cells))
        return list(channels)

    async def _prefs(self, who: str, method: str, body: Any, scope: str | None) -> _Response:
        policy, preferences = self._policy, self._preferences
        if policy is None or preferences is None:
            raise RuntimeError("_prefs sin política o sin almacén de preferencias")
        available = await self._channels_for(who)
        if method == "PATCH":
            if not isinstance(body, dict) or not all(isinstance(v, dict) for v in body.values()):
                return _fail(400, "invalid_body")
            try:
                changes = validate_patch(policy, body, available)
            except PatchRejected as rejected:
                return _fail(
                    422,
                    rejected.reason,
                    str(rejected),
                    category=rejected.category,
                    channel=rejected.channel,
                )
            await preferences.save_changes(who, changes, scope=scope)
        overrides = await preferences.get_overrides(who, scope=scope)
        matrix = effective_matrix(policy, overrides, available)
        return _ok(
            {
                "categories": {
                    category: {
                        channel: {"enabled": cell.enabled, "locked": cell.locked}
                        for channel, cell in cells.items()
                    }
                    for category, cells in matrix.items()
                }
            }
        )

    async def _quiet(self, who: str, method: str, body: Any) -> _Response:
        store = self._quiet_hours
        if store is None:
            raise RuntimeError("_quiet sin almacén de horarios")
        if method == "PUT":
            if body is _NO_BODY:
                return _fail(400, "invalid_body")
            try:
                parsed = parse_quiet_hours(body)
            except InvalidQuietHours as invalid:
                return _fail(422, "invalid_quiet_hours", str(invalid))
            if local_minute(parsed.timezone, self._now()) is None:
                return _fail(422, "unknown_timezone")
            await store.set_quiet_hours(who, parsed)
        found = await store.get_quiet_hours(who)
        return _ok({"quiet_hours": None if found is None else _quiet_hours_to_wire(found)})


def _quiet_hours_to_wire(quiet_hours: QuietHours) -> dict[str, Any]:
    return {
        "enabled": quiet_hours.enabled,
        "start": quiet_hours.start,
        "end": quiet_hours.end,
        "timezone": quiet_hours.timezone,
    }


async def _read_body(receive: Receive) -> Any:
    """El cuerpo como JSON, o `_NO_BODY` si no es JSON."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return _NO_BODY
        chunks.append(bytes(message.get("body", b"")))
        if not message.get("more_body", False):
            return _parse_json(b"".join(chunks))
