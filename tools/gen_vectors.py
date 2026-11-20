#!/usr/bin/env python3
"""Genera los vectores de conformidad de campanita.

Es una implementación INDEPENDIENTE del spec: no importa ninguno de los paquetes
(`python/`, `ts/`) y está escrita de otra forma a propósito. Si los ports y este
archivo coinciden, es porque los dos leyeron `spec/SPEC.md` igual, no porque uno
copió al otro.

Uso:
    python3 tools/gen_vectors.py           regenera spec/vectors/
    python3 tools/gen_vectors.py --check   falla si lo generado difiere del repo

Sólo usa la biblioteca estándar.
"""

from __future__ import annotations

import base64
import calendar
import itertools
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

SPEC = "0"
ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "spec" / "vectors"

INBOX = "inapp"
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")

# ---------------------------------------------------------------------------
# Implementación de referencia del generador.
#
# En vez de recorrer la política celda por celda, la aplana a una tabla
# {(categoría, canal): (default, locked)} y contesta todo con esa tabla.
# ---------------------------------------------------------------------------


def is_valid_policy(policy: object) -> bool:
    if type(policy) is not dict or not policy:
        return False
    for category, cells in policy.items():
        if not ID.fullmatch(category) or type(cells) is not dict:
            return False
        for channel, cell in cells.items():
            if not ID.fullmatch(channel) or type(cell) is not dict:
                return False
            if set(cell) - {"default", "locked", "urgent"} or "default" not in cell:
                return False
            if any(type(value) is not bool for value in cell.values()):
                return False
        inbox = cells.get(INBOX)
        if inbox is None or inbox["default"] is not True or inbox.get("locked") is not True:
            return False
    return True


def table(policy: dict) -> dict[tuple[str, str], tuple[bool, bool]]:
    assert is_valid_policy(policy), "el generador recibió una política inválida"
    return {
        (category, channel): (cell["default"], cell.get("locked", False))
        for category, cells in policy.items()
        for channel, cell in cells.items()
    }


def wanted(tabla, overrides, category, channel) -> bool:
    default, locked = tabla[(category, channel)]
    if locked:
        return default
    return overrides.get(category, {}).get(channel, default)


def channels_for(policy, category, overrides, available) -> list[str]:
    tabla = table(policy)
    return sorted(
        channel
        for (cat, channel) in tabla
        if cat == category
        and channel != INBOX
        and channel in available
        and wanted(tabla, overrides, cat, channel)
    )


def matrix_for(policy, overrides, available) -> dict:
    tabla = table(policy)
    out: dict = {}
    for (category, channel), (_, locked) in tabla.items():
        out.setdefault(category, {})[channel] = {
            "enabled": wanted(tabla, overrides, category, channel),
            "locked": locked or (channel != INBOX and channel not in available),
        }
    return out


def patch_result(policy, patch, available) -> dict:
    tabla = table(policy)
    changes = []
    for category in sorted(patch):
        if category not in policy:
            return {
                "ok": False,
                "error": {"category": category, "channel": None, "reason": "unknown_category"},
            }
        for channel in sorted(patch[category]):
            value = patch[category][channel]
            if (category, channel) not in tabla:
                reason = "unknown_channel"
            elif type(value) is not bool:
                reason = "invalid_value"
            elif tabla[(category, channel)][1]:
                reason = "locked"
            elif channel not in available:
                reason = "unavailable"
            else:
                changes.append({"category": category, "channel": channel, "enabled": value})
                continue
            return {"ok": False, "error": {"category": category, "channel": channel, "reason": reason}}
    return {"ok": True, "changes": changes}


# ---------------------------------------------------------------------------
# Plantillas, catálogo y armado del aviso.
#
# El generador separa la plantilla en piezas con una expresión regular; los
# paquetes la recorren carácter por carácter.
# ---------------------------------------------------------------------------

TYPE_ID = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*")
VAR = re.compile(r"[a-z][a-z0-9_]{0,63}")
PIEZA = re.compile(r"\{\{|\}\}|\{[^{}]*\}|[{}]|[^{}]+")
MAX_SAFE = 2**53 - 1
BLANCOS = " \t\n\r"
EVENT_FIELDS = ["recipient_id", "payload", "action_url", "scope", "dedupe_key"]


class Fallo(Exception):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def pieces(template: object) -> list[tuple[str, str]]:
    """[("text", …) | ("var", nombre)], o Fallo("invalid_template")."""
    if type(template) is not str:
        raise Fallo("invalid_template")
    out: list[tuple[str, str]] = []
    for piece in PIEZA.findall(template):
        if piece == "{{":
            out.append(("text", "{"))
        elif piece == "}}":
            out.append(("text", "}"))
        elif piece in "{}":
            raise Fallo("invalid_template")
        elif piece[0] == "{":
            if not VAR.fullmatch(piece[1:-1]):
                raise Fallo("invalid_template")
            out.append(("var", piece[1:-1]))
        else:
            out.append(("text", piece))
    return out


def show(value: object, name: str) -> str:
    if type(value) is str:
        return value
    if type(value) is int and abs(value) <= MAX_SAFE:
        return str(value)
    if type(value) is float and value.is_integer() and abs(value) <= MAX_SAFE:
        return str(int(value))
    raise Fallo("invalid_variable", name)


def fill(template: object, payload: dict) -> str:
    out = []
    for kind, value in pieces(template):
        if kind == "text":
            out.append(value)
        elif value not in payload:
            raise Fallo("missing_variable", value)
        else:
            out.append(show(payload[value], value))
    return "".join(out)


AUDIENCIAS = ("person", "group")


def is_valid_catalog(catalog: object, policy: dict) -> bool:
    if type(catalog) is not dict:
        return False
    for type_id, entry in catalog.items():
        if len(type_id) > 128 or not TYPE_ID.fullmatch(type_id) or type(entry) is not dict:
            return False
        if set(entry) - {"category", "title", "body", "audience"} or not {"category", "title"} <= set(entry):
            return False
        if "audience" in entry and entry["audience"] not in AUDIENCIAS:
            return False
        if type(entry["category"]) is not str or entry["category"] not in policy:
            return False
        try:
            if not pieces(entry["title"]):
                return False
            if "body" in entry:
                pieces(entry["body"])
        except Fallo:
            return False
    return True


def draft_result(catalog: dict, event: dict) -> dict:
    try:
        return {"ok": True, "draft": build_draft(catalog, event)}
    except Fallo as fallo:
        return {"ok": False, "error": {"reason": fallo.reason, "detail": fallo.detail}}


def build_draft(catalog: dict, event: dict) -> dict:
    type_id = event.get("type")
    if type(type_id) is not str or type_id not in catalog:
        raise Fallo("unknown_type", type_id if type(type_id) is str else None)
    entry = catalog[type_id]

    if type(event.get("recipient_id")) is not str or not event["recipient_id"]:
        raise Fallo("invalid_field", "recipient_id")
    if "payload" in event and type(event["payload"]) is not dict:
        raise Fallo("invalid_field", "payload")
    for name in ("action_url", "scope", "dedupe_key"):
        value = event.get(name)
        if value is not None and (type(value) is not str or not value):
            raise Fallo("invalid_field", name)
    extra = sorted(set(event) - {"type", *EVENT_FIELDS})
    if extra:
        raise Fallo("invalid_field", extra[0])

    payload = event.get("payload", {})
    title = fill(entry["title"], payload)
    if not title.strip(BLANCOS):
        raise Fallo("empty_title")
    return {
        "recipient_id": event["recipient_id"],
        "type": type_id,
        "category": entry["category"],
        "title": title,
        "body": fill(entry["body"], payload) if "body" in entry else None,
        "action_url": event.get("action_url"),
        "payload": payload,
        "scope": event.get("scope"),
        "dedupe_key": event.get("dedupe_key"),
    }


# ---------------------------------------------------------------------------
# Políticas de prueba
# ---------------------------------------------------------------------------

BANDEJA = {"default": True, "locked": True}

#: Una política de ejemplo con los casos típicos: una categoría que no se apaga,
#: dos que salen por email salvo que la persona diga que no, y una que sólo queda
#: en la bandeja salvo que la persona pida más.
EJEMPLO = {
    "security": {
        "email": {"default": True, "locked": True},
        "push": {"default": True, "locked": True},
        "inapp": BANDEJA,
    },
    "invitations": {
        "email": {"default": True},
        "push": {"default": False},
        "inapp": BANDEJA,
    },
    "activity": {
        "email": {"default": False},
        "push": {"default": False},
        "inapp": BANDEJA,
    },
    "digest": {
        "email": {"default": True},
        "push": {"default": False},
        "inapp": BANDEJA,
    },
}

#: Casos de borde: más canales, identificadores con guion, guion bajo y dígitos,
#: una categoría que sólo tiene bandeja, `locked: false` explícito y una celda
#: bloqueada en `false` (un canal que esa categoría no usa nunca).
BORDES = {
    "alertas-2": {
        "inapp": BANDEJA,
        "sms": {"default": True, "locked": True},
        "whatsapp": {"default": True, "locked": False},
        "email": {"default": False},
    },
    "marketing_semanal": {
        "inapp": BANDEJA,
        "email": {"default": True},
        "sms": {"default": False, "locked": True},
    },
    "sistema": {"inapp": BANDEJA},
}

#: `constructor` es un identificador válido y también una propiedad que todo
#: objeto de JavaScript hereda. Un port que busca claves sin distinguir las
#: propias de las heredadas se equivoca con esta política.
HEREDADOS = {
    "constructor": {
        "inapp": BANDEJA,
        "constructor": {"default": True},
        "email": {"default": False},
    },
    "activity": {"inapp": BANDEJA, "email": {"default": True}},
}

# ---------------------------------------------------------------------------
# Vectores
# ---------------------------------------------------------------------------


def politica_validacion() -> dict:
    largo = "a" * 64
    casos = [
        ("la política de ejemplo", EJEMPLO, True),
        ("la política de bordes", BORDES, True),
        ("una sola categoría que sólo tiene bandeja", {"sistema": {"inapp": BANDEJA}}, True),
        ("identificador de 64 caracteres", {largo: {"inapp": BANDEJA, largo: {"default": False}}}, True),
        ("categoría y canal llamados `constructor`", HEREDADOS, True),
        (
            "`locked: false` explícito",
            {"a": {"inapp": BANDEJA, "email": {"default": True, "locked": False}}},
            True,
        ),
        (
            "celda bloqueada en `false`",
            {"a": {"inapp": BANDEJA, "sms": {"default": False, "locked": True}}},
            True,
        ),
        (
            "celda urgente: se entrega aun en no molestar",
            {"a": {"inapp": BANDEJA, "sms": {"default": True, "locked": True, "urgent": True}}},
            True,
        ),
        (
            "`urgent: false` explícito",
            {"a": {"inapp": BANDEJA, "sms": {"default": False, "urgent": False}}},
            True,
        ),
        (
            "`urgent` en `inapp` se acepta y no cambia nada",
            {"a": {"inapp": {**BANDEJA, "urgent": True}}},
            True,
        ),
        (
            "`urgent` no es booleano: número",
            {"a": {"inapp": BANDEJA, "sms": {"default": True, "urgent": 1}}},
            False,
        ),
        (
            "`urgent` no es booleano: nulo",
            {"a": {"inapp": BANDEJA, "sms": {"default": True, "urgent": None}}},
            False,
        ),
        ("`urgent` sin `default`", {"a": {"inapp": BANDEJA, "sms": {"urgent": True}}}, False),
        ("sin categorías", {}, False),
        ("no es un objeto: lista", [], False),
        ("no es un objeto: nulo", None, False),
        ("no es un objeto: texto", "security", False),
        ("no es un objeto: número", 3, False),
        ("la categoría no es un objeto: lista", {"a": []}, False),
        ("la categoría no es un objeto: nulo", {"a": None}, False),
        ("la categoría no es un objeto: booleano", {"a": True}, False),
        ("categoría sin celdas", {"a": {}}, False),
        ("falta `inapp`", {"a": {"email": {"default": True}}}, False),
        ("`inapp` sin bloquear", {"a": {"inapp": {"default": True}}}, False),
        ("`inapp` con `locked: false`", {"a": {"inapp": {"default": True, "locked": False}}}, False),
        ("`inapp` apagado", {"a": {"inapp": {"default": False, "locked": True}}}, False),
        (
            "una categoría buena y otra sin `inapp`",
            {"a": {"inapp": BANDEJA}, "b": {"email": {"default": True}}},
            False,
        ),
        ("la celda no es un objeto: booleano", {"a": {"inapp": BANDEJA, "email": True}}, False),
        ("la celda no es un objeto: lista", {"a": {"inapp": BANDEJA, "email": []}}, False),
        ("la celda no es un objeto: nulo", {"a": {"inapp": BANDEJA, "email": None}}, False),
        ("celda sin `default`", {"a": {"inapp": BANDEJA, "email": {"locked": True}}}, False),
        ("celda vacía", {"a": {"inapp": BANDEJA, "email": {}}}, False),
        ("`default` no es booleano: número", {"a": {"inapp": BANDEJA, "email": {"default": 1}}}, False),
        ("`default` no es booleano: texto", {"a": {"inapp": BANDEJA, "email": {"default": "true"}}}, False),
        ("`default` no es booleano: nulo", {"a": {"inapp": BANDEJA, "email": {"default": None}}}, False),
        (
            "`locked` no es booleano: número",
            {"a": {"inapp": BANDEJA, "email": {"default": True, "locked": 0}}},
            False,
        ),
        (
            "`locked` no es booleano: nulo",
            {"a": {"inapp": BANDEJA, "email": {"default": True, "locked": None}}},
            False,
        ),
        (
            "clave de más en la celda",
            {"a": {"inapp": BANDEJA, "email": {"default": True, "enabled": True}}},
            False,
        ),
        ("clave de más en `inapp`", {"a": {"inapp": {"default": True, "locked": True, "x": True}}}, False),
        ("categoría con mayúscula", {"Security": {"inapp": BANDEJA}}, False),
        ("categoría que empieza con dígito", {"2fa": {"inapp": BANDEJA}}, False),
        ("categoría que empieza con guion", {"-a": {"inapp": BANDEJA}}, False),
        ("categoría con espacio", {"mi categoria": {"inapp": BANDEJA}}, False),
        ("categoría con punto", {"pagos.recibido": {"inapp": BANDEJA}}, False),
        ("categoría con letra no ASCII", {"señales": {"inapp": BANDEJA}}, False),
        ("categoría vacía", {"": {"inapp": BANDEJA}}, False),
        ("categoría de 65 caracteres", {largo + "a": {"inapp": BANDEJA}}, False),
        ("categoría con salto de línea al final", {"a\n": {"inapp": BANDEJA}}, False),
        ("canal con mayúscula", {"a": {"inapp": BANDEJA, "Email": {"default": True}}}, False),
        ("canal con salto de línea al final", {"a": {"inapp": BANDEJA, "email\n": {"default": True}}}, False),
        ("canal vacío", {"a": {"inapp": BANDEJA, "": {"default": True}}}, False),
        ("canal de 65 caracteres", {"a": {"inapp": BANDEJA, largo + "a": {"default": True}}}, False),
    ]
    cases = []
    for note, policy, valid in casos:
        # El valor esperado está escrito a mano; el validador del generador
        # tiene que coincidir. Si no, el error está acá y no en un port.
        assert is_valid_policy(policy) is valid, f"el generador no coincide con: {note}"
        cases.append({"note": note, "policy": policy, "valid": valid})
    return vector("politica/validacion", "politica", cases)


def subsets(items: list[str]) -> list[list[str]]:
    return [list(c) for n in range(len(items) + 1) for c in itertools.combinations(items, n)]


def canales_ejemplo() -> dict:
    """Recorrido completo: cada categoría, cada combinación de overrides sobre
    sus canales de entrega y cada subconjunto de canales disponibles."""
    estados = (None, True, False)
    cases = []
    for category, cells in EJEMPLO.items():
        delivery = sorted(c for c in cells if c != INBOX)
        for combo in itertools.product(estados, repeat=len(delivery)):
            mine = {
                channel: value for channel, value in zip(delivery, combo, strict=True) if value is not None
            }
            overrides = {category: mine} if mine else {}
            for available in subsets(["email", "push"]):
                cases.append(
                    {
                        "category": category,
                        "overrides": overrides,
                        "available": available,
                        "expect": channels_for(EJEMPLO, category, overrides, available),
                    }
                )
    return vector("canales/ejemplo", "canales", cases, EJEMPLO)


