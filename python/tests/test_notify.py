"""El recorrido completo contra los vectores de `spec/vectors/recorrido/`."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from campanita import (
    Change,
    EventRejected,
    InvalidChannels,
    InvalidQuietHours,
    Notification,
    Notifier,
    QuietHours,
    StoreError,
    parse_catalog,
    parse_policy,
    parse_quiet_hours,
)
from campanita._inbox import from_ms
from campanita.conformance import notification_to_wire
from campanita.ports import DeliveryResult
from campanita.stores import MemoryPreferenceStore, MemoryQuietHoursStore, MemoryStore

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
    assert len(VECTORS) >= 8


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_recorrido(path: Path) -> None:
    vector = json.loads(path.read_text(encoding="utf-8"))

    async def run() -> None:
        policy = parse_policy(vector["policy"])
        channels = [Scripted(channel_id) for channel_id in vector["channels"]]
        preferences = MemoryPreferenceStore()
        quiet_hours = MemoryQuietHoursStore()
        # El minuto local lo dice cada paso: la base de zonas horarias no entra en los vectores.
        minute: list[int | None] = [None]
        # Lo que devuelve la función de disponibilidad en este paso; sin valor, la instalación no tiene.
        available: list[list[str] | None] = [None]
        known: dict[str, Notification] = {}
        notifier = Notifier(
            policy=policy,
            catalog=parse_catalog(vector["catalog"], policy),
            store=MemoryStore(),
            preferences=preferences,
            channels=channels,
            quiet_hours=quiet_hours,
            local_minute=lambda _zone, _moment: minute[0],
            available=lambda _who, _scope: vector["channels"] if available[0] is None else available[0],
        )
        for index, step in enumerate(vector["steps"]):
            where = f"{vector['id']} paso {index} ({step['note']})"
            if step["op"] == "save":
                changes = [Change(c["category"], c["channel"], c["enabled"]) for c in step["changes"]]
                await preferences.save_changes(step["recipient_id"], changes, scope=step["scope"])
                continue
            if step["op"] == "quiet":
                await quiet_hours.set_quiet_hours(step["recipient_id"], parse_quiet_hours(step["setting"]))
                continue
            minute[0] = step.get("local_minute")
            available[0] = step.get("available")
            for channel in channels:
                channel.answer = step["outcomes"].get(channel.id, "sent")
                channel.sent.clear()
            got: dict[str, Any]
            if step["op"] == "deliver":
                entregas = await notifier.deliver(known[step["id"]])
                got = {
                    "attempted": [c for c, d in entregas.items() if d.outcome != "silenced"],
                    "deliveries": {c: d.outcome for c, d in entregas.items()},
                }
                assert got == step["expect"], where
                assert sorted(channel.id for channel in channels if channel.sent) == sorted(
                    got["attempted"]
                ), where
                continue
            try:
                result = await notifier.notify(
                    step["event"],
                    id=step["id"],
                    created_at=from_ms(step["at_ms"]),
                    members=step.get("members"),
                )
            except EventRejected as rejected:
                got = {"error": {"reason": rejected.reason, "detail": rejected.detail}}
            except StoreError as rejected:
                got = {"error": {"reason": rejected.reason, "detail": None}}
            else:
                known[result.notification.id] = result.notification
                for copy in result.copies:
                    known[copy.notification.id] = copy.notification
                got = {
                    "created": result.created,
                    "notification": notification_to_wire(result.notification),
                    "copies": [
                        {"created": copy.created, "notification": notification_to_wire(copy.notification)}
                        for copy in result.copies
                    ],
                    "attempted": [
                        channel
                        for channel, delivery in result.deliveries.items()
                        if delivery.outcome != "silenced"
                    ],
                    "deliveries": {
                        channel: delivery.outcome for channel, delivery in result.deliveries.items()
                    },
                }
            assert got == step["expect"], where
            # Lo que el recorrido dice que intentó es lo que los canales recibieron.
            llamados = sorted(channel.id for channel in channels if channel.sent)
            assert llamados == sorted(step["expect"].get("attempted", [])), where
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


# --- no molestar: lo que depende de la plataforma ---------------------------


def test_el_minuto_local_sale_de_la_zona_horaria() -> None:
    from campanita import local_minute

    moment = datetime(2030, 1, 15, 2, 30, tzinfo=UTC)
    assert local_minute("UTC", moment) == 2 * 60 + 30
    # Sin horario de verano: tres horas detrás de UTC todo el año.
    assert local_minute("America/Argentina/Buenos_Aires", moment) == 23 * 60 + 30
    assert local_minute("Asia/Tokyo", moment) == 11 * 60 + 30
    assert local_minute("Asia/Kolkata", moment) == 8 * 60
    # Con horario de verano: la misma hora UTC cae distinto en enero y en julio.
    assert local_minute("Europe/Madrid", moment) == 3 * 60 + 30
    assert local_minute("Europe/Madrid", datetime(2030, 7, 15, 2, 30, tzinfo=UTC)) == 4 * 60 + 30


@pytest.mark.parametrize("zone", ["No/Existe", "x", "../etc/passwd", "America/", "a" * 64])
def test_una_zona_que_la_plataforma_no_conoce_no_tiene_minuto(zone: str) -> None:
    from campanita import local_minute

    assert local_minute(zone, datetime(2030, 1, 15, tzinfo=UTC)) is None


def _notifier_con_horario(zone: str, channel: Scripted) -> tuple[Notifier, MemoryQuietHoursStore]:
    policy = parse_policy(
        {"activity": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}}
    )
    quiet_hours = MemoryQuietHoursStore()
    notifier = Notifier(
        policy=policy,
        catalog=parse_catalog({"nota": {"category": "activity", "title": "Hola"}}, policy),
        store=MemoryStore(),
        preferences=MemoryPreferenceStore(),
        channels=[channel],
        quiet_hours=quiet_hours,
    )
    return notifier, quiet_hours


def test_no_molestar_usa_la_hora_local_del_momento_en_que_se_creo_el_aviso() -> None:
    async def run() -> None:
        channel = Scripted("email")
        notifier, quiet_hours = _notifier_con_horario("Asia/Tokyo", channel)
        setting = {"enabled": True, "start": "22:00", "end": "08:00", "timezone": "Asia/Tokyo"}
        await quiet_hours.set_quiet_hours("ana", parse_quiet_hours(setting))
        event = {"type": "nota", "recipient_id": "ana"}
        # 15:00 UTC son las 00:00 en Tokio: de noche. 03:00 UTC son las 12:00: de día.
        night = await notifier.notify(event, created_at=datetime(2030, 1, 15, 15, 0, tzinfo=UTC))
        day = await notifier.notify(event, created_at=datetime(2030, 1, 15, 3, 0, tzinfo=UTC))
        assert night.deliveries["email"].outcome == "silenced"
        assert day.deliveries["email"].outcome == "sent"
        assert channel.sent == [day.notification.id]

    asyncio.run(run())


def test_con_una_zona_desconocida_se_entrega() -> None:
    async def run() -> None:
        channel = Scripted("email")
        notifier, quiet_hours = _notifier_con_horario("No/Existe", channel)
        setting = {"enabled": True, "start": "00:00", "end": "23:59", "timezone": "No/Existe"}
        await quiet_hours.set_quiet_hours("ana", parse_quiet_hours(setting))
        result = await notifier.notify({"type": "nota", "recipient_id": "ana"})
        assert result.deliveries["email"].outcome == "sent"

    asyncio.run(run())


def test_sin_almacen_de_horarios_nadie_esta_en_silencio() -> None:
    async def run() -> None:
        policy = parse_policy(
            {"activity": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}}
        )
        channel = Scripted("email")
        notifier = Notifier(
            policy=policy,
            catalog=parse_catalog({"nota": {"category": "activity", "title": "Hola"}}, policy),
            store=MemoryStore(),
            preferences=MemoryPreferenceStore(),
            channels=[channel],
            local_minute=lambda _zone, _moment: 0,
        )
        result = await notifier.notify({"type": "nota", "recipient_id": "ana"})
        assert result.deliveries["email"].outcome == "sent"

    asyncio.run(run())


def test_un_canal_que_contesta_silenced_cuenta_como_falla() -> None:
    async def run() -> None:
        channel = Scripted("email")
        notifier, _ = _notifier_con_horario("UTC", channel)
        channel.answer = "silenced"
        result = await notifier.notify({"type": "nota", "recipient_id": "ana"})
        assert result.deliveries["email"].outcome == "failed"

    asyncio.run(run())


@pytest.mark.parametrize("minute", [-1, 1440, 1.5, "600", None, True])
def test_is_quiet_rechaza_un_minuto_fuera_del_dia(minute: Any) -> None:
    from campanita import is_quiet

    setting = parse_quiet_hours({"enabled": True, "start": "22:00", "end": "08:00", "timezone": "UTC"})
    with pytest.raises(ValueError, match="minuto"):
        is_quiet(setting, minute)


def test_un_aviso_de_grupo_sale_una_vez_y_deja_una_copia_a_cada_miembro() -> None:
    async def run() -> None:
        policy = parse_policy({"a": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}})
        catalog = parse_catalog(
            {
                "informe.listo": {"category": "a", "title": "Informe listo", "audience": "group"},
                "nota": {"category": "a", "title": "Hola"},
            },
            policy,
        )
        assert catalog["informe.listo"].audience == "group"
        assert catalog["nota"].audience == "person"
        email = Scripted("email")
        store = MemoryStore()
        notifier = Notifier(
            policy=policy, catalog=catalog, store=store, preferences=MemoryPreferenceStore(), channels=[email]
        )
        result = await notifier.notify(
            {"type": "informe.listo", "recipient_id": "equipo-1", "scope": "p-1"},
            id="g1",
            members=[{"recipient_id": "ana", "id": "c1"}, {"recipient_id": "beto", "id": "c2"}],
        )
        assert email.sent == ["g1"]
        assert [(c.created, c.notification.recipient_id, c.notification.scope) for c in result.copies] == [
            (True, "ana", "p-1"),
            (True, "beto", "p-1"),
        ]
        assert await store.count_unread("ana") == 1
        with pytest.raises(EventRejected) as rejected:
            await notifier.notify(
                {"type": "nota", "recipient_id": "ana"}, members=[{"recipient_id": "beto", "id": "c3"}]
            )
        assert (rejected.value.reason, rejected.value.detail) == ("invalid_field", "members")
        assert await store.count_unread("ana") == 1

    asyncio.run(run())


def _base() -> tuple[Any, Any]:
    policy = parse_policy({"a": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}})
    return policy, parse_catalog({"nota": {"category": "a", "title": "Hola"}}, policy)


def test_deliver_rechaza_una_categoria_que_la_politica_no_tiene() -> None:
    async def run() -> None:
        policy, catalog = _base()
        email = Scripted("email")
        notifier = Notifier(
            policy=policy,
            catalog=catalog,
            store=MemoryStore(),
            preferences=MemoryPreferenceStore(),
            channels=[email],
        )
        result = await notifier.notify({"type": "nota", "recipient_id": "ana"}, id="n1")
        email.sent.clear()
        with pytest.raises(EventRejected) as rejected:
            await notifier.deliver(replace(result.notification, category="otra"))
        assert (rejected.value.reason, rejected.value.detail) == ("invalid_field", "category")
        assert email.sent == []

    asyncio.run(run())


def test_deliver_mira_no_molestar_en_el_instante_de_la_llamada() -> None:
    async def run() -> None:
        policy, catalog = _base()
        quiet_hours = MemoryQuietHoursStore()
        await quiet_hours.set_quiet_hours(
            "ana", parse_quiet_hours({"enabled": True, "start": "22:00", "end": "08:00", "timezone": "UTC"})
        )
        now = [datetime(2030, 1, 1, 12, tzinfo=UTC)]
        moments: list[datetime] = []

        def minute_of(_zone: str, moment: datetime) -> int:
            moments.append(moment)
            return moment.hour * 60 + moment.minute

        notifier = Notifier(
            policy=policy,
            catalog=catalog,
            store=MemoryStore(),
            preferences=MemoryPreferenceStore(),
            channels=[Scripted("email")],
            quiet_hours=quiet_hours,
            clock=lambda: now[0],
            local_minute=minute_of,
        )
        result = await notifier.notify({"type": "nota", "recipient_id": "ana"}, id="n1")
        now[0] = datetime(2030, 1, 1, 23, tzinfo=UTC)
        deliveries = await notifier.deliver(result.notification)
        assert deliveries["email"].outcome == "silenced"
        assert [m.hour for m in moments] == [12, 23]

    asyncio.run(run())


def test_disponibilidad_asincrona_con_destinatario_y_ambito() -> None:
    async def run() -> None:
        policy, catalog = _base()
        calls: list[tuple[str, str | None]] = []

        async def available(who: str, scope: str | None) -> list[str]:
            calls.append((who, scope))
            return [] if who == "ana" else ["email"]

        notifier = Notifier(
            policy=policy,
            catalog=catalog,
            store=MemoryStore(),
            preferences=MemoryPreferenceStore(),
            channels=[Scripted("email")],
            available=available,
        )
        ana = await notifier.notify({"type": "nota", "recipient_id": "ana", "scope": "acme"}, id="n1")
        beto = await notifier.notify({"type": "nota", "recipient_id": "beto"}, id="n2")
        assert list(ana.deliveries) == []
        assert list(beto.deliveries) == ["email"]
        assert calls == [("ana", "acme"), ("beto", None)]

    asyncio.run(run())


class _HorarioRoto:
    """Un almacén que devuelve lo que tiene guardado, aunque esté roto (una fila editada a mano).

    El Notifier sólo lee horarios (`QuietHoursReader`): no hace falta `set_quiet_hours`.
    """

    async def get_quiet_hours(self, recipient_id: str) -> QuietHours | None:
        return QuietHours(enabled=True, start="25:00", end="08:00", timezone="UTC")


def test_un_horario_que_no_cumple_el_spec_no_se_puede_construir() -> None:
    with pytest.raises(InvalidQuietHours, match="`start`"):
        QuietHours(enabled=True, start="25:00", end="08:00", timezone="UTC")
    with pytest.raises(InvalidQuietHours, match="`timezone`"):
        QuietHours(enabled=True, start="22:00", end="08:00", timezone="")


def test_un_horario_guardado_roto_no_silencia_ni_rompe_el_aviso() -> None:
    async def run() -> None:
        policy = parse_policy(
            {"activity": {"inapp": {"default": True, "locked": True}, "email": {"default": True}}}
        )
        channel = Scripted("email")
        notifier = Notifier(
            policy=policy,
            catalog=parse_catalog({"nota": {"category": "activity", "title": "Hola"}}, policy),
            store=MemoryStore(),
            preferences=MemoryPreferenceStore(),
            channels=[channel],
            quiet_hours=_HorarioRoto(),
        )
        result = await notifier.notify({"type": "nota", "recipient_id": "ana"})
        assert result.created
        assert result.deliveries["email"].outcome == "sent"

    asyncio.run(run())
