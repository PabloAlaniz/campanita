"""Lo que los vectores no cubren: la forma de la API en Python."""

from __future__ import annotations

import pytest

import campanita
from campanita import (
    Catalog,
    Cell,
    EventRejected,
    InvalidCatalog,
    InvalidPolicy,
    PatchRejected,
    Policy,
    build_notification,
    parse_catalog,
    parse_policy,
    resolve_channels,
)

RAW = {
    "security": {"inapp": {"default": True, "locked": True}, "email": {"default": True, "locked": True}},
    "activity": {"inapp": {"default": True, "locked": True}, "email": {"default": False}},
}


def test_la_politica_es_inmutable() -> None:
    policy = parse_policy(RAW)
    assert policy["activity"]["email"] == Cell(default=False)
    with pytest.raises(TypeError):
        policy["activity"]["email"] = Cell(default=True)  # type: ignore[index]
    with pytest.raises(TypeError):
        policy["nueva"] = {}  # type: ignore[index]


def test_cambiar_el_dato_original_no_cambia_la_politica() -> None:
    raw = {"activity": {"inapp": {"default": True, "locked": True}, "email": {"default": False}}}
    policy = parse_policy(raw)
    raw["activity"]["email"]["default"] = True
    assert policy["activity"]["email"].default is False


def test_policy_no_se_construye_a_mano() -> None:
    with pytest.raises(TypeError, match="parse_policy"):
        Policy({"activity": {}})


def test_el_error_de_politica_dice_donde() -> None:
    with pytest.raises(InvalidPolicy, match=r"activity\.email: falta `default`"):
        parse_policy({"activity": {"inapp": {"default": True, "locked": True}, "email": {}}})


def test_el_rechazo_lleva_categoria_canal_y_motivo() -> None:
    with pytest.raises(PatchRejected, match=r"security\.email: la celda está bloqueada") as rejected:
        campanita.validate_patch(parse_policy(RAW), {"security": {"email": False}}, ["email"])
    assert (rejected.value.category, rejected.value.channel, rejected.value.reason) == (
        "security",
        "email",
        "locked",
    )


def test_los_argumentos_opcionales_son_sin_overrides_y_sin_canales() -> None:
    policy = parse_policy(RAW)
    assert resolve_channels(policy, "security") == frozenset()
    assert resolve_channels(policy, "security", available={"email"}) == frozenset({"email"})


def test_la_version_del_spec_coincide_con_el_repo() -> None:
    from pathlib import Path

    version = (Path(__file__).resolve().parents[2] / "spec" / "VERSION").read_text(encoding="utf-8").strip()
    assert version == campanita.SPEC_VERSION


CATALOG = {"comments.new": {"category": "activity", "title": "{author} comentó", "body": "{excerpt}"}}


def test_el_catalogo_es_inmutable_y_no_se_construye_a_mano() -> None:
    catalog = parse_catalog(CATALOG, parse_policy(RAW))
    assert catalog["comments.new"].category == "activity"
    with pytest.raises(TypeError):
        catalog["otro"] = catalog["comments.new"]  # type: ignore[index]
    with pytest.raises(TypeError, match="parse_catalog"):
        Catalog({}, {})


def test_el_error_de_catalogo_dice_donde() -> None:
    with pytest.raises(InvalidCatalog, match=r"comments\.new: `category` tiene que ser una categoría"):
        parse_catalog({"comments.new": {"category": "marketing", "title": "x"}}, parse_policy(RAW))
    with pytest.raises(InvalidCatalog, match=r"comments\.new: llave de cierre suelta en la posición 4"):
        parse_catalog({"comments.new": {"category": "activity", "title": "Hola}"}}, parse_policy(RAW))


def test_cambiar_el_evento_despues_no_cambia_el_aviso() -> None:
    catalog = parse_catalog(CATALOG, parse_policy(RAW))
    payload = {"author": "Ana", "excerpt": "Hola"}
    draft = build_notification(catalog, {"type": "comments.new", "recipient_id": "u1", "payload": payload})
    payload["author"] = "Otra"
    assert draft.payload == {"author": "Ana", "excerpt": "Hola"}
    assert (draft.title, draft.body, draft.category) == ("Ana comentó", "Hola", "activity")


def test_el_rechazo_del_evento_lleva_motivo_y_detalle() -> None:
    catalog = parse_catalog(CATALOG, parse_policy(RAW))
    with pytest.raises(EventRejected, match=r"falta una variable \(author\)") as rejected:
        build_notification(catalog, {"type": "comments.new", "recipient_id": "u1"})
    assert (rejected.value.reason, rejected.value.detail) == ("missing_variable", "author")