def canales_bordes() -> dict:
    todos = ["email", "sms", "whatsapp"]
    casos = [
        ("categoría que no está en la política", "desconocida", {}, todos),
        (
            "categoría que no está en la política, con overrides",
            "desconocida",
            {"desconocida": {"email": True}},
            todos,
        ),
        ("categoría que sólo tiene bandeja", "sistema", {}, todos),
        ("sin overrides, todo disponible", "alertas-2", {}, todos),
        ("sin overrides, nada disponible", "alertas-2", {}, []),
        ("un canal bloqueado no disponible no sale", "alertas-2", {}, ["whatsapp"]),
        (
            "override en contra de un canal bloqueado: se ignora",
            "alertas-2",
            {"alertas-2": {"sms": False}},
            todos,
        ),
        (
            "`locked: false` explícito respeta el override",
            "alertas-2",
            {"alertas-2": {"whatsapp": False}},
            todos,
        ),
        (
            "override que enciende un canal apagado por defecto",
            "alertas-2",
            {"alertas-2": {"email": True}},
            todos,
        ),
        (
            "el override enciende pero el canal no está disponible",
            "alertas-2",
            {"alertas-2": {"email": True}},
            ["sms"],
        ),
        (
            "celda bloqueada en `false`: el override no la enciende",
            "marketing_semanal",
            {"marketing_semanal": {"sms": True}},
            todos,
        ),
        (
            "override de `inapp` en `false`: se ignora y `inapp` no sale",
            "alertas-2",
            {"alertas-2": {"inapp": False}},
            todos,
        ),
        ("`inapp` entre los disponibles: no sale igual", "alertas-2", {}, [*todos, "inapp"]),
        (
            "overrides de otra categoría no afectan",
            "alertas-2",
            {"marketing_semanal": {"email": False}},
            todos,
        ),
        ("override sobre un canal que la categoría no tiene", "sistema", {"sistema": {"email": True}}, todos),
        ("override sobre una categoría que ya no existe", "alertas-2", {"vieja": {"email": True}}, todos),
        ("disponible un canal que la política no menciona", "alertas-2", {}, [*todos, "telegram"]),
        (
            "el resultado sale ordenado aunque los disponibles no",
            "alertas-2",
            {"alertas-2": {"email": True}},
            ["whatsapp", "sms", "email"],
        ),
    ]
    cases = [
        {
            "note": note,
            "category": category,
            "overrides": overrides,
            "available": available,
            "expect": channels_for(BORDES, category, overrides, available),
        }
        for note, category, overrides, available in casos
    ]
    return vector("canales/bordes", "canales", cases, BORDES)


def canales_heredados() -> dict:
    todos = ["constructor", "email"]
    casos = [
        ("categoría `constructor`, que sí está en la política", "constructor", {}, todos),
        (
            "su canal `constructor` apagado por override",
            "constructor",
            {"constructor": {"constructor": False}},
            todos,
        ),
        ("su canal `constructor` no disponible", "constructor", {}, ["email"]),
        ("override sobre el email de `constructor`", "constructor", {"constructor": {"email": True}}, todos),
        ("otra categoría sin overrides: no hereda nada", "activity", {}, todos),
        ("`valueOf` no es una categoría", "valueOf", {}, todos),
        ("`toString` no es una categoría", "toString", {}, todos),
        ("`__proto__` no es una categoría", "__proto__", {}, todos),
        ("`hasOwnProperty` no es una categoría", "hasOwnProperty", {}, todos),
        (
            "disponibles con nombres heredados que la política no tiene",
            "activity",
            {},
            ["email", "toString", "__proto__"],
        ),
    ]
    cases = [
        {
            "note": note,
            "category": category,
            "overrides": overrides,
            "available": available,
            "expect": channels_for(HEREDADOS, category, overrides, available),
        }
        for note, category, overrides, available in casos
    ]
    return vector("canales/nombres-heredados", "canales", cases, HEREDADOS)


def matriz_heredados() -> dict:
    casos = [
        ("sin overrides", {}, ["constructor", "email"]),
        (
            "`constructor` apagado por override",
            {"constructor": {"constructor": False}},
            ["constructor", "email"],
        ),
        ("`constructor` no disponible", {}, ["email"]),
    ]
    cases = [
        {
            "note": note,
            "overrides": overrides,
            "available": available,
            "expect": matrix_for(HEREDADOS, overrides, available),
        }
        for note, overrides, available in casos
    ]
    return vector("matriz/nombres-heredados", "matriz", cases, HEREDADOS)


def preferencias_heredados() -> dict:
    todos = ["constructor", "email"]
    casos = [
        (
            "cambiar el canal `constructor` de la categoría `constructor`",
            {"constructor": {"constructor": False}},
            todos,
        ),
        ("`toString` no es una categoría", {"toString": {}}, todos),
        ("`valueOf` no es una categoría", {"valueOf": {"email": True}}, todos),
        ("`__proto__` no es una categoría", {"__proto__": {}}, todos),
        ("`toString` no es un canal", {"activity": {"toString": True}}, todos),
        ("`constructor` no es un canal de `activity`", {"activity": {"constructor": True}}, todos),
        ("`__proto__` no es un canal", {"activity": {"__proto__": True}}, todos),
        ("`constructor` no disponible", {"constructor": {"constructor": True}}, ["email"]),
    ]
    cases = [
        {
            "note": note,
            "patch": patch,
            "available": available,
            "expect": patch_result(HEREDADOS, patch, available),
        }
        for note, patch, available in casos
    ]
    return vector("preferencias/nombres-heredados", "preferencias", cases, HEREDADOS)


def matriz_ejemplo() -> dict:
    juegos = [
        ("sin overrides", {}),
        ("la persona pide email para la actividad", {"activity": {"email": True}}),
        ("la persona apaga el email de las invitaciones", {"invitations": {"email": False}}),
        ("override en contra de seguridad: se ignora", {"security": {"email": False, "push": False}}),
        ("override de `inapp`: se ignora", {"activity": {"inapp": False}}),
        ("override igual al valor por defecto", {"digest": {"email": True, "push": False}}),
        (
            "todo lo editable invertido",
            {
                "invitations": {"email": False, "push": True},
                "activity": {"email": True, "push": True},
                "digest": {"email": False, "push": True},
            },
        ),
        (
            "overrides sobre categoría y canal que no existen",
            {"vieja": {"email": True}, "activity": {"sms": True}},
        ),
    ]
    cases = [
        {
            "note": f"{note}; disponibles: {', '.join(available) or 'ninguno'}",
            "overrides": overrides,
            "available": available,
            "expect": matrix_for(EJEMPLO, overrides, available),
        }
        for note, overrides in juegos
        for available in subsets(["email", "push"])
    ]
    return vector("matriz/ejemplo", "matriz", cases, EJEMPLO)


def matriz_bordes() -> dict:
    casos = [
        ("todo disponible", {}, ["email", "sms", "whatsapp"]),
        ("nada disponible: todo lo que no es bandeja queda bloqueado", {}, []),
        ("`inapp` no depende de los disponibles", {}, ["inapp"]),
        ("un canal no disponible conserva su `enabled`", {"alertas-2": {"email": True}}, ["sms"]),
        (
            "celda bloqueada en `false` con override en contra",
            {"marketing_semanal": {"sms": True}},
            ["email", "sms", "whatsapp"],
        ),
        ("disponible un canal que la política no menciona", {}, ["telegram"]),
    ]
    cases = [
        {
            "note": note,
            "overrides": overrides,
            "available": available,
            "expect": matrix_for(BORDES, overrides, available),
        }
        for note, overrides, available in casos
    ]
    return vector("matriz/bordes", "matriz", cases, BORDES)


def preferencias_validacion() -> dict:
    ambos = ["email", "push"]
    casos = [
        ("pedido vacío", {}, ambos),
        ("una categoría sin celdas", {"activity": {}}, ambos),
        ("un cambio", {"activity": {"email": True}}, ambos),
        ("apagar un canal encendido por defecto", {"invitations": {"email": False}}, ambos),
        ("un cambio igual al valor por defecto se guarda igual", {"digest": {"email": True}}, ambos),
        (
            "varios cambios: salen ordenados por categoría y canal",
            {
                "invitations": {"push": True, "email": False},
                "activity": {"push": True, "email": True},
            },
            ambos,
        ),
        ("categoría bloqueada", {"security": {"email": False}}, ambos),
        ("categoría bloqueada, aunque el valor sea el que ya tiene", {"security": {"push": True}}, ambos),
        ("`inapp` no se puede cambiar", {"activity": {"inapp": False}}, ambos),
        ("canal que no está disponible", {"activity": {"push": True}}, ["email"]),
        ("nada disponible", {"activity": {"email": True}}, []),
        ("categoría que no está en la política", {"marketing": {"email": True}}, ambos),
        ("categoría desconocida sin celdas", {"marketing": {}}, ambos),
        ("canal que la categoría no tiene", {"activity": {"sms": True}}, ambos),
        ("canal desconocido, aunque esté disponible", {"activity": {"sms": True}}, [*ambos, "sms"]),
        ("el valor no es un booleano: texto", {"activity": {"email": "true"}}, ambos),
        ("el valor no es un booleano: número", {"activity": {"email": 1}}, ambos),
        ("el valor no es un booleano: nulo", {"activity": {"email": None}}, ambos),
        ("valor inválido sobre celda bloqueada: gana `invalid_value`", {"security": {"email": "no"}}, ambos),
        (
            "valor inválido sobre canal desconocido: gana `unknown_channel`",
            {"activity": {"sms": "no"}},
            ambos,
        ),
        ("bloqueada y no disponible: gana `locked`", {"security": {"push": False}}, ["email"]),
        (
            "dos errores: se informa el de la primera categoría en orden",
            {
                "security": {"email": False},
                "activity": {"sms": True},
            },
            ambos,
        ),
        (
            "dos errores en una categoría: se informa el del primer canal en orden",
            {
                "activity": {"sms": True, "inapp": False},
            },
            ambos,
        ),
        (
            "un cambio válido antes del error no salva el pedido",
            {
                "activity": {"email": True},
                "security": {"email": False},
            },
            ambos,
        ),
        (
            "una categoría desconocida más adelante en el orden también corta",
            {
                "activity": {"email": True},
                "zzz": {},
            },
            ambos,
        ),
        # U+FF5E va antes que U+1F600 por punto de código, y después si se
        # ordena por unidades UTF-16 (0xD83D < 0xFF5E).
        ("el orden es por punto de código, no por unidades UTF-16", {"\U0001f600": {}, "～": {}}, ambos),
    ]
    cases = [
        {
            "note": note,
            "patch": patch,
            "available": available,
            "expect": patch_result(EJEMPLO, patch, available),
        }
        for note, patch, available in casos
    ]
    return vector("preferencias/validacion", "preferencias", cases, EJEMPLO)


#: Un catálogo de ejemplo sobre la política de ejemplo.
CATALOGO = {
    "invitations.received": {
        "category": "invitations",
        "title": "Te invitaron a {project}",
        "body": "{inviter} te invitó al proyecto {project}.",
    },
    "security.new-login": {"category": "security", "title": "Nuevo inicio de sesión desde {device}"},
    "activity.comment": {"category": "activity", "title": "{title}", "body": "{body}"},
    "digest.weekly": {"category": "digest", "title": "Tu resumen: {count} novedades"},
    "constructor": {"category": "activity", "title": "Un tipo llamado constructor"},
}


def plantilla_render() -> dict:
    casos = [
        ("sin variables", "Hola", {}),
        ("texto vacío", "", {}),
        ("una variable", "Hola {name}", {"name": "Ana"}),
        ("la misma variable dos veces", "{a} y {a}", {"a": "x"}),
        ("dos variables pegadas", "{a}{b}", {"a": "1", "b": "2"}),
        ("sólo una variable", "{a}", {"a": "todo"}),
        ("sobran claves en el payload", "{a}", {"a": "x", "b": "no se usa"}),
        ("llaves literales", "{{así}}", {}),
        ("llave literal y variable", "{{{a}}}", {"a": "x"}),
        ("llaves literales sueltas", "a }} b {{ c", {}),
        ("el valor no se vuelve a interpretar", "{a}", {"a": "{b}"}),
        ("un valor con llaves dobles queda igual", "{a}", {"a": "{{x}}"}),
        ("valor vacío", "[{a}]", {"a": ""}),
        ("texto fuera del plano básico", "🔔 {a} ñandú", {"a": "😀"}),
        ("nombre con guion bajo y dígitos", "{user_2}", {"user_2": "ok"}),
        ("nombre de 64 caracteres", "{" + "a" * 64 + "}", {"a" * 64: "ok"}),
        ("entero", "{n} avisos", {"n": 3}),
        ("cero", "{n}", {"n": 0}),
        ("entero negativo", "{n}", {"n": -12}),
        ("el mayor entero seguro", "{n}", {"n": MAX_SAFE}),
        ("el menor entero seguro", "{n}", {"n": -MAX_SAFE}),
        ("un decimal sin parte fraccionaria es un entero", "{n}", {"n": 3.0}),
        ("cero negativo", "{n}", {"n": -0.0}),
        ("entero fuera del rango seguro", "{n}", {"n": MAX_SAFE + 1}),
        ("decimal", "{n}", {"n": 1.5}),
        ("booleano", "{a}", {"a": True}),
        ("nulo", "{a}", {"a": None}),
        ("lista", "{a}", {"a": ["x"]}),
        ("objeto", "{a}", {"a": {"b": "x"}}),
        ("falta la variable", "Hola {name}", {}),
        ("faltan dos: se informa la primera que aparece", "{b} {a}", {}),
        ("la primera está mal y la segunda falta", "{a} {b}", {"a": None}),
        ("`constructor` no está en el payload", "{constructor}", {}),
        ("`constructor` sí está en el payload", "{constructor}", {"constructor": "x"}),
        ("`valueof` no está en el payload", "{valueof}", {"a": "x"}),
        ("llave que abre sin cerrar", "Hola {name", {"name": "x"}),
        ("llave que cierra sola", "Hola name}", {"name": "x"}),
        ("llave que abre sola al final", "Hola {", {}),
        ("nombre vacío", "{}", {}),
        ("nombre con espacios", "{ name }", {"name": "x"}),
        ("nombre con mayúscula", "{Name}", {"Name": "x"}),
        ("nombre con punto", "{a.b}", {"a.b": "x"}),
        ("nombre con guion", "{a-b}", {"a-b": "x"}),
        ("nombre que empieza con dígito", "{1a}", {"1a": "x"}),
        ("nombre que empieza con guion bajo", "{_a}", {"_a": "x"}),
        ("nombre de 65 caracteres", "{" + "a" * 65 + "}", {"a" * 65: "x"}),
        ("nombre con formato", "{n:>5}", {"n": 1}),
        ("llaves anidadas", "{a{b}}", {"a": "x", "b": "y"}),
        ("tres llaves que abren", "{{{", {}),
        ("la plantilla inválida gana aunque antes falte una variable", "{a} {", {}),
    ]
    cases = []
    for note, template, payload in casos:
        try:
            expect: dict = {"ok": True, "text": fill(template, payload)}
        except Fallo as fallo:
            expect = {"ok": False, "error": {"reason": fallo.reason, "detail": fallo.detail}}
        cases.append({"note": note, "template": template, "payload": payload, "expect": expect})
    return vector("plantilla/render", "plantilla", cases)


