"""El recorrido completo contra los vectores de `spec/vectors/recorrido/`."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from campanita import (
    Change,
    EventRejected,
    InvalidChannels,
    Notification,
    Notifier,
    StoreError,
    parse_catalog,
    parse_policy,
)
from campanita._inbox import from_ms
from campanita.conformance import notification_to_wire
from campanita.ports import DeliveryResult
from campanita.stores import MemoryPreferenceStore, MemoryStore

SPEC = Path(__file__).resolve().parents[2] / "spec" / "vectors"
VECTORS = sorted((SPEC / "recorrido").glob("*.json"))


class Scripted:
    """Un canal que contesta lo que el paso le indica y anota a quién le mandó."""

    def __init__(self, channel_id: str) -> None:
        self.id = channel_id
        self.answer = "sent"
        self.sent: list[str] = []

    async def send(self, notification: Notification) -> Any:
        self.sent.append(notification.id)
        if self.answer == "throws":
            raise RuntimeError("el proveedor no responde")
        if self.answer == "garbage":
            return {"outcome": "sent"}
        if self.answer == "unknown":
            return DeliveryResult("delivered")  # type: ignore[arg-type]
        return DeliveryResult(self.answer)  # type: ignore[arg-type]


def test_hay_recorridos() -> None:
    assert len(VECTORS) >= 7


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_recorrido(path: Path) -> None:
    vector = json.loads(path.read_text(encoding="utf-8"))

    async def run() -> None:
        policy = parse_policy(vector["policy"])
        channels = [Scripted(channel_id) for channel_id in vector["channels"]]
        preferences = MemoryPreferenceStore()
        notifier = Notifier(
            policy=policy,
            catalog=parse_catalog(vector["catalog"], policy),
            store=MemoryStore(),
            preferences=preferences,
            channels=channels,
        )
        for index, step in enumerate(vector["steps"]):
            where = f"{vector['id']} paso {index} ({step['note']})"
            if step["op"] == "save":
                changes = [Change(c["category"], c["channel"], c["enabled"]) for c in step["changes"]]
                await preferences.save_changes(step["recipient_id"], changes, scope=step["scope"])
                continue
            for channel in channels:
                channel.answer = step["outcomes"].get(channel.id, "sent")
                channel.sent.clear()
            got: dict[str, Any]
            try:
                result = await notifier.notify(
                    step["event"], id=step["id"], created_at=from_ms(step["at_ms"])
                )
            except EventRejected as rejected:
                got = {"error": {"reason": rejected.reason, "detail": rejected.detail}}
            except StoreError as rejected:
                got = {"error": {"reason": rejected.reason, "detail": None}}
            else:
                got = {
                    "created": result.created,
                    "notification": notification_to_wire(result.notification),
                    "attempted": list(result.deliveries),
                    "deliveries": {
                        channel: delivery.outcome for channel, delivery in result.deliveries.items()
                    },
                }
            assert got == step["expect"], where
            # Lo que el recorrido dice que intentó es lo que los canales recibieron.
            llamados = sorted(channel.id for channel in channels if channel.sent)
            assert llamados == sorted(got.get("attempted", [])), where
            assert all(channel.sent in ([], [step["id"]]) for channel in channels), where

    asyncio.run(run())


def test_canales_configurados() -> None:
    vector = json.loads((SPEC / "recorrido-canales" / "validacion.json").read_text(encoding="utf-8"))
    policy = parse_policy({"a": {"inapp": {"default": True, "locked": True}}})
    for case in vector["cases"]:

        def build(case: dict[str, Any] = case) -> Notifier:
            return Notifier(
                policy=policy,
                catalog=parse_catalog({}, policy),
                store=MemoryStore(),
                preferences=MemoryPreferenceStore(),
                channels=[Scripted(channel_id) for channel_id in case["channels"]],
            )

        if case["valid"]:
            build()
        else:
            with pytest.raises(InvalidChannels):
                build()


def test_sin_pasar_identificador_ni_fecha_los_pone_el_notifier() -> None:
    async def run() -> None:
        policy = parse_policy({"a": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}})
        email = Scripted("email")
        momentos = iter([datetime(2030, 1, 1, tzinfo=UTC), datetime(2030, 1, 2, tzinfo=UTC)])
        notifier = Notifier(
            policy=policy,
            catalog=parse_catalog({"nota": {"category": "a", "title": "Hola"}}, policy),
            store=MemoryStore(),
            preferences=MemoryPreferenceStore(),
            channels=[email],
            clock=lambda: next(momentos),
        )
        first = await notifier.notify({"type": "nota", "recipient_id": "ana"})
        second = await notifier.notify({"type": "nota", "recipient_id": "ana"})
        assert first.notification.id != second.notification.id
        assert len(first.notification.id) == 36
        assert second.notification.created_at == datetime(2030, 1, 2, tzinfo=UTC)
        assert first.deliveries["email"].outcome == "sent"

    asyncio.run(run())


def test_el_error_de_un_canal_que_lanza_queda_en_el_resultado() -> None:
    async def run() -> None:
        policy = parse_policy({"a": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}})
        email = Scripted("email")
        email.answer = "throws"
        notifier = Notifier(
            policy=policy,
            catalog=parse_catalog({"nota": {"category": "a", "title": "Hola"}}, policy),
            store=MemoryStore(),
            preferences=MemoryPreferenceStore(),
            channels=[email],
        )
        result = await notifier.notify({"type": "nota", "recipient_id": "ana"})
        assert result.deliveries["email"] == DeliveryResult(
            "failed", error="RuntimeError: el proveedor no responde"
        )

    asyncio.run(run())
