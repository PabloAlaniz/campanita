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
    InvalidPolicy,
    PatchRejected,
    effective_matrix,
    parse_policy,
    resolve_channels,
    validate_patch,
)

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
        for index, case in enumerate(vector["cases"]):
            label = case.get("note", "")
            out.append(
                pytest.param(vector.get("policy"), case, id=f"{vector['id']}[{index}] {label}".strip())
            )
    return out


def test_hay_vectores() -> None:
    assert VECTORS, "no se encontraron vectores: ¿se movió spec/vectors?"
    assert (SPEC / "VERSION").read_text(encoding="utf-8").strip() == "0"


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_el_vector_cumple_el_schema(path: Path) -> None:
    jsonschema.Draft7Validator(SCHEMA).validate(load(path))


@pytest.mark.parametrize(("_policy", "case"), cases("politica"))
def test_politica(_policy: None, case: dict[str, Any]) -> None:
    if case["valid"]:
        parse_policy(case["policy"])
    else:
        with pytest.raises(InvalidPolicy):
            parse_policy(case["policy"])


@pytest.mark.parametrize(("policy", "case"), cases("canales"))
def test_canales(policy: dict[str, Any], case: dict[str, Any]) -> None:
    got = resolve_channels(parse_policy(policy), case["category"], case["overrides"], case["available"])
    assert sorted(got) == case["expect"]


@pytest.mark.parametrize(("policy", "case"), cases("matriz"))
def test_matriz(policy: dict[str, Any], case: dict[str, Any]) -> None:
    got = effective_matrix(parse_policy(policy), case["overrides"], case["available"])
    plain = {
        category: {channel: asdict(cell) for channel, cell in cells.items()}
        for category, cells in got.items()
    }
    assert plain == case["expect"]


@pytest.mark.parametrize(("policy", "case"), cases("preferencias"))
def test_preferencias(policy: dict[str, Any], case: dict[str, Any]) -> None:
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