def catalogo_validacion() -> dict:
    ok = {"category": "activity", "title": "Hola"}
    casos = [
        ("el catálogo de ejemplo", CATALOGO, True),
        ("catálogo vacío", {}, True),
        ("tipo de un solo segmento", {"aviso": ok}, True),
        ("tipo con varios segmentos, guiones y dígitos", {"a-1.b_2.c": ok}, True),
        ("tipo de 128 caracteres", {"a" * 128: ok}, True),
        ("con cuerpo", {"a": {**ok, "body": "Chau {name}"}}, True),
        ("cuerpo vacío", {"a": {**ok, "body": ""}}, True),
        ("dos tipos de la misma categoría", {"a": ok, "b": ok}, True),
        ("no es un objeto: lista", [], False),
        ("no es un objeto: nulo", None, False),
        ("no es un objeto: texto", "a", False),
        ("la entrada no es un objeto: texto", {"a": "Hola"}, False),
        ("la entrada no es un objeto: nulo", {"a": None}, False),
        ("la entrada no es un objeto: lista", {"a": []}, False),
        ("falta `category`", {"a": {"title": "Hola"}}, False),
        ("falta `title`", {"a": {"category": "activity"}}, False),
        ("clave de más", {"a": {**ok, "email_template": "x"}}, False),
        ("la categoría no está en la política", {"a": {"category": "marketing", "title": "Hola"}}, False),
        ("la categoría no es texto", {"a": {"category": 1, "title": "Hola"}}, False),
        (
            "la categoría es `constructor`, que la política no tiene",
            {"a": {"category": "constructor", "title": "Hola"}},
            False,
        ),
        ("título vacío", {"a": {"category": "activity", "title": ""}}, False),
        ("el título no es texto", {"a": {"category": "activity", "title": 3}}, False),
        ("el título es nulo", {"a": {"category": "activity", "title": None}}, False),
        ("título con plantilla inválida", {"a": {"category": "activity", "title": "Hola {"}}, False),
        ("cuerpo con plantilla inválida", {"a": {**ok, "body": "{Name}"}}, False),
        ("el cuerpo no es texto", {"a": {**ok, "body": 3}}, False),
        ("el cuerpo es nulo", {"a": {**ok, "body": None}}, False),
        ("tipo vacío", {"": ok}, False),
        ("tipo con mayúscula", {"Aviso": ok}, False),
        ("tipo que empieza con punto", {".a": ok}, False),
        ("tipo que termina con punto", {"a.": ok}, False),
        ("tipo con dos puntos seguidos", {"a..b": ok}, False),
        ("segmento que empieza con dígito", {"a.1b": ok}, False),
        ("tipo con espacio", {"a b": ok}, False),
        ("tipo con salto de línea al final", {"a\n": ok}, False),
        ("tipo de 129 caracteres", {"a" * 129: ok}, False),
        ("uno bueno y uno malo", {"a": ok, "b": {"title": "Hola"}}, False),
        ("audiencia de persona", {"a": {**ok, "audience": "person"}}, True),
        ("audiencia de grupo", {"a": {**ok, "audience": "group"}}, True),
        ("audiencia desconocida", {"a": {**ok, "audience": "team"}}, False),
        ("audiencia con mayúscula", {"a": {**ok, "audience": "Group"}}, False),
        ("audiencia vacía", {"a": {**ok, "audience": ""}}, False),
        ("la audiencia es nula", {"a": {**ok, "audience": None}}, False),
        ("la audiencia no es texto", {"a": {**ok, "audience": ["group"]}}, False),
    ]
    cases = []
    for note, catalog, valid in casos:
        assert is_valid_catalog(catalog, EJEMPLO) is valid, f"el generador no coincide con: {note}"
        cases.append({"note": note, "catalog": catalog, "valid": valid})
    return vector("catalogo/validacion", "catalogo", cases, EJEMPLO)


def aviso_armado() -> dict:
    base = {
        "type": "invitations.received",
        "recipient_id": "u1",
        "payload": {"project": "Atlas", "inviter": "Ana"},
    }
    comment = {"type": "activity.comment", "recipient_id": "u1"}
    casos = [
        ("lo mínimo", base),
        (
            "con todos los campos",
            {**base, "action_url": "/projects/atlas", "scope": "org-7", "dedupe_key": "inv-42"},
        ),
        (
            "los opcionales en nulo son lo mismo que ausentes",
            {**base, "action_url": None, "scope": None, "dedupe_key": None},
        ),
        (
            "un tipo sin cuerpo deja el cuerpo en nulo",
            {"type": "security.new-login", "recipient_id": "u1", "payload": {"device": "Firefox"}},
        ),
        (
            "el payload se guarda entero, también lo que no se usa",
            {**base, "payload": {**base["payload"], "extra": [1, {"a": None}]}},
        ),
        ("variable entera", {"type": "digest.weekly", "recipient_id": "u1", "payload": {"count": 12}}),
        (
            "título y cuerpo que vienen ya redactados",
            {**comment, "payload": {"title": "Ana comentó", "body": "«Listo»"}},
        ),
        ("el cuerpo puede quedar vacío", {**comment, "payload": {"title": "Ana comentó", "body": ""}}),
        ("un tipo sin variables no necesita payload", {"type": "constructor", "recipient_id": "u1"}),
        ("la `action_url` no se interpreta", {**base, "action_url": "javascript:alert(1)"}),
        ("el destinatario es opaco", {**base, "recipient_id": " 7 "}),
        ("tipo que no está en el catálogo", {**base, "type": "invitations.accepted"}),
        ("`toString` no es un tipo", {**base, "type": "toString"}),
        ("`__proto__` no es un tipo", {**base, "type": "__proto__"}),
        ("falta el tipo", {"recipient_id": "u1"}),
        ("el tipo no es texto", {**base, "type": 7}),
        ("el tipo es nulo", {**base, "type": None}),
        ("falta el destinatario", {"type": "invitations.received", "payload": base["payload"]}),
        ("destinatario vacío", {**base, "recipient_id": ""}),
        ("el destinatario no es texto", {**base, "recipient_id": 7}),
        ("el destinatario es nulo", {**base, "recipient_id": None}),
        ("el payload es nulo", {**base, "payload": None}),
        ("el payload es una lista", {**base, "payload": []}),
        ("el payload es texto", {**base, "payload": "x"}),
        ("`action_url` vacía", {**base, "action_url": ""}),
        ("`action_url` no es texto", {**base, "action_url": 1}),
        ("`scope` vacío", {**base, "scope": ""}),
        ("`scope` no es texto", {**base, "scope": 7}),
        ("`dedupe_key` vacía", {**base, "dedupe_key": ""}),
        ("`dedupe_key` no es texto", {**base, "dedupe_key": 42}),
        ("campo desconocido", {**base, "dedup_key": "inv-42"}),
        ("dos campos desconocidos: se informa el primero en orden", {**base, "zeta": 1, "alfa": 2}),
        ("falta una variable del título", {**base, "payload": {"inviter": "Ana"}}),
        ("falta una variable del cuerpo", {**base, "payload": {"project": "Atlas"}}),
        (
            "una variable con un valor que no se puede mostrar",
            {**base, "payload": {"project": ["Atlas"], "inviter": "Ana"}},
        ),
        ("título vacío", {**comment, "payload": {"title": "", "body": "x"}}),
        ("título sólo con espacios", {**comment, "payload": {"title": " \t\n\r", "body": "x"}}),
        (
            "un espacio de no separación cuenta como texto",
            {**comment, "payload": {"title": " ", "body": "x"}},
        ),
        ("orden: el tipo antes que los campos", {"type": "nope", "recipient_id": ""}),
        ("orden: el destinatario antes que el payload", {**base, "recipient_id": "", "payload": None}),
        ("orden: los campos conocidos antes que los desconocidos", {**base, "scope": "", "alfa": 1}),
        ("orden: los campos antes que las variables", {**base, "payload": {}, "dedupe_key": ""}),
        ("orden: el título vacío antes que el cuerpo", {**comment, "payload": {"title": " "}}),
    ]
    cases = [{"note": note, "event": event, "expect": draft_result(CATALOGO, event)} for note, event in casos]
    assert is_valid_catalog(CATALOGO, EJEMPLO)
    data = vector("aviso/armado", "aviso", cases, EJEMPLO)
    return {**{k: v for k, v in data.items() if k != "cases"}, "catalog": CATALOGO, "cases": cases}


# ---------------------------------------------------------------------------
# La bandeja: un modelo en memoria hecho a fuerza bruta.
#
# Cada operación recorre la lista entera, filtra y ordena. No hay índices ni
# atajos: es la definición más directa de lo que dice el spec.
# ---------------------------------------------------------------------------


class Modelo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, draft: dict, new_id: str, at_ms: int) -> dict:
        if draft["dedupe_key"] is not None:
            for row in self.rows:
                if row["recipient_id"] == draft["recipient_id"] and row["dedupe_key"] == draft["dedupe_key"]:
                    return {"created": False, "notification": dict(row)}
        if any(row["id"] == new_id for row in self.rows):
            return {"error": "duplicate_id"}
        row = {"id": new_id, **draft, "created_at_ms": at_ms, "read_at_ms": None}
        self.rows.append(row)
        return {"created": True, "notification": dict(row)}

    def _visible(self, recipient_id: str, scope: str | None) -> list[dict]:
        return [
            row
            for row in self.rows
            if row["recipient_id"] == recipient_id and (scope is None or row["scope"] in (None, scope))
        ]

    def list(self, query: dict) -> dict:
        limit = query["limit"]
        if type(limit) is not int or limit < 1:
            return {"error": "invalid_limit"}
        rows = self._visible(query["recipient_id"], query.get("scope"))
        if query.get("unread_only"):
            rows = [row for row in rows if row["read_at_ms"] is None]
        if query.get("category") is not None:
            rows = [row for row in rows if row["category"] == query["category"]]
        rows.sort(key=lambda row: (row["created_at_ms"], row["id"]), reverse=True)
        cursor = query.get("cursor")
        if cursor is not None:
            edge = (cursor["created_at_ms"], cursor["id"])
            rows = [row for row in rows if (row["created_at_ms"], row["id"]) < edge]
        page, rest = rows[:limit], rows[limit:]
        last = page[-1] if page and rest else None
        return {
            "items": [dict(row) for row in page],
            "next_cursor": None
            if last is None
            else {"created_at_ms": last["created_at_ms"], "id": last["id"]},
        }

    def count_unread(self, recipient_id: str, scope: str | None, cap: object) -> object:
        if cap is not None and (type(cap) is not int or cap < 1):
            return {"error": "invalid_cap"}
        total = sum(1 for row in self._visible(recipient_id, scope) if row["read_at_ms"] is None)
        return total if cap is None else min(total, cap)

    def mark_read(self, notification_id: str, recipient_id: str, at_ms: int) -> bool:
        for row in self.rows:
            if (
                row["id"] == notification_id
                and row["recipient_id"] == recipient_id
                and row["read_at_ms"] is None
            ):
                row["read_at_ms"] = at_ms
                return True
        return False

    def mark_all_read(self, recipient_id, before_ms, at_ms, scope, category) -> int:
        changed = 0
        for row in self._visible(recipient_id, scope):
            pendiente = row["read_at_ms"] is None and row["created_at_ms"] < before_ms
            if pendiente and category in (None, row["category"]):
                row["read_at_ms"] = at_ms
                changed += 1
        return changed


def aviso(recipient="ana", category="activity", title="Aviso", **extra) -> dict:
    """Un aviso armado, con los opcionales en nulo."""
    base = {
        "recipient_id": recipient,
        "type": "nota",
        "category": category,
        "title": title,
        "body": None,
        "action_url": None,
        "payload": {},
        "scope": None,
        "dedupe_key": None,
    }
    assert set(extra) <= set(base), extra
    return {**base, **extra}


class Guion:
    """Arma los pasos de un vector contra el modelo y anota lo que devuelve."""

    def __init__(self) -> None:
        self.modelo, self.steps = Modelo(), []

    def _step(self, note: str, op: str, args: dict, expect: object) -> object:
        self.steps.append({"note": note, "op": op, **args, "expect": expect})
        return expect

    def add(self, note, new_id, at_ms, **draft):
        d = aviso(**draft)
        return self._step(
            note, "add", {"draft": d, "id": new_id, "at_ms": at_ms}, self.modelo.add(d, new_id, at_ms)
        )

    def list(self, note, **query):
        query.setdefault("recipient_id", "ana")
        return self._step(note, "list", {"query": query}, self.modelo.list(query))

    def count(self, note, recipient="ana", scope=None, cap=None):
        args = {"recipient_id": recipient, "scope": scope, "cap": cap}
        return self._step(note, "count_unread", args, self.modelo.count_unread(recipient, scope, cap))

    def read(self, note, new_id, at_ms, recipient="ana"):
        args = {"id": new_id, "recipient_id": recipient, "at_ms": at_ms}
        return self._step(note, "mark_read", args, self.modelo.mark_read(new_id, recipient, at_ms))

    def read_all(self, note, before_ms, at_ms, recipient="ana", scope=None, category=None):
        args = {
            "recipient_id": recipient,
            "before_ms": before_ms,
            "at_ms": at_ms,
            "scope": scope,
            "category": category,
        }
        return self._step(
            note,
            "mark_all_read",
            args,
            self.modelo.mark_all_read(recipient, before_ms, at_ms, scope, category),
        )


def ids(result: dict) -> list[str]:
    return [row["id"] for row in result["items"]]


def bandeja_basico() -> dict:
    g = Guion()
    assert g.list("una bandeja vacía", limit=10) == {"items": [], "next_cursor": None}
    assert g.count("sin avisos no hay nada sin leer") == 0
    g.add("el primer aviso", "n1", 1000, title="Uno")
    g.add(
        "el segundo",
        "n2",
        2000,
        title="Dos",
        body="Con cuerpo",
        action_url="/dos",
        payload={"k": [1, {"a": None}]},
    )
    g.add("el tercero", "n3", 3000, title="Tres", category="security")
    assert ids(g.list("la bandeja sale del más nuevo al más viejo", limit=10)) == ["n3", "n2", "n1"]
    assert g.count("tres sin leer") == 3
    assert g.read("marcar uno como leído", "n2", 5000) is True
    assert g.read("marcarlo de nuevo no cambia nada", "n2", 9000) is False
    r = g.list("la hora de lectura es la primera", limit=10)
    assert [row["read_at_ms"] for row in r["items"]] == [None, 5000, None]
    assert g.count("quedan dos sin leer") == 2
    assert ids(g.list("sólo los no leídos", limit=10, unread_only=True)) == ["n3", "n1"]
    assert ids(g.list("por categoría", limit=10, category="security")) == ["n3"]
    assert ids(g.list("una categoría sin avisos", limit=10, category="digest")) == []
    assert ids(g.list("categoría y no leídos a la vez", limit=10, category="activity", unread_only=True)) == [
        "n1"
    ]
    assert g.read("un aviso que no existe", "nope", 6000) is False
    assert g.read("el aviso de otra persona", "n1", 6000, recipient="beto") is False
    assert g.count("el intento ajeno no cambió nada") == 2
    return vector("bandeja/basico", "bandeja", g.steps)


def bandeja_duplicados() -> dict:
    g = Guion()
    first = g.add("un aviso con clave", "n1", 1000, title="Original", dedupe_key="k1")
    again = g.add(
        "la misma clave: no se crea otro y vuelve el original",
        "n2",
        2000,
        title="Otro texto",
        dedupe_key="k1",
    )
    assert first["created"] and not again["created"] and again["notification"] == first["notification"]
    assert ids(g.list("sigue habiendo uno solo", limit=10)) == ["n1"]
    assert g.add(
        "la misma clave para otra persona sí se crea", "n3", 3000, recipient="beto", dedupe_key="k1"
    )["created"]
    assert g.add("otra clave sí se crea", "n4", 4000, dedupe_key="k2")["created"]
    assert g.add("sin clave nunca se considera repetido", "n5", 5000, title="Igual")["created"]
    assert g.add("sin clave, idéntico al anterior: se crea igual", "n6", 6000, title="Igual")["created"]
    assert g.read("leer el original", "n1", 7000) is True
    late = g.add("repetir una clave ya leída devuelve el aviso leído", "n7", 8000, dedupe_key="k1")
    assert not late["created"] and late["notification"]["read_at_ms"] == 7000
    scoped = g.add(
        "la clave es por persona: otro ámbito no la separa", "n8", 9000, dedupe_key="k1", scope="org-1"
    )
    assert not scoped["created"] and scoped["notification"]["scope"] is None
    assert g.add("un identificador que ya existe se rechaza", "n4", 10000) == {"error": "duplicate_id"}
    dup = g.add("la clave repetida se revisa antes que el identificador", "n4", 11000, dedupe_key="k2")
    assert dup["created"] is False and dup["notification"]["id"] == "n4"
    assert ids(g.list("lo rechazado no dejó nada", limit=10)) == ["n6", "n5", "n4", "n1"]
    assert g.count("y el contador coincide") == 3
    return vector("bandeja/duplicados", "bandeja", g.steps)


def bandeja_paginado() -> dict:
    g = Guion()
    for index in range(1, 8):
        g.add(f"aviso {index}", f"n{index}", index * 1000)
    p1 = g.list("primera página", limit=3)
    assert ids(p1) == ["n7", "n6", "n5"] and p1["next_cursor"] == {"created_at_ms": 5000, "id": "n5"}
    g.add("llega un aviso entre una página y la siguiente", "n8", 8000)
    p2 = g.list("la segunda página no repite ni saltea", limit=3, cursor=p1["next_cursor"])
    assert ids(p2) == ["n4", "n3", "n2"]
    p3 = g.list("la última página, incompleta, no tiene cursor", limit=3, cursor=p2["next_cursor"])
    assert ids(p3) == ["n1"] and p3["next_cursor"] is None
    exact = g.list(
        "una página que termina justo en el último tampoco tiene cursor",
        limit=4,
        cursor={"created_at_ms": 5000, "id": "n5"},
    )
    assert ids(exact) == ["n4", "n3", "n2", "n1"] and exact["next_cursor"] is None
    assert ids(g.list("pedir de nuevo desde el principio trae lo nuevo", limit=3)) == ["n8", "n7", "n6"]
    assert g.list("límite de uno", limit=1)["next_cursor"] == {"created_at_ms": 8000, "id": "n8"}
    assert ids(g.list("un cursor más viejo que todo", limit=3, cursor={"created_at_ms": 1, "id": "a"})) == []
    assert ids(
        g.list(
            "un cursor que no corresponde a ningún aviso",
            limit=2,
            cursor={"created_at_ms": 4500, "id": "zzz"},
        )
    ) == ["n4", "n3"]
    assert ids(
        g.list(
            "un cursor más nuevo que todo trae desde el principio",
            limit=2,
            cursor={"created_at_ms": 99000, "id": "a"},
        )
    ) == ["n8", "n7"]
    g.read("leer uno del medio", "n6", 20000)
    u1 = g.list("los filtros se aplican antes de paginar", limit=2, unread_only=True)
    assert ids(u1) == ["n8", "n7"]
    assert ids(
        g.list("y el cursor sigue dentro del filtro", limit=2, unread_only=True, cursor=u1["next_cursor"])
    ) == ["n5", "n4"]
    assert g.list("límite cero", limit=0) == {"error": "invalid_limit"}
    assert g.list("límite negativo", limit=-1) == {"error": "invalid_limit"}
    return vector("bandeja/paginado", "bandeja", g.steps)


