"""Corre todos los vectores de `spec/vectors/` contra el paquete.

Es la prueba de conformidad: el paquete cumple el spec cuando esto pasa.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from campanita import (
    EventRejected,
    InvalidCatalog,
    InvalidPolicy,
    InvalidQuietHours,
    InvalidTemplate,
    PatchRejected,
    TemplateError,
    build_notification,
    effective_matrix,
    is_quiet,
    parse_catalog,
    parse_policy,
    parse_quiet_hours,
    render_template,
    resolve_channels,
    validate_patch,
)
from campanita.conformance import quiet_hours_to_wire

SPEC = Path(__file__).resolve().parents[2] / "spec"
VECTORS = sorted((SPEC / "vectors").rglob("*.json"))
SCHEMA = json.loads((SPEC / "schemas" / "vector.schema.json").read_text(encoding="utf-8"))


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cases(kind: str) -> list[Any]:
    """Un parámetro de pytest por caso, con un id legible."""
    out = []
    for path in VECTORS:
        vector = load(path)
        if vector["kind"] != kind:
            continue
        for index, case in enumerate(vector.get("cases", ())):
            label = case.get("note", "")
            out.append(pytest.param(vector, case, id=f"{vector['id']}[{index}] {label}".strip()))
    return out


def test_hay_vectores() -> None:
    assert VECTORS, "no se encontraron vectores: ¿se movió spec/vectors?"
    assert (SPEC / "VERSION").read_text(encoding="utf-8").strip() == "0"


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_el_vector_cumple_el_schema(path: Path) -> None:
    jsonschema.Draft7Validator(SCHEMA).validate(load(path))


@pytest.mark.parametrize(("_vector", "case"), cases("politica"))
def test_politica(_vector: dict[str, Any], case: dict[str, Any]) -> None:
    if case["valid"]:
        parse_policy(case["policy"])
    else:
        with pytest.raises(InvalidPolicy):
            parse_policy(case["policy"])


@pytest.mark.parametrize(("vector", "case"), cases("canales"))
def test_canales(vector: dict[str, Any], case: dict[str, Any]) -> None:
    policy = vector["policy"]
    got = resolve_channels(parse_policy(policy), case["category"], case["overrides"], case["available"])
    assert sorted(got) == case["expect"]


@pytest.mark.parametrize(("vector", "case"), cases("matriz"))
def test_matriz(vector: dict[str, Any], case: dict[str, Any]) -> None:
    policy = vector["policy"]
    got = effective_matrix(parse_policy(policy), case["overrides"], case["available"])
    plain = {
        category: {channel: asdict(cell) for channel, cell in cells.items()}
        for category, cells in got.items()
    }
    assert plain == case["expect"]


@pytest.mark.parametrize(("vector", "case"), cases("preferencias"))
def test_preferencias(vector: dict[str, Any], case: dict[str, Any]) -> None:
    policy = vector["policy"]
    expect = case["expect"]
    if expect["ok"]:
        got = validate_patch(parse_policy(policy), case["patch"], case["available"])
        assert [asdict(change) for change in got] == expect["changes"]
    else:
        with pytest.raises(PatchRejected) as rejected:
            validate_patch(parse_policy(policy), case["patch"], case["available"])
        error = rejected.value
        assert {"category": error.category, "channel": error.channel, "reason": error.reason} == expect[
            "error"
        ]


@pytest.mark.parametrize(("_vector", "case"), cases("plantilla"))
def test_plantilla(_vector: dict[str, Any], case: dict[str, Any]) -> None:
    expect = case["expect"]
    if expect["ok"]:
        assert render_template(case["template"], case["payload"]) == expect["text"]
        return
    with pytest.raises((InvalidTemplate, TemplateError)) as failed:
        render_template(case["template"], case["payload"])
    error = failed.value
    got = (
        {"reason": error.reason, "detail": error.name}
        if isinstance(error, TemplateError)
        else {"reason": "invalid_template", "detail": None}
    )
    assert got == expect["error"]


@pytest.mark.parametrize(("vector", "case"), cases("catalogo"))
def test_catalogo(vector: dict[str, Any], case: dict[str, Any]) -> None:
    policy = parse_policy(vector["policy"])
    if case["valid"]:
        parse_catalog(case["catalog"], policy)
    else:
        with pytest.raises(InvalidCatalog):
            parse_catalog(case["catalog"], policy)


@pytest.mark.parametrize(("vector", "case"), cases("aviso"))
def test_aviso(vector: dict[str, Any], case: dict[str, Any]) -> None:
    catalog = parse_catalog(vector["catalog"], parse_policy(vector["policy"]))
    expect = case["expect"]
    if expect["ok"]:
        assert asdict(build_notification(catalog, case["event"])) == expect["draft"]
    else:
        with pytest.raises(EventRejected) as rejected:
            build_notification(catalog, case["event"])
        assert {"reason": rejected.value.reason, "detail": rejected.value.detail} == expect["error"]


@pytest.mark.parametrize(("_vector", "case"), cases("silencio-validacion"))
def test_silencio_validacion(_vector: dict[str, Any], case: dict[str, Any]) -> None:
    if case["valid"]:
        parsed = parse_quiet_hours(case["setting"])
        assert quiet_hours_to_wire(parsed) == case["setting"]
    else:
        with pytest.raises(InvalidQuietHours):
            parse_quiet_hours(case["setting"])


@pytest.mark.parametrize(("_vector", "case"), cases("silencio"))
def test_silencio(_vector: dict[str, Any], case: dict[str, Any]) -> None:
    assert is_quiet(parse_quiet_hours(case["setting"]), case["minute"]) is case["expect"]


def test_todos_los_tipos_de_vector_tienen_quien_los_corra() -> None:
    # Los de `bandeja`, `preferencias-guardadas` y `silencio-guardado` los corre tests/test_stores.py
    # contra cada almacén; los de `recorrido`, tests/test_notify.py; los de
    # email, tests/test_email.py; y los de `http`, tests/test_http.py.
    corridos = {
        "politica",
        "canales",
        "matriz",
        "preferencias",
        "plantilla",
        "catalogo",
        "aviso",
        "bandeja",
        "preferencias-guardadas",
        "recorrido",
        "recorrido-canales",
        "email-mensaje",
        "email-direcciones",
        "silencio-validacion",
        "silencio",
        "silencio-guardado",
        "http",
    }
    assert {load(path)["kind"] for path in VECTORS} == corridos
