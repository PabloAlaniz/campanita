"""La forma de la bandeja en el cable: lo que viaja entre el adaptador HTTP y la
interfaz. Está en la sección "La bandeja por HTTP" del spec."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime
from typing import Any

from .._inbox import Cursor, Notification, from_ms, to_ms

_ISO = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})Z", re.ASCII)
_B64URL = re.compile(r"[A-Za-z0-9_-]*", re.ASCII)
_MS = re.compile(r"0|[1-9][0-9]*", re.ASCII)
_EPOCH_YEAR = 1970

#: La fecha más lejana de JavaScript, en milisegundos: el tope de un cursor.
MAX_CURSOR_MS = 8_640_000_000_000_000

#: El último milisegundo que entra en un `datetime` de Python (31/12/9999).
_MAX_DATETIME_MS = to_ms(datetime.max.replace(tzinfo=UTC))


def to_iso(moment: datetime) -> str:
    """`AAAA-MM-DDTHH:MM:SS.mmmZ`, en UTC: el formato de `toISOString()` de JavaScript."""
    u = from_ms(to_ms(moment))
    return (
        f"{u.year:04d}-{u.month:02d}-{u.day:02d}T{u.hour:02d}:{u.minute:02d}:{u.second:02d}"
        f".{u.microsecond // 1000:03d}Z"
    )


def from_iso(text: object) -> datetime | None:
    """El inverso de `to_iso`, estricto: sólo acepta texto que, escrito de nuevo,
    da lo mismo (un 30 de febrero no), y fechas desde 1970. `None` si no."""
    if not isinstance(text, str):
        return None
    match = _ISO.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second, millis = (int(part) for part in match.groups())
    if year < _EPOCH_YEAR:
        return None
    try:
        moment = datetime(year, month, day, hour, minute, second, millis * 1000, tzinfo=UTC)
    except ValueError:
        return None
    return moment if to_iso(moment) == text else None


def item_to_wire(item: Notification) -> dict[str, Any]:
    """Un aviso como lo devuelve `GET /feed`: sin `recipient_id` ni `dedupe_key`."""
    return {
        "id": item.id,
        "type": item.type,
        "category": item.category,
        "title": item.title,
        "body": item.body,
        "action_url": item.action_url,
        "payload": dict(item.payload),
        "scope": item.scope,
        "created_at": to_iso(item.created_at),
        "read_at": None if item.read_at is None else to_iso(item.read_at),
    }


# ── El cursor ───────────────────────────────────────────────────────


def _cursor_text(ms: int, id: str) -> str:
    return base64.urlsafe_b64encode(f"{ms}:{id}".encode()).decode("ascii").rstrip("=")


def encode_cursor(cursor: Cursor) -> str:
    """El base64url sin relleno del UTF-8 de `<created_at_ms>:<id>`."""
    created_at, id = cursor
    return _cursor_text(to_ms(created_at), id)


def decode_cursor_parts(text: str) -> tuple[int, str] | None:
    """`(created_at_ms, id)`, o `None` si el texto no es exactamente un cursor
    que devolvería `encode_cursor`."""
    if not _B64URL.fullmatch(text) or len(text) % 4 == 1:
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    ms_text, colon, id = raw.partition(":")
    if not colon or not _MS.fullmatch(ms_text):
        return None
    ms = int(ms_text)
    if ms > MAX_CURSOR_MS or _cursor_text(ms, id) != text:
        return None
    return ms, id


def decode_cursor(text: str) -> Cursor | None:
    """El inverso de `encode_cursor`, o `None` si el texto no es exactamente un
    cursor que devolvería `encode_cursor`.

    El spec admite fechas hasta la más lejana de JavaScript (año 275760), y un
    `datetime` llega hasta el 9999: un cursor más allá vale, pero acá se lee como
    el último milisegundo de 9999. Para el valor exacto, `decode_cursor_parts`.
    """
    parts = decode_cursor_parts(text)
    if parts is None:
        return None
    ms, id = parts
    return from_ms(min(ms, _MAX_DATETIME_MS)), id