def bandeja_empates() -> dict:
    g = Guion()
    # Misma fecha: desempata el identificador, por punto de código. "B" (0x42)
    # va antes que "a" (0x61), y U+FF5E antes que U+1F600 aunque en UTF-16 sea
    # al revés.
    for new_id in ["a", "B", "c", "～", "\U0001f600", "10", "9"]:
        g.add(f"aviso {new_id!r} en el mismo milisegundo", new_id, 1000)
    g.add("uno más nuevo", "z", 2000)
    todo = g.list("con la misma fecha, ordena el identificador de mayor a menor", limit=20)
    assert ids(todo) == ["z", "\U0001f600", "～", "c", "a", "B", "9", "10"], ids(todo)
    p1 = g.list("paginar dentro de un empate", limit=3)
    p2 = g.list("sigue donde quedó", limit=3, cursor=p1["next_cursor"])
    p3 = g.list("y termina", limit=3, cursor=p2["next_cursor"])
    assert ids(p1) + ids(p2) + ids(p3) == ids(todo) and p3["next_cursor"] is None
    g.add("llega otro con la misma fecha y un identificador intermedio", "b", 1000)
    assert ids(g.list("queda en su lugar del orden", limit=20)) == [
        "z",
        "\U0001f600",
        "～",
        "c",
        "b",
        "a",
        "B",
        "9",
        "10",
    ]
    return vector("bandeja/empates", "bandeja", g.steps)


def bandeja_ambitos() -> dict:
    g = Guion()
    g.add("un aviso de cuenta, sin ámbito", "n1", 1000, title="Cuenta")
    g.add("uno del ámbito x", "n2", 2000, scope="x")
    g.add("otro del ámbito x", "n3", 3000, scope="x", category="security")
    g.add("uno del ámbito y", "n4", 4000, scope="y")
    g.add("uno de otra persona en x", "n5", 5000, scope="x", recipient="beto")
    assert ids(g.list("sin filtro de ámbito se ve todo lo propio", limit=10)) == ["n4", "n3", "n2", "n1"]
    assert ids(g.list("con ámbito x: lo de x y lo que no tiene ámbito", limit=10, scope="x")) == [
        "n3",
        "n2",
        "n1",
    ]
    assert ids(g.list("con ámbito y", limit=10, scope="y")) == ["n4", "n1"]
    assert ids(g.list("un ámbito sin avisos igual muestra los de cuenta", limit=10, scope="z")) == ["n1"]
    assert g.count("sin leer en total") == 4
    assert g.count("sin leer en x", scope="x") == 3
    assert g.count("sin leer en z", scope="z") == 1
    assert ids(g.list("ámbito y categoría", limit=10, scope="x", category="security")) == ["n3"]
    p = g.list("ámbito y paginado", limit=2, scope="x")
    assert ids(g.list("el cursor respeta el ámbito", limit=2, scope="x", cursor=p["next_cursor"])) == ["n1"]
    assert g.read_all("marcar todo en x marca también los de cuenta", 99000, 10000, scope="x") == 3
    assert ids(g.list("lo de y sigue sin leer", limit=10, unread_only=True)) == ["n4"]
    assert g.count("lo de la otra persona no se tocó", recipient="beto", scope="x") == 1
    return vector("bandeja/ambitos", "bandeja", g.steps)


def bandeja_marcar_todo() -> dict:
    g = Guion()
    g.add("aviso 1", "n1", 1000)
    g.add("aviso 2", "n2", 2000, category="security")
    g.add("aviso 3", "n3", 3000)
    g.add("aviso 4", "n4", 4000)
    g.add("de otra persona", "n5", 2500, recipient="beto")
    g.read("uno ya leído de antes", "n1", 1500)
    assert g.read_all("el corte es estricto: lo creado justo en ese instante no se marca", 3000, 9000) == 1
    r = g.list("se marcó sólo lo anterior al corte, y lo ya leído conserva su hora", limit=10)
    assert [(row["id"], row["read_at_ms"]) for row in r["items"]] == [
        ("n4", None),
        ("n3", None),
        ("n2", 9000),
        ("n1", 1500),
    ]
    assert g.read_all("repetirlo no marca nada", 3000, 9500) == 0
    assert g.read_all("por categoría, sin avisos que coincidan", 99000, 9600, category="security") == 0
    assert g.read_all("por categoría", 99000, 9700, category="activity") == 2
    assert g.count("no queda nada sin leer") == 0
    assert g.count("lo de la otra persona sigue sin leer", recipient="beto") == 1
    assert g.read_all("una persona sin avisos", 99000, 9800, recipient="carla") == 0
    assert g.read_all("un corte anterior a todo", 1, 9900, recipient="beto") == 0
    return vector("bandeja/marcar-todo", "bandeja", g.steps)


def bandeja_contador() -> dict:
    g = Guion()
    for index in range(1, 6):
        g.add(f"aviso {index}", f"n{index}", index * 1000)
    assert g.count("sin tope") == 5
    assert g.count("tope mayor que el total", cap=99) == 5
    assert g.count("tope igual al total", cap=5) == 5
    assert g.count("tope menor que el total", cap=3) == 3
    assert g.count("tope de uno: alcanza para saber si hay algo", cap=1) == 1
    g.read("leer uno", "n3", 9000)
    assert g.count("los leídos no cuentan", cap=99) == 4
    assert g.count("otra persona", recipient="beto", cap=3) == 0
    assert g.count("tope cero", cap=0) == {"error": "invalid_cap"}
    assert g.count("tope negativo", cap=-2) == {"error": "invalid_cap"}
    return vector("bandeja/contador", "bandeja", g.steps)


def bandeja_personas() -> dict:
    g = Guion()
    g.add("de ana", "n1", 1000)
    g.add("de beto", "n2", 2000, recipient="beto")
    g.add("de una persona llamada constructor", "n3", 3000, recipient="constructor")
    g.add(
        "otra de ana, de un tipo y un ámbito llamados constructor",
        "n4",
        4000,
        scope="constructor",
        category="constructor",
    )
    assert ids(g.list("ana ve lo suyo", limit=10)) == ["n4", "n1"]
    assert ids(g.list("beto ve lo suyo", limit=10, recipient_id="beto")) == ["n2"]
    assert ids(g.list("constructor ve lo suyo", limit=10, recipient_id="constructor")) == ["n3"]
    assert ids(g.list("una persona sin avisos", limit=10, recipient_id="toString")) == []
    assert ids(g.list("ámbito constructor", limit=10, scope="constructor")) == ["n4", "n1"]
    assert ids(g.list("categoría constructor", limit=10, category="constructor")) == ["n4"]
    assert ids(g.list("categoría toString", limit=10, category="toString")) == []
    assert g.read("beto no puede leer lo de ana", "n1", 5000, recipient="beto") is False
    assert g.read_all("ni marcarlo todo", 99000, 6000, recipient="beto") == 1
    assert g.count("ana sigue con dos sin leer") == 2
    assert (
        g.read("el identificador es de una persona: mayúsculas distintas no coinciden", "N1", 7000) is False
    )
    assert ids(g.list("ni el destinatario", limit=10, recipient_id="Ana")) == []
    return vector("bandeja/personas", "bandeja", g.steps)


# ---------------------------------------------------------------------------
# Las preferencias guardadas: un diccionario plano por (persona, ámbito, celda).
# ---------------------------------------------------------------------------


class Ajustes:
    def __init__(self) -> None:
        self.cells: dict[tuple[str, str | None, str, str], bool] = {}
        self.steps: list[dict] = []

    def save(self, note: str, changes: list[tuple[str, str, bool]], recipient="ana", scope=None) -> None:
        for category, channel, enabled in changes:
            self.cells[(recipient, scope, category, channel)] = enabled
        wire = [{"category": c, "channel": ch, "enabled": e} for c, ch, e in changes]
        self.steps.append(
            {
                "note": note,
                "op": "save",
                "recipient_id": recipient,
                "scope": scope,
                "changes": wire,
                "expect": None,
            }
        )

    def get(self, note: str, recipient="ana", scope=None) -> dict:
        out: dict = {}
        for (who, where, category, channel), enabled in self.cells.items():
            if who == recipient and where == scope:
                out.setdefault(category, {})[channel] = enabled
        self.steps.append(
            {"note": note, "op": "get", "recipient_id": recipient, "scope": scope, "expect": out}
        )
        return out


def guardadas_basico() -> dict:
    g = Ajustes()
    assert g.get("quien nunca cambió nada no tiene nada guardado") == {}
    g.save("un cambio", [("activity", "email", True)])
    assert g.get("se lee lo que se guardó") == {"activity": {"email": True}}
    g.save("otro canal de la misma categoría", [("activity", "push", False)])
    g.save("otra categoría", [("digest", "email", False)])
    assert g.get("los cambios se acumulan") == {
        "activity": {"email": True, "push": False},
        "digest": {"email": False},
    }
    g.save("volver a cambiar una celda la pisa", [("activity", "email", False)])
    assert g.get("queda el último valor")["activity"] == {"email": False, "push": False}
    g.save("varios cambios en un solo pedido", [("digest", "push", True), ("invitations", "email", False)])
    g.save(
        "la misma celda dos veces en un pedido: vale la última",
        [("digest", "email", True), ("digest", "email", False), ("digest", "email", True)],
    )
    assert g.get("todo junto") == {
        "activity": {"email": False, "push": False},
        "digest": {"email": True, "push": True},
        "invitations": {"email": False},
    }
    g.save("un pedido sin cambios no hace nada", [])
    assert len(g.get("sigue igual")) == 3
    g.save("un valor igual al que ya había", [("digest", "push", True)])
    assert g.get("no cambia nada")["digest"] == {"email": True, "push": True}
    g.save("el almacén no conoce la política: guarda cualquier celda", [("no-existe", "tampoco", True)])
    assert g.get("y la devuelve")["no-existe"] == {"tampoco": True}
    return vector("preferencias-guardadas/basico", "preferencias-guardadas", g.steps)


def guardadas_aislamiento() -> dict:
    g = Ajustes()
    g.save("ana, sin ámbito", [("activity", "email", True)])
    g.save("beto, sin ámbito", [("activity", "email", False), ("digest", "email", False)], recipient="beto")
    assert g.get("ana ve lo suyo") == {"activity": {"email": True}}
    assert g.get("beto ve lo suyo", recipient="beto") == {
        "activity": {"email": False},
        "digest": {"email": False},
    }
    assert g.get("otra persona no tiene nada", recipient="carla") == {}
    assert g.get("el destinatario se compara como texto exacto", recipient="Ana") == {}
    g.save("ana en el ámbito x", [("activity", "email", False), ("activity", "push", True)], scope="x")
    assert g.get("cada ámbito es independiente: x", scope="x") == {"activity": {"email": False, "push": True}}
    assert g.get("sin ámbito sigue como estaba") == {"activity": {"email": True}}
    assert g.get("un ámbito sin cambios está vacío: no hereda", scope="y") == {}
    assert g.get("el ámbito de una persona no es el de otra", recipient="beto", scope="x") == {}
    g.save("un ámbito de texto vacío no es lo mismo que sin ámbito", [("digest", "push", True)], scope="")
    assert g.get("sin ámbito no lo ve") == {"activity": {"email": True}}
    assert g.get("el ámbito vacío sí", scope="") == {"digest": {"push": True}}
    # Nombres que en algunos lenguajes chocan con propiedades heredadas, y
    # combinaciones que no se pueden confundir por cómo se arma una clave.
    g.save(
        "persona, ámbito, categoría y canal llamados como propiedades heredadas",
        [("constructor", "toString", True), ("__proto__", "__proto__", False)],
        recipient="__proto__",
        scope="constructor",
    )
    assert g.get("se leen igual que cualquier otro", recipient="__proto__", scope="constructor") == {
        "constructor": {"toString": True},
        "__proto__": {"__proto__": False},
    }
    assert g.get("y no aparecen donde no se guardaron", recipient="constructor") == {}
    g.save("persona `a b` sin ámbito", [("c", "d", True)], recipient="a b")
    g.save("persona `a` con ámbito `b`", [("c", "d", False)], recipient="a", scope="b")
    assert g.get("no se mezclan (1)", recipient="a b") == {"c": {"d": True}}
    assert g.get("no se mezclan (2)", recipient="a", scope="b") == {"c": {"d": False}}
    g.save("categoría `x y` con canal `z`", [("x y", "z", True), ("x", "y z", False)], recipient="dani")
    assert g.get("tampoco las celdas", recipient="dani") == {"x y": {"z": True}, "x": {"y z": False}}
    return vector("preferencias-guardadas/aislamiento", "preferencias-guardadas", g.steps)


# ---------------------------------------------------------------------------
# El recorrido completo: armar, guardar y entregar.
#
# Junta las piezas de arriba tal cual: el armado, el modelo de la bandeja, las
# preferencias guardadas y la resolución de canales.
# ---------------------------------------------------------------------------

OUTCOMES = ("sent", "expired", "failed", "skipped")


def members_ok(members: object, group: bool) -> bool:
    """La lista de miembros de un `notify`. Ausente o vacía vale siempre."""
    if members is None:
        return True
    if type(members) is not list:
        return False
    if members and not group:
        return False
    for m in members:
        if type(m) is not dict or set(m) != {"recipient_id", "id"}:
            return False
        if any(type(m[k]) is not str or not m[k] for k in m):
            return False
    return len({m["recipient_id"] for m in members}) == len({m["id"] for m in members}) == len(members)


def valid_channels(channels: object) -> bool:
    if type(channels) is not list:
        return False
    if any(type(c) is not str or not ID.fullmatch(c) or c == INBOX for c in channels):
        return False
    return len(set(channels)) == len(channels)


# ---------------------------------------------------------------------------
# No molestar: un horario por persona. La cuenta se hace sobre una tabla de los
# 1440 minutos del día, no con comparaciones: es otra manera de llegar a lo mismo.
# ---------------------------------------------------------------------------

HORA = re.compile(r"[0-9]{2}:[0-9]{2}")
ZONA = re.compile(r"[A-Za-z0-9_+/-]{1,64}")


def minute_of(text: str) -> int | None:
    if type(text) is not str or not HORA.fullmatch(text) or not text.isascii():
        return None
    hours, minutes = int(text[:2]), int(text[3:])
    return hours * 60 + minutes if hours < 24 and minutes < 60 else None


def valid_quiet_hours(setting: object) -> bool:
    if type(setting) is not dict or set(setting) != {"enabled", "start", "end", "timezone"}:
        return False
    if type(setting["enabled"]) is not bool:
        return False
    if minute_of(setting["start"]) is None or minute_of(setting["end"]) is None:
        return False
    zone = setting["timezone"]
    return type(zone) is str and ZONA.fullmatch(zone) is not None


def quiet_minutes(setting: dict) -> set[int]:
    """Los minutos del día que caen en el horario: se camina desde `start` hasta `end`."""
    if not setting["enabled"]:
        return set()
    minute, end, out = minute_of(setting["start"]), minute_of(setting["end"]), set()
    while minute != end:
        out.add(minute)
        minute = (minute + 1) % 1440
    return out


def in_quiet_hours(setting: dict, minute: int) -> bool:
    return minute in quiet_minutes(setting)


def horario(start="22:00", end="08:00", enabled=True, timezone="America/Lima") -> dict:
    return {"enabled": enabled, "start": start, "end": end, "timezone": timezone}


