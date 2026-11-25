"""El canal de push. Ningún test sale a la red."""

from __future__ import annotations

import asyncio
import functools
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from campanita import Notification, Notifier, parse_catalog, parse_policy
from campanita.channels import PushChannel, PushDevice, PushMessage, render_push, summarize_push
from campanita.ports import DeliveryResult
from campanita.stores import MemoryPreferenceStore, MemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

CASES: dict[str, list[dict[str, Any]]] = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "push.json").read_text(encoding="utf-8")
)


def notification(**changes: Any) -> Notification:
    base: dict[str, Any] = {
        "id": "n1",
        "recipient_id": "ana",
        "type": "nota",
        "category": "activity",
        "title": "Ana comentó",
        "body": "¿Va con IVA?",
        "action_url": "https://example.com/doc/7",
        "payload": {},
        "scope": None,
        "dedupe_key": None,
        "created_at": datetime(2030, 1, 1, tzinfo=UTC),
    }
    return Notification(**{**base, **changes})


def sync(test: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """Corre un test `async` sin depender de un plugin."""

    @functools.wraps(test)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


class Recorder:
    """Un transporte de mentira que anota lo que recibe."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, PushMessage]] = []

    async def send(self, target: Any, message: PushMessage) -> DeliveryResult:
        self.sent.append((target, message))
        return DeliveryResult("sent")


class Echo:
    """Contesta el resultado que trae el destino."""

    async def send(self, target: Any, message: PushMessage) -> DeliveryResult:
        return DeliveryResult(target)


# --- los casos compartidos ------------------------------------------------------


@pytest.mark.parametrize("case", CASES["mensaje"], ids=lambda case: case["note"])
def test_mensaje_por_omision(case: dict[str, Any]) -> None:
    message = render_push(notification(**case["notification"]))
    assert {
        "title": message.title,
        "body": message.body,
        "url": message.url,
        "thread": message.thread,
        "data": dict(message.data),
    } == case["message"]


@pytest.mark.parametrize("case", CASES["resumen"], ids=lambda case: case["note"])
def test_resumen(case: dict[str, Any]) -> None:
    assert summarize_push(case["outcomes"]) == case["outcome"]


@pytest.mark.parametrize("case", CASES["resumen"], ids=lambda case: case["note"])
@sync
async def test_el_canal_contesta_el_resumen(case: dict[str, Any]) -> None:
    devices = [PushDevice("otro" if outcome == "skipped" else "t", outcome) for outcome in case["outcomes"]]
    channel = PushChannel(transports={"t": Echo()}, resolve_devices=lambda _: devices)
    assert (await channel.send(notification())).outcome == case["outcome"]


# --- el canal -------------------------------------------------------------------


@sync
async def test_manda_a_cada_dispositivo_por_su_transporte() -> None:
    a, b = Recorder(), Recorder()
    seen: list[Notification] = []

    async def resolve(n: Notification) -> list[PushDevice]:
        seen.append(n)
        return [PushDevice("a", "uno"), PushDevice("b", "dos"), PushDevice("a", "tres")]

    channel = PushChannel(transports={"a": a, "b": b}, resolve_devices=resolve)
    aviso = notification(scope="equipo-1")
    assert await channel.send(aviso) == DeliveryResult("sent")
    assert seen == [aviso]
    assert [target for target, _ in a.sent] == ["uno", "tres"]
    assert [target for target, _ in b.sent] == ["dos"]
    assert a.sent[0][1] == render_push(aviso)
    assert channel.id == "push"


@pytest.mark.parametrize("none", [[], None, ()])
@sync
async def test_sin_dispositivos_no_hay_a_donde_entregar(none: Any) -> None:
    recorder = Recorder()
    channel = PushChannel(transports={"a": recorder}, resolve_devices=lambda _: none)
    assert (await channel.send(notification())).outcome == "skipped"
    assert recorder.sent == []


@sync
async def test_si_no_se_pueden_buscar_los_dispositivos_falla_sin_repetir_el_error() -> None:
    def resolve(_: Notification) -> list[PushDevice]:
        raise LookupError("token-secreto")

    channel = PushChannel(transports={}, resolve_devices=resolve)
    assert await channel.send(notification()) == DeliveryResult(
        "failed", error="no se pudieron buscar los dispositivos: LookupError"
    )


@sync
async def test_un_transporte_roto_no_frena_al_resto() -> None:
    class Roto:
        async def send(self, target: Any, message: PushMessage) -> DeliveryResult:
            raise ConnectionError("https://example.com/secreto")

    class Raro:
        async def send(self, target: Any, message: PushMessage) -> DeliveryResult:
            return DeliveryResult("silenced")

    bien = Recorder()
    results: list[tuple[Any, DeliveryResult]] = []
    channel = PushChannel(
        transports={"roto": Roto(), "raro": Raro(), "bien": bien},
        resolve_devices=lambda _: [PushDevice("roto", 1), PushDevice("raro", 2), PushDevice("bien", 3)],
        on_result=lambda device, result, _: results.append((device.target, result)),
    )
    assert (await channel.send(notification())).outcome == "sent"
    assert [target for target, _ in bien.sent] == [3]
    assert [(target, result.outcome) for target, result in results] == [
        (1, "failed"),
        (2, "failed"),
        (3, "sent"),
    ]
    assert "secreto" not in repr(results)


@sync
async def test_on_result_avisa_de_cada_dispositivo_y_que_lance_no_cambia_nada() -> None:
    seen: list[str] = []

    async def note(device: PushDevice, result: DeliveryResult, n: Notification) -> None:
        seen.append(f"{device.transport}:{result.outcome}:{n.id}")
        raise RuntimeError("la base no contesta")

    channel = PushChannel(
        transports={"t": Echo()},
        resolve_devices=lambda _: [PushDevice("t", "expired"), PushDevice("nadie", "x")],
        on_result=note,
    )
    result = await channel.send(notification())
    assert result == DeliveryResult("expired", error="push: no lo recibió ninguno de 2 dispositivos")
    assert sorted(seen) == ["nadie:skipped:n1", "t:expired:n1"]


@sync
async def test_usa_el_render_y_el_id_que_le_pasen() -> None:
    recorder = Recorder()
    channel = PushChannel(
        id="movil",
        transports={"a": recorder},
        resolve_devices=lambda _: [PushDevice("a", 1)],
        render=lambda n: PushMessage(title=n.title, thread=n.category),
    )
    await channel.send(notification())
    assert channel.id == "movil"
    assert recorder.sent[0][1].thread == "activity"


@sync
async def test_un_render_que_lanza_es_un_fallo() -> None:
    def render(_: Notification) -> PushMessage:
        raise ValueError("x")

    channel = PushChannel(transports={}, resolve_devices=lambda _: [PushDevice("a", 1)], render=render)
    assert await channel.send(notification()) == DeliveryResult(
        "failed", error="no se pudo armar el mensaje: ValueError"
    )


def test_exige_resolve_devices_y_transports() -> None:
    with pytest.raises(TypeError):
        PushChannel(transports={}, resolve_devices=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PushChannel(transports=None, resolve_devices=lambda _: [])  # type: ignore[arg-type]


@sync
async def test_es_un_canal_mas_del_recorrido() -> None:
    recorder = Recorder()
    policy = parse_policy(
        {"activity": {"inapp": {"default": True, "locked": True}, "push": {"default": True}}}
    )
    notifier = Notifier(
        policy=policy,
        catalog=parse_catalog(
            {"comments.new": {"category": "activity", "title": "{author} comentó"}}, policy
        ),
        store=MemoryStore(),
        preferences=MemoryPreferenceStore(),
        channels=[
            PushChannel(
                transports={"a": recorder},
                resolve_devices=lambda n: [PushDevice("a", n.recipient_id)],
            )
        ],
    )
    result = await notifier.notify(
        {"type": "comments.new", "recipient_id": "user-7", "payload": {"author": "Ana"}}
    )
    assert result.deliveries["push"].outcome == "sent"
    target, message = recorder.sent[0]
    assert target == "user-7"
    assert message.title == "Ana comentó"
    assert message.data == {"notification_id": result.notification.id, "type": "comments.new"}
