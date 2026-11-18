"""Corre las secuencias del spec contra un almacén de avisos o de preferencias.

Es lo que usa este paquete para probar sus adaptadores, y lo que puede usar
quien escriba el suyo:

    vector = json.loads(Path("spec/vectors/bandeja/basico.json").read_text())
    await run_store_vector(vector, lambda: MiAlmacen(...))

`run_preference_vector` hace lo mismo con `spec/vectors/preferencias-guardadas/`.

Cada vector necesita un almacén vacío.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ._catalog import Draft
from ._inbox import FeedQuery, Notification, StoreError, from_ms, to_ms
from ._types import Change
from .ports import NotificationStore, PreferenceStore


def notification_to_wire(item: Notification) -> dict[str, Any]:
    """El aviso con los nombres y las fechas que usan los vectores."""
    return {
        "id": item.id,
        "recipient_id": item.recipient_id,
        "type": item.type,
        "category": item.category,
        "title": item.title,
        "body": item.body,
        "action_url": item.action_url,
        "payload": dict(item.payload),
        "scope": item.scope,
        "dedupe_key": item.dedupe_key,
        "created_at_ms": to_ms(item.created_at),
        "read_at_ms": None if item.read_at is None else to_ms(item.read_at),
    }


async def run_store_vector(
    vector: Mapping[str, Any],
    make_store: Callable[[], NotificationStore | Awaitable[NotificationStore]],
) -> None:
    """Corre un vector de tipo `bandeja`. Lanza `AssertionError` en el primer
    paso que no coincide, diciendo cuál."""
    if vector.get("kind") != "bandeja":
        raise ValueError("run_store_vector sólo corre vectores de tipo `bandeja`")
    made = make_store()
    store = await made if isinstance(made, Awaitable) else made

    for index, step in enumerate(vector["steps"]):
        try:
            got: Any = await _run(store, step)
        except StoreError as error:
            got = {"error": error.reason}
        if got != step["expect"]:
            raise AssertionError(
                f"{vector['id']} paso {index} ({step['note']}), operación `{step['op']}`:\n"
                f"  se esperaba: {step['expect']!r}\n"
                f"  se obtuvo:   {got!r}"
            )


async def _run(store: NotificationStore, step: Mapping[str, Any]) -> Any:
    op = step["op"]
    if op == "add":
        result = await store.add(Draft(**step["draft"]), id=step["id"], created_at=from_ms(step["at_ms"]))
        return {"created": result.created, "notification": notification_to_wire(result.notification)}
    if op == "list":
        query = dict(step["query"])
        cursor = query.pop("cursor", None)
        page = await store.list_feed(
            FeedQuery(
                **query, cursor=None if cursor is None else (from_ms(cursor["created_at_ms"]), cursor["id"])
            )
        )
        return {
            "items": [notification_to_wire(item) for item in page.items],
            "next_cursor": None
            if page.next_cursor is None
            else {"created_at_ms": to_ms(page.next_cursor[0]), "id": page.next_cursor[1]},
        }
    if op == "count_unread":
        return await store.count_unread(step["recipient_id"], scope=step["scope"], cap=step["cap"])
    if op == "mark_read":
        return await store.mark_read(step["id"], step["recipient_id"], from_ms(step["at_ms"]))
    if op == "mark_all_read":
        return await store.mark_all_read(
            step["recipient_id"],
            before=from_ms(step["before_ms"]),
            read_at=from_ms(step["at_ms"]),
            scope=step["scope"],
            category=step["category"],
        )
    raise ValueError(f"operación desconocida: {op}")


async def run_preference_vector(
    vector: Mapping[str, Any],
    make_store: Callable[[], PreferenceStore | Awaitable[PreferenceStore]],
) -> None:
    """Corre un vector de tipo `preferencias-guardadas`. Lanza `AssertionError`
    en el primer paso que no coincide, diciendo cuál."""
    if vector.get("kind") != "preferencias-guardadas":
        raise ValueError("run_preference_vector sólo corre vectores de tipo `preferencias-guardadas`")
    made = make_store()
    store = await made if isinstance(made, Awaitable) else made

    for index, step in enumerate(vector["steps"]):
        got: Any
        if step["op"] == "save":
            changes = [Change(c["category"], c["channel"], c["enabled"]) for c in step["changes"]]
            await store.save_changes(step["recipient_id"], changes, scope=step["scope"])
            got = None
        elif step["op"] == "get":
            found = await store.get_overrides(step["recipient_id"], scope=step["scope"])
            got = {category: dict(channels) for category, channels in found.items()}
        else:
            raise ValueError(f"operación desconocida: {step['op']}")
        if got != step["expect"]:
            raise AssertionError(
                f"{vector['id']} paso {index} ({step['note']}), operación `{step['op']}`:\n"
                f"  se esperaba: {step['expect']!r}\n"
                f"  se obtuvo:   {got!r}"
            )