def silencio_validacion() -> dict:
    base = horario()
    casos = [
        ("un horario que cruza la medianoche", base, True),
        ("un horario dentro del mismo día", horario("13:00", "15:30"), True),
        ("apagado", horario(enabled=False), True),
        ("los extremos del día", horario("00:00", "23:59"), True),
        ("inicio y fin iguales (horario vacío)", horario("09:00", "09:00"), True),
        ("zona `UTC`", horario(timezone="UTC"), True),
        ("zona con signo y dígitos", horario(timezone="Etc/GMT+3"), True),
        ("zona con guion y guion bajo", horario(timezone="America/Port-au-Prince_x"), True),
        ("zona de 64 caracteres", horario(timezone="a" * 64), True),
        ("no es un objeto: nulo", None, False),
        ("no es un objeto: lista", [], False),
        ("no es un objeto: texto", "22:00-08:00", False),
        ("objeto vacío", {}, False),
        ("falta `enabled`", {k: v for k, v in base.items() if k != "enabled"}, False),
        ("falta `start`", {k: v for k, v in base.items() if k != "start"}, False),
        ("falta `end`", {k: v for k, v in base.items() if k != "end"}, False),
        ("falta `timezone`", {k: v for k, v in base.items() if k != "timezone"}, False),
        ("clave de más", {**base, "days": [1, 2]}, False),
        ("`enabled` no es booleano: número", {**base, "enabled": 1}, False),
        ("`enabled` no es booleano: texto", {**base, "enabled": "true"}, False),
        ("`enabled` no es booleano: nulo", {**base, "enabled": None}, False),
        ("hora 24", horario("24:00"), False),
        ("minuto 60", horario("23:60"), False),
        ("hora de un dígito", horario("9:00"), False),
        ("minuto de un dígito", horario("09:5"), False),
        ("con segundos", horario("09:00:00"), False),
        ("sin dos puntos", horario("0900"), False),
        ("con punto", horario("09.00"), False),
        ("con espacio adelante", horario(" 09:00"), False),
        ("con salto de línea al final", horario("09:00\n"), False),
        ("con signo", horario("-9:00"), False),
        ("dígitos que no son ASCII", horario("０９:００"), False),
        ("hora vacía", horario(""), False),
        ("`start` no es texto: número", {**base, "start": 900}, False),
        ("`start` no es texto: nulo", {**base, "start": None}, False),
        ("`end` inválido", horario(end="25:00"), False),
        ("`end` no es texto", {**base, "end": 480}, False),
        ("zona vacía", horario(timezone=""), False),
        ("zona de 65 caracteres", horario(timezone="a" * 65), False),
        ("zona con espacio", horario(timezone="America/New York"), False),
        ("zona con letra no ASCII", horario(timezone="América/Lima"), False),
        ("zona con dos puntos", horario(timezone="UTC+03:00"), False),
        ("zona con salto de línea al final", horario(timezone="UTC\n"), False),
        ("zona que no es texto: número", {**base, "timezone": -180}, False),
        ("zona que no es texto: nulo", {**base, "timezone": None}, False),
    ]
    cases = []
    for note, setting, valid in casos:
        assert valid_quiet_hours(setting) is valid, f"el generador no coincide con: {note}"
        cases.append({"note": note, "setting": setting, "valid": valid})
    return vector("silencio/validacion", "silencio-validacion", cases)


def silencio_horario() -> dict:
    cases = []

    def caso(note: str, setting: dict, hhmm: str, expect: bool) -> None:
        minute = minute_of(hhmm)
        # El valor esperado está escrito a mano; el modelo tiene que coincidir.
        assert in_quiet_hours(setting, minute) is expect, f"el generador no coincide con: {note}"
        cases.append({"note": f"{note} ({hhmm})", "setting": setting, "minute": minute, "expect": expect})

    noche = horario("22:00", "08:00")
    caso("cruza la medianoche: un minuto antes de empezar", noche, "21:59", False)
    caso("cruza la medianoche: el inicio entra", noche, "22:00", True)
    caso("cruza la medianoche: último minuto del día", noche, "23:59", True)
    caso("cruza la medianoche: primer minuto del día", noche, "00:00", True)
    caso("cruza la medianoche: de madrugada", noche, "03:30", True)
    caso("cruza la medianoche: un minuto antes de terminar", noche, "07:59", True)
    caso("cruza la medianoche: el fin no entra", noche, "08:00", False)
    caso("cruza la medianoche: al mediodía", noche, "12:00", False)

    siesta = horario("13:00", "15:30")
    caso("mismo día: un minuto antes", siesta, "12:59", False)
    caso("mismo día: el inicio entra", siesta, "13:00", True)
    caso("mismo día: en el medio", siesta, "14:15", True)
    caso("mismo día: un minuto antes de terminar", siesta, "15:29", True)
    caso("mismo día: el fin no entra", siesta, "15:30", False)
    caso("mismo día: a la medianoche", siesta, "00:00", False)
    caso("mismo día: último minuto del día", siesta, "23:59", False)

    vacio = horario("09:00", "09:00")
    caso("inicio y fin iguales: nunca, ni en ese minuto", vacio, "09:00", False)
    caso("inicio y fin iguales: nunca, ni el resto del día", vacio, "21:00", False)

    casi_todo = horario("08:00", "07:59")
    caso("todo el día menos un minuto: ese minuto queda afuera", casi_todo, "07:59", False)
    caso("todo el día menos un minuto: el siguiente entra", casi_todo, "08:00", True)
    caso("todo el día menos un minuto: el anterior entra", casi_todo, "07:58", True)

    desde_cero = horario("00:00", "06:00")
    caso("empieza a la medianoche: primer minuto", desde_cero, "00:00", True)
    caso("empieza a la medianoche: último minuto del día anterior", desde_cero, "23:59", False)
    hasta_cero = horario("20:00", "00:00")
    caso("termina a la medianoche: último minuto del día", hasta_cero, "23:59", True)
    caso("termina a la medianoche: la medianoche no entra", hasta_cero, "00:00", False)
    un_minuto = horario("23:59", "00:00")
    caso("un solo minuto, el último del día", un_minuto, "23:59", True)
    caso("un solo minuto: el anterior no", un_minuto, "23:58", False)

    apagado = horario("22:00", "08:00", enabled=False)
    caso("apagado: nunca, aunque caiga en el rango", apagado, "23:00", False)
    caso("apagado: nunca", apagado, "12:00", False)
    caso("la zona no cambia la cuenta: el minuto ya es local", horario(timezone="Asia/Tokyo"), "23:00", True)

    # Barrido completo de dos horarios: cada minuto del día, contra la tabla.
    for setting in (noche, siesta):
        for minute in range(0, 1440, 97):
            cases.append(
                {
                    "note": f"barrido {setting['start']}–{setting['end']}, minuto {minute}",
                    "setting": setting,
                    "minute": minute,
                    "expect": in_quiet_hours(setting, minute),
                }
            )
    return vector("silencio/horario", "silencio", cases)


class Horarios:
    def __init__(self) -> None:
        self.saved: dict[str, dict] = {}
        self.steps: list[dict] = []

    def set(self, note: str, setting: dict, recipient="ana") -> None:
        self.saved[recipient] = setting
        self.steps.append(
            {"note": note, "op": "set", "recipient_id": recipient, "setting": setting, "expect": None}
        )

    def get(self, note: str, recipient="ana") -> dict | None:
        out = self.saved.get(recipient)
        self.steps.append({"note": note, "op": "get", "recipient_id": recipient, "expect": out})
        return out


def silencio_guardado() -> dict:
    h = Horarios()
    assert h.get("quien nunca definió un horario no tiene ninguno") is None
    h.set("ana define su horario", horario())
    assert h.get("se lee lo que se guardó") == horario()
    assert h.get("es de ana: beto no tiene", recipient="beto") is None
    h.set("beto define otro", horario("13:00", "15:30", timezone="Europe/Madrid"), recipient="beto")
    assert h.get("cada uno ve el suyo", recipient="beto")["start"] == "13:00"
    assert h.get("el de ana no cambió")["start"] == "22:00"
    h.set("volver a guardar lo reemplaza entero", horario("23:30", "06:15", timezone="UTC"))
    assert h.get("queda el último") == horario("23:30", "06:15", timezone="UTC")
    h.set("apagarlo lo deja guardado", horario("23:30", "06:15", enabled=False, timezone="UTC"))
    assert h.get("apagado, con sus horas")["enabled"] is False
    h.set("guardar lo mismo que ya había", horario("23:30", "06:15", enabled=False, timezone="UTC"))
    assert h.get("no cambia nada")["end"] == "06:15"
    h.set("los extremos del día", horario("00:00", "23:59"), recipient="caro")
    assert h.get("se guardan tal cual", recipient="caro")["end"] == "23:59"
    for raro in ("Ana", "ana ", "", "ana%", "an_", "ana'--", "ANA"):
        assert h.get(f"la persona es texto exacto: {raro!r} no es ana", recipient=raro) is None
    h.set("una persona de texto vacío es una persona más", horario("01:00", "02:00"), recipient="")
    h.set("y una con comodines también", horario("03:00", "04:00"), recipient="an_")
    assert h.get("la vacía", recipient="")["start"] == "01:00"
    assert h.get("la de comodines", recipient="an_")["start"] == "03:00"
    assert h.get("ana sigue con el suyo")["start"] == "23:30"
    return vector("silencio-guardado/basico", "silencio-guardado", h.steps)


def recorrido_silencio() -> dict:
    policy = {
        "security": {
            "inapp": BANDEJA,
            "email": {"default": True, "locked": True},
            "sms": {"default": True, "locked": True, "urgent": True},
        },
        "activity": {
            "inapp": BANDEJA,
            "email": {"default": True},
            "push": {"default": True, "urgent": True},
            "sms": {"default": False},
        },
        "quiet": {"inapp": {**BANDEJA, "urgent": True}},
    }
    r = Recorrido(policy, RECORRIDO_CATALOGO_SILENCIO, ["email", "push", "sms"])
    comment = {"type": "comment.new", "recipient_id": "ana", "payload": {"author": "Beto", "excerpt": "Hola"}}
    login = {"type": "login.new", "recipient_id": "ana", "payload": {"device": "Firefox"}}
    de_noche, de_dia = minute_of("23:30"), minute_of("12:00")

    a = r.notify("sin horario definido, la hora no importa", comment, "n1", 1000, minute=de_noche)
    assert a["attempted"] == ["email", "push"]
    r.quiet("ana no quiere que la molesten de noche", horario("22:00", "08:00"))
    b = r.notify("de día, todo sale como siempre", comment, "n2", 2000, minute=de_dia)
    assert b["deliveries"] == {"email": "sent", "push": "sent"}
    c = r.notify(
        "de noche, lo que no es urgente se silencia y lo urgente sale", comment, "n3", 3000, minute=de_noche
    )
    assert c["created"] and c["attempted"] == ["push"]
    assert c["deliveries"] == {"email": "silenced", "push": "sent"}
    d = r.notify(
        "un canal bloqueado también se silencia si no es urgente", login, "n4", 4000, minute=de_noche
    )
    assert d["attempted"] == ["sms"] and d["deliveries"] == {"email": "silenced", "sms": "sent"}
    e = r.notify(
        "lo que contesta el canal urgente se anota igual que siempre",
        login,
        "n5",
        5000,
        minute=de_noche,
        sms="throws",
        email="sent",
    )
    assert e["deliveries"] == {"email": "silenced", "sms": "failed"}
    f = r.notify(
        "el horario es de ana: a beto le llega todo",
        {**comment, "recipient_id": "beto"},
        "n6",
        6000,
        minute=de_noche,
    )
    assert f["attempted"] == ["email", "push"]
    g = r.notify(
        "vale para cualquier ámbito: el horario es de la persona",
        {**comment, "scope": "proyecto-1"},
        "n7",
        7000,
        minute=de_noche,
    )
    assert g["deliveries"] == {"email": "silenced", "push": "sent"}
    r.save(
        "ana pide sms para la actividad y apaga el email",
        [("activity", "sms", True), ("activity", "email", False)],
    )
    h = r.notify("se silencia sólo lo que iba a salir", comment, "n8", 8000, minute=de_noche)
    assert h["deliveries"] == {"push": "sent", "sms": "silenced"}
    i = r.notify("el fin del horario no entra", comment, "n9", 9000, minute=minute_of("08:00"))
    assert i["attempted"] == ["push", "sms"]
    j = r.notify("el inicio sí", comment, "n10", 10000, minute=minute_of("22:00"))
    assert j["attempted"] == ["push"]
    k = r.notify(
        "un repetido no se entrega ni se silencia: no pasa nada",
        {**comment, "dedupe_key": "k1"},
        "n11",
        11000,
        minute=de_dia,
    )
    assert k["created"]
    again = r.notify("la segunda vez", {**comment, "dedupe_key": "k1"}, "n12", 12000, minute=de_noche)
    assert not again["created"] and again["deliveries"] == {}
    solo = r.notify(
        "una categoría que sólo tiene bandeja se guarda y listo",
        {"type": "note.saved", "recipient_id": "ana"},
        "n13",
        13000,
        minute=de_noche,
    )
    assert solo["created"] and solo["deliveries"] == {}
    r.quiet("ana apaga el horario sin borrarlo", horario("22:00", "08:00", enabled=False))
    m = r.notify("apagado, de noche sale todo", comment, "n14", 14000, minute=de_noche)
    assert m["attempted"] == ["push", "sms"]
    r.quiet("un horario vacío no silencia nada", horario("09:00", "09:00"))
    n = r.notify("ni en ese minuto", comment, "n15", 15000, minute=minute_of("09:00"))
    assert n["attempted"] == ["push", "sms"]
    r.quiet("un horario de siesta", horario("13:00", "15:30"))
    o = r.notify("silencia a la siesta", comment, "n16", 16000, minute=minute_of("14:00"))
    assert o["deliveries"] == {"push": "sent", "sms": "silenced"}
    p = r.notify("y no de noche", comment, "n17", 17000, minute=de_noche)
    assert p["attempted"] == ["push", "sms"]
    return recorrido_vector("recorrido/silencio", r)


RECORRIDO_CATALOGO_SILENCIO = {
    "login.new": {"category": "security", "title": "Nuevo inicio de sesión desde {device}"},
    "comment.new": {"category": "activity", "title": "{author} comentó", "body": "{excerpt}"},
    "note.saved": {"category": "quiet", "title": "Nota guardada"},
}


class Recorrido:
    def __init__(self, policy: dict, catalog: dict, channels: list[str]) -> None:
        assert is_valid_policy(policy) and is_valid_catalog(catalog, policy) and valid_channels(channels)
        self.policy, self.catalog, self.channels = policy, catalog, channels
        self.store, self.prefs, self.steps = Modelo(), Ajustes(), []
        self.quiet_hours: dict[str, dict] = {}

    def quiet(self, note: str, setting: dict, recipient="ana") -> None:
        assert valid_quiet_hours(setting), "el generador recibió un horario inválido"
        self.quiet_hours[recipient] = setting
        self.steps.append(
            {"note": note, "op": "quiet", "recipient_id": recipient, "setting": setting, "expect": None}
        )

    def save(self, note: str, changes: list[tuple[str, str, bool]], recipient="ana", scope=None) -> None:
        self.prefs.save(note, changes, recipient, scope)
        self.steps.append(self.prefs.steps.pop())

    def notify(
        self,
        note: str,
        event: dict,
        new_id: str,
        at_ms: int,
        minute: int | None = None,
        members: object = None,
        available: list[str] | None = None,
        **outcomes: str,
    ) -> dict:
        """`minute` es el minuto local del destinatario en este paso (para no molestar).
        `available` son los canales que la instalación dice disponibles para el
        destinatario en este paso; sin valor, todos los configurados.
        `outcomes` dice qué contesta cada canal en este paso; por omisión, `sent`.
        Además de los tres resultados válidos: `throws` (el canal falla con una
        excepción), `garbage` (devuelve algo que no es un resultado) y `unknown`
        (devuelve un resultado bien formado con un valor que no existe)."""
        expect = self._notify(event, new_id, at_ms, outcomes, minute, members, available)
        step = {
            "note": note,
            "op": "notify",
            "event": event,
            "id": new_id,
            "at_ms": at_ms,
            "outcomes": outcomes,
            "expect": expect,
        }
        if minute is not None:
            step["local_minute"] = minute
        if members is not None:
            step["members"] = members
        if available is not None:
            step["available"] = available
        self.steps.append(step)
        return expect

    def deliver(
        self,
        note: str,
        notification_id: str,
        minute: int | None = None,
        available: list[str] | None = None,
        **outcomes: str,
    ) -> dict:
        """Entrega otra vez un aviso que ya está en el almacén, sin guardar nada."""
        row = next(row for row in self.store.rows if row["id"] == notification_id)
        expect = self._entregar(row, outcomes, minute, available)
        step = {"note": note, "op": "deliver", "id": notification_id, "outcomes": outcomes, "expect": expect}
        if minute is not None:
            step["local_minute"] = minute
        if available is not None:
            step["available"] = available
        self.steps.append(step)
        return expect

    def _notify(
        self,
        event: dict,
        new_id: str,
        at_ms: int,
        outcomes: dict,
        minute: int | None,
        members: object,
        available: list[str] | None,
    ) -> dict:
        try:
            draft = build_draft(self.catalog, event)
        except Fallo as fallo:
            return {"error": {"reason": fallo.reason, "detail": fallo.detail}}
        group = self.catalog[draft["type"]].get("audience") == "group"
        if not members_ok(members, group):
            return {"error": {"reason": "invalid_field", "detail": "members"}}
        added = self.store.add(draft, new_id, at_ms)
        if "error" in added:
            return {"error": {"reason": added["error"], "detail": None}}
        if not added["created"]:
            return {**added, "copies": [], "attempted": [], "deliveries": {}}
        copies = []
        for member in members or []:
            copy = self.store.add({**draft, "recipient_id": member["recipient_id"]}, member["id"], at_ms)
            if "error" in copy:
                return {"error": {"reason": copy["error"], "detail": None}}
            copies.append(copy)
        return {**added, "copies": copies, **self._entregar(draft, outcomes, minute, available)}

    def _entregar(self, aviso: dict, outcomes: dict, minute: int | None, available: list[str] | None) -> dict:
        """Los pasos que comparten `notify` y `deliver`: elegir los canales y entregar."""
        saved: dict = {}
        for (who, where, category, channel), enabled in self.prefs.cells.items():
            if who == aviso["recipient_id"] and where == aviso["scope"]:
                saved.setdefault(category, {})[channel] = enabled
        usable = [c for c in self.channels if available is None or c in available]
        targets = channels_for(self.policy, aviso["category"], saved, usable)
        setting = self.quiet_hours.get(aviso["recipient_id"])
        silent = setting is not None and minute is not None and in_quiet_hours(setting, minute)
        deliveries, attempted = {}, []
        for channel in targets:
            if silent and not self.policy[aviso["category"]][channel].get("urgent", False):
                deliveries[channel] = "silenced"
                continue
            attempted.append(channel)
            answer = outcomes.get(channel, "sent")
            deliveries[channel] = answer if answer in OUTCOMES else "failed"
        return {"attempted": attempted, "deliveries": deliveries}


