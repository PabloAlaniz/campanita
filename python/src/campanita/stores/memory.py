"""Un almacén en memoria. Sirve para tests y para probar campanita sin base."""

from __future__ import annotations

import copy
from dataclasses import replace
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
    paginate,
    to_ms,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from .._catalog import Draft
    from .._quiet import QuietHours
    from .._types import Change, Overrides


class MemoryStore:
    """Todo vive en el proceso y se pierde al terminar."""

    def __init__(self) -> None:
        self._by_id: dict[str, Notification] = {}
        self._by_key: dict[tuple[str, str], str] = {}

    async def add(self, draft: Draft, *, id: str, created_at: datetime) -> AddResult:
        if draft.dedupe_key is not None:
            existing = self._by_key.get((draft.recipient_id, draft.dedupe_key))
            if existing is not None:
                return AddResult(self._by_id[existing], created=False)
        if id in self._by_id:
            raise StoreError("duplicate_id")
        # Una copia profunda: lo guardado no cambia si quien llama toca su payload.
        stored = replace(
            Notification.from_draft(draft, id=id, created_at=created_at),
            payload=copy.deepcopy(dict(draft.payload)),
        )
        self._by_id[id] = stored
        if draft.dedupe_key is not None:
            self._by_key[(draft.recipient_id, draft.dedupe_key)] = id
        return AddResult(stored, created=True)

    async def list_feed(self, query: FeedQuery) -> FeedPage:
        limit = check_limit(query.limit)
        rows = [
            item
            for item in self._visible(query.recipient_id, query.scope)
            if (not query.unread_only or item.read_at is None)
            and (query.category is None or item.category == query.category)
        ]
        rows.sort(key=_position, reverse=True)
        if query.cursor is not None:
            edge = (to_ms(query.cursor[0]), query.cursor[1])
            rows = [item for item in rows if _position(item) < edge]
        return paginate(rows[: limit + 1], limit)

    async def count_unread(
        self, recipient_id: str, *, scope: str | None = None, cap: int | None = None
    ) -> int:
        cap = check_cap(cap)
        total = sum(1 for item in self._visible(recipient_id, scope) if item.read_at is None)
        return total if cap is None else min(total, cap)

    async def mark_read(self, id: str, recipient_id: str, read_at: datetime) -> bool:
        item = self._by_id.get(id)
        if item is None or item.recipient_id != recipient_id or item.read_at is not None:
            return False
        self._by_id[id] = replace(item, read_at=from_ms(to_ms(read_at)))
        return True

    async def mark_all_read(
        self,
        recipient_id: str,
        *,
        before: datetime,
        read_at: datetime,
        scope: str | None = None,
        category: str | None = None,
    ) -> int:
        cut, at = to_ms(before), from_ms(to_ms(read_at))
        changed = 0
        for item in self._visible(recipient_id, scope):
            if item.read_at is not None or to_ms(item.created_at) >= cut:
                continue
            if category is not None and item.category != category:
                continue
            self._by_id[item.id] = replace(item, read_at=at)
            changed += 1
        return changed

    def _visible(self, recipient_id: str, scope: str | None) -> list[Notification]:
        return [
            item
            for item in self._by_id.values()
            if item.recipient_id == recipient_id and (scope is None or item.scope in (None, scope))
        ]


def _position(item: Notification) -> tuple[int, str]:
    # `str` se compara por punto de código, que es el desempate que pide el spec.
    return (to_ms(item.created_at), item.id)


class MemoryQuietHoursStore:
    """Los horarios de no molestar, en el proceso."""

    def __init__(self) -> None:
        self._by_recipient: dict[str, QuietHours] = {}

    async def get_quiet_hours(self, recipient_id: str) -> QuietHours | None:
        return self._by_recipient.get(recipient_id)

    async def set_quiet_hours(self, recipient_id: str, quiet_hours: QuietHours) -> None:
        self._by_recipient[recipient_id] = quiet_hours


class MemoryPreferenceStore:
    """Las preferencias guardadas, en el proceso."""

    def __init__(self) -> None:
        self._cells: dict[tuple[str, str | None], dict[str, dict[str, bool]]] = {}

    async def get_overrides(self, recipient_id: str, *, scope: str | None = None) -> Overrides:
        mine = self._cells.get((recipient_id, scope), {})
        # Una copia: tocar lo devuelto no cambia lo guardado.
        return {category: dict(channels) for category, channels in mine.items()}

    async def save_changes(
        self, recipient_id: str, changes: Sequence[Change], *, scope: str | None = None
    ) -> None:
        if not changes:
            return
        mine = self._cells.setdefault((recipient_id, scope), {})
        for change in changes:
            mine.setdefault(change.category, {})[change.channel] = change.enabled