RECORRIDO_POLITICA = {
    "security": {
        "inapp": BANDEJA,
        "email": {"default": True, "locked": True},
        "sms": {"default": True, "locked": True},
    },
    "activity": {
        "inapp": BANDEJA,
        "email": {"default": False},
        "push": {"default": True},
        "sms": {"default": False},
    },
    "digest": {"inapp": BANDEJA, "email": {"default": True}},
    "quiet": {"inapp": BANDEJA},
}
RECORRIDO_CATALOGO = {
    "login.new": {"category": "security", "title": "Nuevo inicio de sesión desde {device}"},
    "comment.new": {"category": "activity", "title": "{author} comentó", "body": "{excerpt}"},
    "digest.weekly": {"category": "digest", "title": "Tu resumen de la semana"},
    "note.saved": {"category": "quiet", "title": "Nota guardada"},
}


def recorrido_basico() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["email", "push", "sms"])
    comment = {"type": "comment.new", "recipient_id": "ana", "payload": {"author": "Beto", "excerpt": "Hola"}}
    first = r.notify(
        "un aviso sale por los canales que la política enciende por defecto", comment, "n1", 1000
    )
    assert first["created"] and first["deliveries"] == {"push": "sent"}
    login = {"type": "login.new", "recipient_id": "ana", "payload": {"device": "Firefox"}}
    assert r.notify("una categoría con dos canales: se intentan en orden", login, "n2", 2000)[
        "attempted"
    ] == ["email", "sms"]
    assert (
        r.notify(
            "una categoría que sólo tiene bandeja no entrega nada",
            {"type": "note.saved", "recipient_id": "ana"},
            "n3",
            3000,
        )["deliveries"]
        == {}
    )
    r.save(
        "ana pide email para la actividad y apaga el push",
        [("activity", "email", True), ("activity", "push", False)],
    )
    assert r.notify("el siguiente aviso respeta lo guardado", comment, "n4", 4000)["attempted"] == ["email"]
    assert r.notify(
        "lo guardado es de ana: beto sigue con los valores por defecto",
        {**comment, "recipient_id": "beto"},
        "n5",
        5000,
    )["attempted"] == ["push"]
    r.save(
        "ana intenta apagar seguridad (el almacén lo guarda, la política lo ignora)",
        [("security", "email", False)],
    )
    assert r.notify("un canal bloqueado sale igual", login, "n6", 6000)["attempted"] == ["email", "sms"]
    r.save("ana enciende todo en la actividad", [("activity", "push", True), ("activity", "sms", True)])
    assert r.notify("tres canales, en orden por punto de código", comment, "n7", 7000)["attempted"] == [
        "email",
        "push",
        "sms",
    ]
    return recorrido_vector("recorrido/basico", r)


def recorrido_fallas() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["email", "push", "sms"])
    login = {"type": "login.new", "recipient_id": "ana", "payload": {"device": "Firefox"}}
    a = r.notify("un canal falla: el otro se intenta igual", login, "n1", 1000, email="failed")
    assert a["created"] and a["deliveries"] == {"email": "failed", "sms": "sent"}
    assert r.notify("un destino vencido", login, "n2", 2000, sms="expired")["deliveries"] == {
        "email": "sent",
        "sms": "expired",
    }
    assert r.notify(
        "un canal que lanza una excepción cuenta como fallido, y el resto sigue",
        login,
        "n3",
        3000,
        email="throws",
    )["deliveries"] == {"email": "failed", "sms": "sent"}
    assert r.notify(
        "un canal que devuelve algo que no es un resultado cuenta como fallido",
        login,
        "n4",
        4000,
        sms="garbage",
    )["deliveries"] == {"email": "sent", "sms": "failed"}
    assert r.notify(
        "un canal que devuelve un resultado con un valor que no existe cuenta como fallido",
        login,
        "n4b",
        4500,
        email="unknown",
    )["deliveries"] == {"email": "failed", "sms": "sent"}
    assert r.notify(
        "un canal que no tiene a dónde entregar lo informa como omitido", login, "n4c", 4700, sms="skipped"
    )["deliveries"] == {"email": "sent", "sms": "skipped"}
    b = r.notify(
        "todos fallan: el aviso queda guardado igual", login, "n5", 5000, email="throws", sms="failed"
    )
    assert b["created"] and b["notification"]["id"] == "n5"
    assert r.notify(
        "lo que contesta un canal que no se intenta no importa", login, "n6", 6000, push="throws"
    )["deliveries"] == {"email": "sent", "sms": "sent"}
    return recorrido_vector("recorrido/fallas", r)


def recorrido_duplicados() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["email", "push", "sms"])
    login = {
        "type": "login.new",
        "recipient_id": "ana",
        "payload": {"device": "Firefox"},
        "dedupe_key": "login-1",
    }
    assert r.notify("la primera vez se guarda y se entrega", login, "n1", 1000)["attempted"] == [
        "email",
        "sms",
    ]
    again = r.notify("repetir la clave no guarda ni vuelve a entregar", login, "n2", 2000)
    assert again == {
        "created": False,
        "notification": again["notification"],
        "copies": [],
        "attempted": [],
        "deliveries": {},
    }
    assert again["notification"]["id"] == "n1"
    assert r.notify("la misma clave para otra persona sí", {**login, "recipient_id": "beto"}, "n3", 3000)[
        "created"
    ]
    c = r.notify(
        "si la primera entrega falló, repetir la clave tampoco reintenta",
        {**login, "dedupe_key": "login-2"},
        "n4",
        4000,
        email="failed",
        sms="failed",
    )
    assert c["deliveries"] == {"email": "failed", "sms": "failed"}
    assert (
        r.notify("el reintento es de quien llama", {**login, "dedupe_key": "login-2"}, "n5", 5000)[
            "attempted"
        ]
        == []
    )
    assert r.notify(
        "un identificador repetido se rechaza y no entrega", {**login, "dedupe_key": None}, "n1", 6000
    ) == {"error": {"reason": "duplicate_id", "detail": None}}
    return recorrido_vector("recorrido/duplicados", r)


def recorrido_rechazos() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["email", "push", "sms"])
    assert (
        r.notify("un tipo que no está en el catálogo", {"type": "nope", "recipient_id": "ana"}, "n1", 1000)[
            "error"
        ]["reason"]
        == "unknown_type"
    )
    assert r.notify("falta una variable", {"type": "login.new", "recipient_id": "ana"}, "n2", 2000)[
        "error"
    ] == {"reason": "missing_variable", "detail": "device"}
    assert (
        r.notify(
            "un campo desconocido", {"type": "note.saved", "recipient_id": "ana", "urgente": True}, "n3", 3000
        )["error"]["detail"]
        == "urgente"
    )
    ok = r.notify(
        "un evento rechazado no guardó nada: el identificador sigue libre",
        {"type": "note.saved", "recipient_id": "ana"},
        "n1",
        4000,
    )
    assert ok["created"] and ok["notification"]["id"] == "n1"
    return recorrido_vector("recorrido/rechazos", r)


def recorrido_ambitos() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["email", "push", "sms"])
    comment = {"type": "comment.new", "recipient_id": "ana", "payload": {"author": "Beto", "excerpt": "Hola"}}
    r.save("ana, sin ámbito: quiere email", [("activity", "email", True)])
    r.save("ana, en el ámbito x: no quiere push", [("activity", "push", False)], scope="x")
    assert r.notify("un aviso sin ámbito usa lo guardado sin ámbito", comment, "n1", 1000)["attempted"] == [
        "email",
        "push",
    ]
    assert (
        r.notify("un aviso del ámbito x usa sólo lo guardado en x", {**comment, "scope": "x"}, "n2", 2000)[
            "attempted"
        ]
        == []
    )
    assert r.notify(
        "un aviso de un ámbito sin nada guardado usa los valores de la política",
        {**comment, "scope": "y"},
        "n3",
        3000,
    )["attempted"] == ["push"]
    return recorrido_vector("recorrido/ambitos", r)


def recorrido_disponibles() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["push", "telegram"])
    login = {"type": "login.new", "recipient_id": "ana", "payload": {"device": "Firefox"}}
    comment = {"type": "comment.new", "recipient_id": "ana", "payload": {"author": "Beto", "excerpt": "Hola"}}
    assert (
        r.notify(
            "los canales de la política que no están configurados no se intentan, aunque estén bloqueados",
            login,
            "n1",
            1000,
        )["attempted"]
        == []
    )
    assert r.notify("los configurados sí", comment, "n2", 2000)["attempted"] == ["push"]
    r.save("pedir un canal que no está configurado", [("activity", "email", True)])
    assert r.notify("no lo hace aparecer", comment, "n3", 3000)["attempted"] == ["push"]
    return recorrido_vector("recorrido/disponibles", r)


def recorrido_disponibles_por_persona() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["email", "push", "sms"])
    login = {"type": "login.new", "recipient_id": "ana", "payload": {"device": "Firefox"}}
    comment = {"type": "comment.new", "recipient_id": "ana", "payload": {"author": "Beto", "excerpt": "Hola"}}
    r.save("ana pide los avisos de actividad por email", [("activity", "email", True)])
    assert r.notify("sin decir qué hay disponible, valen todos los configurados", comment, "n1", 1000)[
        "attempted"
    ] == ["email", "push"]
    assert r.notify(
        "la instalación dice qué tiene disponible ana: sólo esos se miran",
        comment,
        "n2",
        2000,
        available=["push"],
    )["attempted"] == ["push"]
    assert r.notify(
        "un canal bloqueado en encendido tampoco sale si no está disponible para ella",
        login,
        "n3",
        3000,
        available=["email"],
    )["attempted"] == ["email"]
    assert r.notify(
        "un canal disponible que no está configurado no aparece",
        comment,
        "n4",
        4000,
        available=["push", "telegram"],
    )["attempted"] == ["push"]
    a = r.notify("nada disponible: el aviso se guarda y no se entrega", login, "n5", 5000, available=[])
    assert a["created"] and a["attempted"] == []
    return recorrido_vector("recorrido/disponibles-por-persona", r)


def recorrido_entregar() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, ["email", "push", "sms"])
    comment = {"type": "comment.new", "recipient_id": "ana", "payload": {"author": "Beto", "excerpt": "Hola"}}
    keyed = {**comment, "dedupe_key": "c-1"}
    assert r.notify("se avisa y el push falla", keyed, "n1", 1000, push="failed")["deliveries"] == {
        "push": "failed"
    }
    assert r.notify("el repetido no se vuelve a entregar", keyed, "n2", 2000)["attempted"] == []
    assert r.deliver("entregar el aviso guardado lo intenta otra vez", "n1")["deliveries"] == {"push": "sent"}
    assert r.deliver("y otra: entregar no lleva la cuenta de lo entregado", "n1")["attempted"] == ["push"]
    r.save("ana pide la actividad por email", [("activity", "email", True)])
    assert r.deliver("las preferencias son las de ahora, no las del momento en que se guardó", "n1")[
        "attempted"
    ] == ["email", "push"]
    assert r.deliver("con la disponibilidad de ahora", "n1", available=["email"])["attempted"] == ["email"]
    r.quiet("ana silencia la noche", {"enabled": True, "start": "22:00", "end": "08:00", "timezone": "UTC"})
    assert r.deliver("en silencio no se interrumpe", "n1", minute=23 * 60)["deliveries"] == {
        "email": "silenced",
        "push": "silenced",
    }
    assert r.deliver("fuera del horario, sí", "n1", minute=12 * 60)["attempted"] == ["email", "push"]
    return recorrido_vector("recorrido/entregar", r)


def recorrido_sin_canales() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, [])
    login = {"type": "login.new", "recipient_id": "ana", "payload": {"device": "Firefox"}}
    a = r.notify("sin ningún canal configurado, el aviso se guarda y no se entrega", login, "n1", 1000)
    assert a["created"] and a["attempted"] == [] and a["deliveries"] == {}
    return recorrido_vector("recorrido/sin-canales", r)


RECORRIDO_CATALOGO_GRUPOS = {
    **RECORRIDO_CATALOGO,
    "report.ready": {
        "category": "digest",
        "title": "Informe listo: {name}",
        "audience": "group",
    },
    "report.mine": {"category": "digest", "title": "Tu informe está listo: {name}", "audience": "person"},
}


def recorrido_grupos() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO_GRUPOS, ["email", "push", "sms"])
    report = {"type": "report.ready", "recipient_id": "equipo-1", "payload": {"name": "Marzo"}}
    ana, beto = {"recipient_id": "ana", "id": "c-ana"}, {"recipient_id": "beto", "id": "c-beto"}

    a = r.notify(
        "un aviso de grupo sale una vez, por los canales del grupo, y deja una copia a cada miembro",
        report,
        "n1",
        1000,
        members=[ana, beto],
    )
    assert a["created"] and a["attempted"] == ["email"]
    assert [c["notification"]["recipient_id"] for c in a["copies"]] == ["ana", "beto"]
    r.save("ana apaga el email de los resúmenes", [("digest", "email", False)])
    b = r.notify(
        "las preferencias de los miembros no cambian lo que sale para el grupo",
        report,
        "n2",
        2000,
        members=[{"recipient_id": "ana", "id": "c2-ana"}],
    )
    assert b["attempted"] == ["email"] and b["copies"][0]["created"]
    r.save("el grupo apaga el email de los resúmenes", [("digest", "email", False)], recipient="equipo-1")
    c = r.notify("las del grupo sí", report, "n3", 3000, members=[{"recipient_id": "beto", "id": "c3-beto"}])
    assert c["attempted"] == [] and c["copies"][0]["created"]
    r.save(
        "en el ámbito p2 el grupo quiere sms",
        [("digest", "email", True)],
        recipient="equipo-1",
        scope="p2",
    )
    d = r.notify(
        "el ámbito del aviso elige las preferencias del grupo",
        {**report, "scope": "p2"},
        "n4",
        4000,
        members=[{"recipient_id": "ana", "id": "c4-ana"}],
    )
    assert d["attempted"] == ["email"] and d["copies"][0]["notification"]["scope"] == "p2"
    e = r.notify("sin miembros, sólo el grupo", report, "n5", 5000)
    assert e["created"] and e["copies"] == []
    f = r.notify("con la lista vacía, igual", report, "n6", 6000, members=[])
    assert f["copies"] == []
    r.quiet("el grupo tiene horario de no molestar", horario("22:00", "08:00"), recipient="equipo-1")
    g = r.notify(
        "el horario del grupo silencia lo que sale para el grupo; las copias se guardan igual",
        {**report, "scope": "p2"},
        "n7",
        7000,
        minute=minute_of("23:00"),
        members=[{"recipient_id": "ana", "id": "c7-ana"}],
    )
    assert g["deliveries"] == {"email": "silenced"} and g["copies"][0]["created"]
    keyed = {**report, "dedupe_key": "informe-marzo"}
    h = r.notify(
        "con clave, el grupo y cada miembro la guardan",
        keyed,
        "n8",
        8000,
        members=[{"recipient_id": "ana", "id": "c8-ana"}],
    )
    assert h["created"] and h["copies"][0]["created"]
    i = r.notify(
        "el repetido del grupo no hace copias ni entrega",
        keyed,
        "n9",
        9000,
        members=[{"recipient_id": "beto", "id": "c9-beto"}],
    )
    assert not i["created"] and i["copies"] == [] and i["attempted"] == []
    r.notify(
        "beto ya tenía un aviso suyo con la misma clave",
        {
            "type": "report.mine",
            "recipient_id": "beto",
            "payload": {"name": "Abril"},
            "dedupe_key": "informe-abril",
        },
        "n10",
        10000,
    )
    j = r.notify(
        "su copia no se crea y se informa como no creada; la de ana sí",
        {**report, "payload": {"name": "Abril"}, "dedupe_key": "informe-abril"},
        "n11",
        11000,
        members=[{"recipient_id": "beto", "id": "c11-beto"}, {"recipient_id": "ana", "id": "c11-ana"}],
    )
    assert j["created"] and [c["created"] for c in j["copies"]] == [False, True]
    assert j["copies"][0]["notification"]["id"] == "n10"
    return recorrido_vector("recorrido/grupos", r)


def recorrido_miembros() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO_GRUPOS, ["email", "push", "sms"])
    report = {"type": "report.ready", "recipient_id": "equipo-1", "payload": {"name": "Marzo"}}
    mine = {"type": "report.mine", "recipient_id": "ana", "payload": {"name": "Marzo"}}
    ana = {"recipient_id": "ana", "id": "c-ana"}
    casos = [
        ("un tipo de persona no lleva miembros", mine, [ana]),
        ("los miembros no son una lista", report, {"ana": "c-ana"}),
        ("un miembro no es un objeto", report, ["ana"]),
        ("a un miembro le falta el id", report, [{"recipient_id": "ana"}]),
        ("un miembro con una clave de más", report, [{**ana, "scope": "x"}]),
        ("un miembro con destinatario vacío", report, [{"recipient_id": "", "id": "c1"}]),
        ("un miembro con id vacío", report, [{"recipient_id": "ana", "id": ""}]),
        ("un miembro con id que no es texto", report, [{"recipient_id": "ana", "id": 1}]),
        ("dos veces la misma persona", report, [ana, {"recipient_id": "ana", "id": "c2"}]),
        ("dos veces el mismo id", report, [ana, {"recipient_id": "beto", "id": "c-ana"}]),
    ]
    for n, (note, event, members) in enumerate(casos, start=1):
        got = r.notify(note, event, f"x{n}", n * 1000, members=members)
        assert got == {"error": {"reason": "invalid_field", "detail": "members"}}, note
    assert r.notify(
        "el tipo se revisa antes que los miembros", {**report, "type": "nope"}, "x98", 98000, members=["ana"]
    )["error"] == {"reason": "unknown_type", "detail": "nope"}
    ok = r.notify(
        "un tipo de persona con la lista vacía vale, y lo rechazado no guardó nada",
        mine,
        "x1",
        99000,
        members=[],
    )
    assert ok["created"] and ok["copies"] == []
    r.notify(
        "el id de una copia ocupado", report, "x100", 100000, members=[{"recipient_id": "beto", "id": "x1"}]
    )
    assert r.steps[-1]["expect"] == {"error": {"reason": "duplicate_id", "detail": None}}
    after = r.notify(
        "lo guardado antes de la copia rechazada queda: el id del grupo ya está ocupado",
        report,
        "x100",
        101000,
    )
    assert after == {"error": {"reason": "duplicate_id", "detail": None}}
    return recorrido_vector("recorrido/miembros", r)


def recorrido_vector(vector_id: str, r: Recorrido) -> dict:
    return {
        "spec": SPEC,
        "id": vector_id,
        "kind": "recorrido",
        "policy": r.policy,
        "catalog": r.catalog,
        "channels": r.channels,
        "steps": r.steps,
    }


def canales_configurados() -> dict:
    casos = [
        ("ninguno", [], True),
        ("uno", ["email"], True),
        ("varios", ["email", "push", "sms"], True),
        ("con guion, guion bajo y dígitos", ["web-push", "sms_2"], True),
        ("uno que ninguna política nombra", ["telegram"], True),
        ("llamado `constructor`", ["constructor"], True),
        ("repetido", ["email", "push", "email"], False),
        ("`inapp` no es un canal de entrega", ["email", "inapp"], False),
        ("con mayúscula", ["Email"], False),
        ("vacío", [""], False),
        ("con espacio", ["web push"], False),
        ("que empieza con dígito", ["2fa"], False),
    ]
    cases = []
    for note, channels, valid in casos:
        assert valid_channels(channels) is valid, note
        cases.append({"note": note, "channels": channels, "valid": valid})
    return {"spec": SPEC, "id": "recorrido-canales/validacion", "kind": "recorrido-canales", "cases": cases}


# ---------------------------------------------------------------------------
# Email: el mensaje por omisión y qué direcciones se aceptan.
# ---------------------------------------------------------------------------

CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
PROHIBIDOS = set('<>,;:"()[]\\')


def email_message(notification: dict) -> dict:
    parts = [p for p in (notification["body"], notification["action_url"]) if p]
    return {
        "subject": CONTROL.sub(" ", notification["title"]),
        "text": "\n\n".join(parts) if parts else notification["title"],
    }


def valid_address(address: object) -> bool:
    if type(address) is not str or not 3 <= len(address) <= 254:
        return False
    if any(not "!" <= char <= "~" or char in PROHIBIDOS for char in address):
        return False
    local, at, domain = address.partition("@")
    return at == "@" and "@" not in domain and local != "" and domain != ""


def email_mensaje() -> dict:
    casos = [
        ("sólo título: el texto es el título", aviso(title="Nuevo inicio de sesión")),
        ("con cuerpo", aviso(title="Ana comentó", body="¿Va con IVA?")),
        ("con enlace", aviso(title="Ana comentó", action_url="https://example.com/doc/7")),
        (
            "con cuerpo y enlace: separados por una línea en blanco",
            aviso(title="Ana comentó", body="¿Va con IVA?", action_url="https://example.com/doc/7"),
        ),
        ("un cuerpo vacío es como no tener cuerpo", aviso(title="Ana comentó", body="", action_url="/doc/7")),
        ("un cuerpo vacío y sin enlace: el texto es el título", aviso(title="Ana comentó", body="")),
        (
            "el cuerpo se copia tal cual, con sus saltos de línea",
            aviso(title="Resumen", body="Uno\nDos\r\n\tTres  "),
        ),
        ("el enlace no se interpreta", aviso(title="Aviso", action_url="/relativo?x=1&y=2")),
        ("un salto de línea en el título pasa a ser un espacio", aviso(title="Línea uno\nLínea dos")),
        ("varios caracteres de control seguidos son un solo espacio", aviso(title="A\r\n\t B")),
        (
            "un intento de inyectar una cabecera queda en una línea",
            aviso(title="Hola\r\nBcc: otro@example.com"),
        ),
        ("otros caracteres de control también", aviso(title="A\x00B\x1fC\x7fD")),
        ("control al principio y al final: queda el espacio", aviso(title="\nHola\n")),
        ("los espacios comunes no se tocan", aviso(title="  dos  espacios  ")),
        ("el texto no latino y fuera del plano básico pasa igual", aviso(title="🔔 Ñandú — 通知", body="😀")),
        ("si el texto es el título, va sin tocar", aviso(title="Uno\nDos")),
    ]
    cases = [
        {
            "note": note,
            "notification": {"id": "n1", **n, "created_at_ms": 1000, "read_at_ms": None},
            "expect": email_message(n),
        }
        for note, n in casos
    ]
    return {"spec": SPEC, "id": "email/mensaje", "kind": "email-mensaje", "cases": cases}


def email_direcciones() -> dict:
    casos = [
        ("una dirección común", "ana@example.com", True),
        ("con punto, guion y más", "ana.perez+avisos@mail-1.example.com", True),
        ("lo más corto posible", "a@b", True),
        ("con caracteres raros pero permitidos", "a!#$%&'*/=?^_`{|}~@example.com", True),
        ("de 254 caracteres", "a" * 242 + "@example.com", True),
        ("de 255 caracteres", "a" * 243 + "@example.com", False),
        ("vacía", "", False),
        ("sin arroba", "ana.example.com", False),
        ("dos arrobas", "ana@uno@example.com", False),
        ("sin parte local", "@example.com", False),
        ("sin dominio", "ana@", False),
        ("sólo la arroba", "@", False),
        ("con espacio", "ana perez@example.com", False),
        ("con espacio al final", "ana@example.com ", False),
        ("con salto de línea", "ana@example.com\nBcc: otro@example.com", False),
        ("con retorno de carro", "ana@example.com\r", False),
        ("con tabulación", "ana\t@example.com", False),
        ("con nulo", "ana\x00@example.com", False),
        ("con nombre y corchetes angulares", "Ana <ana@example.com>", False),
        ("con corchetes angulares", "<ana@example.com>", False),
        ("dos direcciones con coma", "ana@example.com,beto@example.com", False),
        ("dos direcciones con punto y coma", "ana@example.com;beto@example.com", False),
        ("con comillas", '"ana"@example.com', False),
        ("con paréntesis", "ana(comentario)@example.com", False),
        ("con dos puntos", "mailto:ana@example.com", False),
        ("con barra invertida", "ana\\@example.com", False),
        ("con letra no ASCII", "ñandú@example.com", False),
        ("con dominio no ASCII", "ana@ñandú.example", False),
        ("no es texto: nulo", None, False),
        ("no es texto: número", 7, False),
        ("no es texto: lista", ["ana@example.com"], False),
    ]
    cases = []
    for note, address, valid in casos:
        assert valid_address(address) is valid, note
        cases.append({"note": note, "address": address, "valid": valid})
    return {"spec": SPEC, "id": "email/direcciones", "kind": "email-direcciones", "cases": cases}


# ---------------------------------------------------------------------------
# La bandeja por HTTP: un servidor de juguete sobre los modelos de arriba.
#
# Cada pedido se resuelve con comparaciones de texto y expresiones regulares,
# sin router: es la lectura más literal de la tabla de rutas del spec.
# ---------------------------------------------------------------------------

DECIMAL = re.compile(r"[1-9][0-9]*")
ISO = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})Z")
B64URL = re.compile(r"[A-Za-z0-9_-]*")
RUTAS = {
    "/feed": "GET",
    "/unread-count": "GET",
    "/feed/read-all": "POST",
    "/preferences": ("GET", "PATCH"),
    "/quiet-hours": ("GET", "PUT"),
}
#: Zonas que el servidor de juguete conoce. Alcanza para los vectores.
ZONAS = {"America/Lima", "UTC", "Europe/Madrid"}


def iso(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    y, mo, d, h, mi, s = time.gmtime(seconds)[:6]
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}.{millis:03d}Z"


def from_iso(text: object) -> int | None:
    if not isinstance(text, str) or not ISO.fullmatch(text):
        return None
    y, mo, d, h, mi, s, ms = (int(x) for x in ISO.fullmatch(text).groups())
    if y < 1970 or not 1 <= mo <= 12 or not 1 <= d <= calendar.monthrange(y, mo)[1]:
        return None
    if h > 23 or mi > 59 or s > 59:
        return None
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) * 1000 + ms


def cursor_text(created_at_ms: int, row_id: str) -> str:
    raw = f"{created_at_ms}:{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def cursor_value(text: str) -> dict | None:
    if not B64URL.fullmatch(text) or len(text) % 4 == 1:
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    ms, sep, row_id = raw.partition(":")
    if not sep or not (ms == "0" or DECIMAL.fullmatch(ms)) or int(ms) > 8_640_000_000_000_000:
        return None
    if cursor_text(int(ms), row_id) != text:
        return None
    return {"created_at_ms": int(ms), "id": row_id}


def en_el_cable(row: dict) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "category": row["category"],
        "title": row["title"],
        "body": row["body"],
        "action_url": row["action_url"],
        "payload": row["payload"],
        "scope": row["scope"],
        "created_at": iso(row["created_at_ms"]),
        "read_at": None if row["read_at_ms"] is None else iso(row["read_at_ms"]),
    }


def falla(status: int, code: str, **extra) -> dict:
    return {"status": status, "body": {"error": {"code": code, **extra}}}


def ok(body: dict) -> dict:
    return {"status": 200, "body": body}


class Servidor:
    def __init__(self, policy: dict, available: list[str]) -> None:
        self.policy, self.available = policy, available
        self.modelo = Modelo()
        self.ajustes: dict[tuple[str, str | None], dict] = {}
        self.horarios: dict[str, dict] = {}

    def atender(self, who, method, path, query, body, raw, at_ms) -> dict | None:
        id_read = re.fullmatch(r"/feed/([^/]+)/read", path)
        if id_read:
            allowed = ("POST",)
        elif path in RUTAS:
            allowed = RUTAS[path] if isinstance(RUTAS[path], tuple) else (RUTAS[path],)
        else:
            return None
        if method not in allowed:
            return falla(405, "method_not_allowed")
        if who is None:
            return falla(401, "unauthenticated")
        params: dict[str, str] = {}
        for key, value in query:
            if key in params:
                return falla(400, "invalid_query")
            params[key] = value
        if id_read:
            row_id = urllib.parse.unquote(id_read.group(1))
            return ok({"changed": self.modelo.mark_read(row_id, who, at_ms)})
        if path == "/feed":
            return self.feed(who, params)
        if path == "/unread-count":
            cap_text = params.get("cap", "99")
            if not DECIMAL.fullmatch(cap_text) or int(cap_text) > 1000:
                return falla(400, "invalid_cap")
            cap = int(cap_text)
            n = self.modelo.count_unread(who, params.get("scope"), cap + 1)
            return ok({"count": min(n, cap), "capped": n > cap})
        if path == "/feed/read-all":
            return self.read_all(who, body, raw, at_ms)
        if path == "/preferences":
            return self.preferencias(who, method, params.get("scope"), body, raw)
        return self.horario(who, method, body, raw)

    def feed(self, who: str, params: dict) -> dict:
        limit_text = params.get("limit", "20")
        if not DECIMAL.fullmatch(limit_text) or int(limit_text) > 100:
            return falla(400, "invalid_limit")
        if params.get("unread_only", "false") not in ("true", "false"):
            return falla(400, "invalid_query")
        cursor = None
        if "cursor" in params:
            cursor = cursor_value(params["cursor"])
            if cursor is None:
                return falla(400, "invalid_cursor")
        page = self.modelo.list(
            {
                "recipient_id": who,
                "limit": int(limit_text),
                "cursor": cursor,
                "unread_only": params.get("unread_only") == "true",
                "category": params.get("category"),
                "scope": params.get("scope"),
            }
        )
        edge = page["next_cursor"]
        return ok(
            {
                "items": [en_el_cable(row) for row in page["items"]],
                "next_cursor": None if edge is None else cursor_text(edge["created_at_ms"], edge["id"]),
            }
        )

    def read_all(self, who, body, raw, at_ms) -> dict:
        if raw is not None or not isinstance(body, dict) or not set(body) <= {"before", "scope", "category"}:
            return falla(400, "invalid_body")
        before = from_iso(body.get("before"))
        texto_o_nada = all(body.get(k) is None or isinstance(body[k], str) for k in ("scope", "category"))
        if before is None or not texto_o_nada:
            return falla(400, "invalid_body")
        changed = self.modelo.mark_all_read(who, before, at_ms, body.get("scope"), body.get("category"))
        return ok({"changed": changed})

    def preferencias(self, who, method, scope, body, raw) -> dict:
        guardado = self.ajustes.setdefault((who, scope), {})
        if method == "PATCH":
            de_objetos = isinstance(body, dict) and all(isinstance(v, dict) for v in body.values())
            if raw is not None or not de_objetos:
                return falla(400, "invalid_body")
            result = patch_result(self.policy, body, self.available)
            if not result["ok"]:
                e = result["error"]
                return falla(422, e["reason"], category=e["category"], channel=e["channel"])
            for change in result["changes"]:
                guardado.setdefault(change["category"], {})[change["channel"]] = change["enabled"]
        return ok({"categories": matrix_for(self.policy, guardado, self.available)})

    def horario(self, who, method, body, raw) -> dict:
        if method == "PUT":
            if raw is not None:
                return falla(400, "invalid_body")
            if not valid_quiet_hours(body):
                return falla(422, "invalid_quiet_hours")
            if body["timezone"] not in ZONAS:
                return falla(422, "unknown_timezone")
            self.horarios[who] = body
        return ok({"quiet_hours": self.horarios.get(who)})


class Pedidos:
    """Arma los pasos de un vector `http` contra el servidor de juguete."""

    def __init__(self, policy: dict | None = None, available: list[str] | None = None) -> None:
        self.policy = EJEMPLO if policy is None else policy
        self.available = ["email"] if available is None else available
        self.servidor = Servidor(self.policy, self.available)
        self.steps: list[dict] = []
        self.clock = 100_000

    def add(self, note, new_id, at_ms, **draft) -> None:
        d = aviso(**draft)
        expect = self.servidor.modelo.add(d, new_id, at_ms)
        self.steps.append(
            {"note": note, "op": "add", "draft": d, "id": new_id, "at_ms": at_ms, "expect": expect}
        )

    def req(self, note, method, path, query=(), body=..., raw=None, who="ana"):
        self.clock += 1000
        step = {"note": note, "op": "request", "as": who, "method": method, "path": path}
        step["query"] = [list(pair) for pair in query]
        if body is not ...:
            step["body"] = body
        if raw is not None:
            step["raw_body"] = raw
        step["at_ms"] = self.clock
        got = self.servidor.atender(
            who, method, path, list(query), None if body is ... else body, raw, self.clock
        )
        step["expect"] = got
        self.steps.append(step)
        return got

    def vector(self, vector_id: str) -> dict:
        return {
            "spec": SPEC,
            "id": vector_id,
            "kind": "http",
            "policy": self.policy,
            "available": self.available,
            "steps": self.steps,
        }


def items_of(response: dict) -> list[str]:
    return [item["id"] for item in response["body"]["items"]]


def code_of(response: dict) -> tuple[int, str]:
    return response["status"], response["body"]["error"]["code"]


def http_bandeja() -> dict:
    p = Pedidos()
    for index in range(1, 6):
        p.add(f"aviso {index}", f"n{index}", index * 1000)
    p.add("uno del ámbito acme", "n6", 6000, scope="acme", category="security")
    p.add("uno de otro ámbito", "n7", 7000, scope="otro")
    p.add("uno de otra persona", "b1", 8000, recipient="beto")
    p.add("un id con barra y espacio", "x/y z", 500)

    assert p.req("una ruta que no es del contrato no se atiende", "GET", "/nada") is None
    assert p.req("ni con una barra al final", "GET", "/feed/") is None
    assert p.req("ni sin id", "POST", "/feed//read") is None
    assert code_of(p.req("sin persona", "GET", "/feed", who=None)) == (401, "unauthenticated")
    assert code_of(p.req("el método va antes que la persona", "DELETE", "/feed", who=None)) == (
        405,
        "method_not_allowed",
    )
    assert code_of(p.req("read-all es POST", "GET", "/feed/read-all")) == (405, "method_not_allowed")

    r = p.req("la bandeja, del más nuevo al más viejo", "GET", "/feed")
    assert items_of(r) == ["n7", "n6", "n5", "n4", "n3", "n2", "n1", "x/y z"]
    assert r["body"]["next_cursor"] is None
    r = p.req("de a tres", "GET", "/feed", [("limit", "3")])
    assert items_of(r) == ["n7", "n6", "n5"]
    r = p.req("la página siguiente", "GET", "/feed", [("limit", "3"), ("cursor", r["body"]["next_cursor"])])
    assert items_of(r) == ["n4", "n3", "n2"]
    r = p.req("la última", "GET", "/feed", [("limit", "3"), ("cursor", r["body"]["next_cursor"])])
    assert items_of(r) == ["n1", "x/y z"] and r["body"]["next_cursor"] is None
    assert items_of(p.req("con ámbito: el suyo y los de cuenta", "GET", "/feed", [("scope", "acme")])) == [
        "n6",
        "n5",
        "n4",
        "n3",
        "n2",
        "n1",
        "x/y z",
    ]
    assert items_of(p.req("el ámbito vacío es un ámbito", "GET", "/feed", [("scope", "")]))[0] == "n5"
    assert items_of(p.req("por categoría", "GET", "/feed", [("category", "security")])) == ["n6"]
    assert len(items_of(p.req("parámetros desconocidos se ignoran", "GET", "/feed", [("_", "123")]))) == 8
    assert items_of(p.req("beto ve lo suyo", "GET", "/feed", who="beto")) == ["b1"]
    assert items_of(p.req("alguien sin avisos", "GET", "/feed", who="carla")) == []

    for note, value in [
        ("límite cero", "0"),
        ("límite mayor que 100", "101"),
        ("límite con cero a la izquierda", "07"),
        ("límite que no es un número", "diez"),
        ("límite vacío", ""),
        ("límite negativo", "-1"),
        ("límite con decimales", "2.0"),
    ]:
        assert code_of(p.req(note, "GET", "/feed", [("limit", value)])) == (400, "invalid_limit")
    assert items_of(p.req("límite 100", "GET", "/feed", [("limit", "100")]))[0] == "n7"
    otro = cursor_text(5000, "n5")
    for note, value in [
        ("cursor que no es base64url", "%%%"),
        ("cursor con relleno", otro + "="),
        ("cursor sin dos puntos", base64.urlsafe_b64encode(b"5000").decode().rstrip("=")),
        ("cursor con fecha que no es un número", base64.urlsafe_b64encode(b"x:n5").decode().rstrip("=")),
        ("cursor con cero a la izquierda", base64.urlsafe_b64encode(b"05000:n5").decode().rstrip("=")),
        ("cursor vacío", ""),
        ("cursor más allá de la última fecha", cursor_text(8_640_000_000_000_001, "n5")),
    ]:
        assert code_of(p.req(note, "GET", "/feed", [("cursor", value)])) == (400, "invalid_cursor"), note
    assert items_of(p.req("un cursor armado a mano vale", "GET", "/feed", [("cursor", otro)])) == [
        "n4",
        "n3",
        "n2",
        "n1",
        "x/y z",
    ]
    assert code_of(p.req("unread_only raro", "GET", "/feed", [("unread_only", "1")])) == (
        400,
        "invalid_query",
    )
    assert code_of(p.req("un parámetro repetido", "GET", "/feed", [("limit", "2"), ("limit", "3")])) == (
        400,
        "invalid_query",
    )

    assert p.req("el contador", "GET", "/unread-count")["body"] == {"count": 8, "capped": False}
    assert p.req("con tope", "GET", "/unread-count", [("cap", "3")])["body"] == {"count": 3, "capped": True}
    assert p.req("tope igual al total: no es 8+", "GET", "/unread-count", [("cap", "8")])["body"] == {
        "count": 8,
        "capped": False,
    }
    assert p.req("con ámbito", "GET", "/unread-count", [("scope", "otro")])["body"]["count"] == 7
    for note, value in [("tope cero", "0"), ("tope mayor que 1000", "1001"), ("tope raro", "x")]:
        assert code_of(p.req(note, "GET", "/unread-count", [("cap", value)])) == (400, "invalid_cap")

    assert p.req("marcar uno", "POST", "/feed/n1/read")["body"] == {"changed": True}
    assert p.req("marcarlo de nuevo no cambia nada", "POST", "/feed/n1/read")["body"] == {"changed": False}
    assert p.req("uno que no existe no es un error", "POST", "/feed/zz/read")["body"] == {"changed": False}
    assert p.req("lo de otra persona tampoco", "POST", "/feed/b1/read")["body"] == {"changed": False}
    assert p.req("el id va decodificado", "POST", "/feed/x%2Fy%20z/read")["body"] == {"changed": True}
    r = p.req(
        "se ve leído, con la hora del servidor", "GET", "/feed", [("category", "activity"), ("scope", "")]
    )
    assert [i["read_at"] is not None for i in r["body"]["items"]] == [False] * 4 + [True, True]
    r = p.req("sólo los no leídos", "GET", "/feed", [("unread_only", "true")])
    assert items_of(r) == ["n7", "n6", "n5", "n4", "n3", "n2"]

    hasta = iso(3000 + 1)
    assert p.req("marcar todo hasta n3, que se vio", "POST", "/feed/read-all", body={"before": hasta})[
        "body"
    ] == {"changed": 2}
    assert p.req(
        "por ámbito y categoría",
        "POST",
        "/feed/read-all",
        body={"before": iso(99000), "scope": "acme", "category": "security"},
    )["body"] == {"changed": 1}
    assert p.req("el resto", "POST", "/feed/read-all", body={"before": iso(99000), "scope": None})[
        "body"
    ] == {"changed": 3}
    assert p.req("no queda nada", "GET", "/unread-count")["body"] == {"count": 0, "capped": False}
    assert p.req("beto sigue igual", "GET", "/unread-count", who="beto")["body"]["count"] == 1
    for note, kwargs in [
        ("un cuerpo que no es JSON", {"raw": "{"}),
        ("sin cuerpo", {}),
        ("un cuerpo que no es un objeto", {"body": [iso(1)]}),
        ("sin before", {"body": {}}),
        ("una clave de más", {"body": {"before": iso(1), "recipient_id": "beto"}}),
        ("before sin milisegundos", {"body": {"before": "2026-11-20T10:00:00Z"}}),
        ("before con zona", {"body": {"before": "2026-11-20T10:00:00.000-03:00"}}),
        ("before en un día que no existe", {"body": {"before": "2026-02-30T10:00:00.000Z"}}),
        ("before antes de 1970", {"body": {"before": "1969-12-31T23:59:59.999Z"}}),
        ("before como número", {"body": {"before": 3000}}),
        ("scope que no es texto", {"body": {"before": iso(1), "scope": 5}}),
    ]:
        assert code_of(p.req(note, "POST", "/feed/read-all", **kwargs)) == (400, "invalid_body"), note
    return p.vector("http/bandeja")


def http_preferencias() -> dict:
    p = Pedidos()
    assert code_of(p.req("sin persona", "GET", "/preferences", who=None)) == (401, "unauthenticated")
    assert code_of(p.req("no se borra", "DELETE", "/preferences")) == (405, "method_not_allowed")
    r = p.req("la matriz de quien nunca cambió nada", "GET", "/preferences")
    assert r["body"]["categories"]["activity"]["email"] == {"enabled": False, "locked": False}
    r = p.req("un cambio", "PATCH", "/preferences", body={"activity": {"email": True}})
    assert r["body"]["categories"]["activity"]["email"] == {"enabled": True, "locked": False}
    r = p.req("queda guardado", "GET", "/preferences")
    assert r["body"]["categories"]["activity"]["email"]["enabled"] is True
    r = p.req("los ámbitos no se heredan", "GET", "/preferences", [("scope", "acme")])
    assert r["body"]["categories"]["activity"]["email"]["enabled"] is False
    p.req(
        "un cambio en un ámbito",
        "PATCH",
        "/preferences",
        [("scope", "acme")],
        body={"invitations": {"email": False}},
    )
    r = p.req("no toca lo de sin ámbito", "GET", "/preferences")
    assert r["body"]["categories"]["invitations"]["email"]["enabled"] is True
    assert (
        p.req("de otra persona, nada", "GET", "/preferences", who="beto")["body"]["categories"]["activity"][
            "email"
        ]["enabled"]
        is False
    )
    assert p.req("una celda bloqueada", "PATCH", "/preferences", body={"security": {"email": False}})["body"][
        "error"
    ] == {"code": "locked", "category": "security", "channel": "email"}
    assert p.req("un canal no disponible", "PATCH", "/preferences", body={"activity": {"push": True}})[
        "body"
    ]["error"] == {"code": "unavailable", "category": "activity", "channel": "push"}
    assert p.req("una categoría desconocida", "PATCH", "/preferences", body={"nada": {}})["body"][
        "error"
    ] == {"code": "unknown_category", "category": "nada", "channel": None}
    assert p.req(
        "un valor que no es booleano",
        "PATCH",
        "/preferences",
        body={"activity": {"email": "sí"}},
    )["body"]["error"] == {"code": "invalid_value", "category": "activity", "channel": "email"}
    r = p.req(
        "rechazado entero: lo válido tampoco se guarda",
        "PATCH",
        "/preferences",
        body={"activity": {"email": False}, "security": {"email": False}},
    )
    assert r["status"] == 422
    assert (
        p.req("sigue como antes", "GET", "/preferences")["body"]["categories"]["activity"]["email"]["enabled"]
        is True
    )
    for note, kwargs in [
        ("un cuerpo que no es JSON", {"raw": "nope"}),
        ("un cuerpo que no es un objeto", {"body": ["activity"]}),
        ("una categoría que no es un objeto", {"body": {"activity": True}}),
        ("sin cuerpo", {}),
    ]:
        assert code_of(p.req(note, "PATCH", "/preferences", **kwargs)) == (400, "invalid_body"), note
    assert p.req("un pedido vacío es válido", "PATCH", "/preferences", body={})["status"] == 200
    return p.vector("http/preferencias")


def http_horario() -> dict:
    p = Pedidos()
    assert code_of(p.req("sin persona", "GET", "/quiet-hours", who=None)) == (401, "unauthenticated")
    assert code_of(p.req("no es PATCH", "PATCH", "/quiet-hours")) == (405, "method_not_allowed")
    assert p.req("quien nunca definió uno", "GET", "/quiet-hours")["body"] == {"quiet_hours": None}
    h = horario()
    assert p.req("guardar uno", "PUT", "/quiet-hours", body=h)["body"] == {"quiet_hours": h}
    assert p.req("se lee", "GET", "/quiet-hours")["body"] == {"quiet_hours": h}
    assert p.req("es de la persona", "GET", "/quiet-hours", who="beto")["body"] == {"quiet_hours": None}
    otro = horario(start="23:00", enabled=False, timezone="Europe/Madrid")
    assert p.req("reemplazarlo", "PUT", "/quiet-hours", body=otro)["body"] == {"quiet_hours": otro}
    for note, body in [
        ("una hora inválida", horario(start="24:00")),
        ("una clave de más", {**horario(), "days": []}),
        ("una clave de menos", {k: v for k, v in horario().items() if k != "end"}),
        ("no es un objeto", "22:00-08:00"),
    ]:
        assert code_of(p.req(note, "PUT", "/quiet-hours", body=body)) == (422, "invalid_quiet_hours"), note
    assert code_of(
        p.req("una zona que no existe", "PUT", "/quiet-hours", body=horario(timezone="Marte/Olimpo"))
    ) == (
        422,
        "unknown_timezone",
    )
    assert code_of(p.req("un cuerpo que no es JSON", "PUT", "/quiet-hours", raw="{")) == (400, "invalid_body")
    assert p.req("quedó el último válido", "GET", "/quiet-hours")["body"] == {"quiet_hours": otro}
    return p.vector("http/horario")


def vector(vector_id: str, kind: str, cases: list, policy: dict | None = None) -> dict:
    out: dict = {"spec": SPEC, "id": vector_id, "kind": kind}
    if policy is not None:
        out["policy"] = policy
    out["steps" if kind in ("bandeja", "preferencias-guardadas", "silencio-guardado") else "cases"] = cases
    return out


ALL = [
    politica_validacion,
    canales_ejemplo,
    canales_bordes,
    matriz_ejemplo,
    matriz_bordes,
    preferencias_validacion,
    canales_heredados,
    matriz_heredados,
    preferencias_heredados,
    plantilla_render,
    catalogo_validacion,
    aviso_armado,
    bandeja_basico,
    bandeja_duplicados,
    bandeja_paginado,
    bandeja_empates,
    bandeja_ambitos,
    bandeja_marcar_todo,
    bandeja_contador,
    bandeja_personas,
    guardadas_basico,
    guardadas_aislamiento,
    recorrido_basico,
    recorrido_fallas,
    recorrido_duplicados,
    recorrido_rechazos,
    recorrido_ambitos,
    recorrido_disponibles,
    recorrido_disponibles_por_persona,
    recorrido_entregar,
    recorrido_sin_canales,
    recorrido_silencio,
    recorrido_grupos,
    recorrido_miembros,
    canales_configurados,
    silencio_validacion,
    silencio_horario,
    silencio_guardado,
    email_mensaje,
    email_direcciones,
    http_bandeja,
    http_preferencias,
    http_horario,
]


def render(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    expected = {VECTORS / f"{data['id']}.json": render(data) for data in (build() for build in ALL)}
    on_disk = set(VECTORS.rglob("*.json")) if VECTORS.exists() else set()

    if check:
        problems = [
            f"falta o difiere: {p.relative_to(ROOT)}"
            for p, text in expected.items()
            if not p.exists() or p.read_text(encoding="utf-8") != text
        ]
        problems += [f"sobra: {p.relative_to(ROOT)}" for p in sorted(on_disk - set(expected))]
        for line in problems:
            print(line, file=sys.stderr)
        if problems:
            print("Los vectores no coinciden. Corré: python3 tools/gen_vectors.py", file=sys.stderr)
            return 1
        print(f"{len(expected)} vectores en sincronía.")
        return 0

    for stale in on_disk - set(expected):
        stale.unlink()
    for path, text in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    total = sum(len(v.get("cases", v.get("steps"))) for v in map(json.loads, expected.values()))
    print(f"{len(expected)} vectores, {total} casos, en {VECTORS.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
