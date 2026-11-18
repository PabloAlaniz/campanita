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

import itertools
import json
import re
import sys
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
            if set(cell) - {"default", "locked"} or "default" not in cell:
                return False
            if any(type(value) is not bool for value in cell.values()):
                return False
        if cells.get(INBOX) != {"default": True, "locked": True}:
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


def is_valid_catalog(catalog: object, policy: dict) -> bool:
    if type(catalog) is not dict:
        return False
    for type_id, entry in catalog.items():
        if len(type_id) > 128 or not TYPE_ID.fullmatch(type_id) or type(entry) is not dict:
            return False
        if set(entry) - {"category", "title", "body"} or not {"category", "title"} <= set(entry):
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


def valid_channels(channels: object) -> bool:
    if type(channels) is not list:
        return False
    if any(type(c) is not str or not ID.fullmatch(c) or c == INBOX for c in channels):
        return False
    return len(set(channels)) == len(channels)


class Recorrido:
    def __init__(self, policy: dict, catalog: dict, channels: list[str]) -> None:
        assert is_valid_policy(policy) and is_valid_catalog(catalog, policy) and valid_channels(channels)
        self.policy, self.catalog, self.channels = policy, catalog, channels
        self.store, self.prefs, self.steps = Modelo(), Ajustes(), []

    def save(self, note: str, changes: list[tuple[str, str, bool]], recipient="ana", scope=None) -> None:
        self.prefs.save(note, changes, recipient, scope)
        self.steps.append(self.prefs.steps.pop())

    def notify(self, note: str, event: dict, new_id: str, at_ms: int, **outcomes: str) -> dict:
        """`outcomes` dice qué contesta cada canal en este paso; por omisión, `sent`.
        Además de los tres resultados válidos: `throws` (el canal falla con una
        excepción), `garbage` (devuelve algo que no es un resultado) y `unknown`
        (devuelve un resultado bien formado con un valor que no existe)."""
        expect = self._notify(event, new_id, at_ms, outcomes)
        self.steps.append(
            {
                "note": note,
                "op": "notify",
                "event": event,
                "id": new_id,
                "at_ms": at_ms,
                "outcomes": outcomes,
                "expect": expect,
            }
        )
        return expect

    def _notify(self, event: dict, new_id: str, at_ms: int, outcomes: dict) -> dict:
        try:
            draft = build_draft(self.catalog, event)
        except Fallo as fallo:
            return {"error": {"reason": fallo.reason, "detail": fallo.detail}}
        added = self.store.add(draft, new_id, at_ms)
        if "error" in added:
            return {"error": {"reason": added["error"], "detail": None}}
        if not added["created"]:
            return {**added, "attempted": [], "deliveries": {}}
        saved: dict = {}
        for (who, where, category, channel), enabled in self.prefs.cells.items():
            if who == draft["recipient_id"] and where == draft["scope"]:
                saved.setdefault(category, {})[channel] = enabled
        targets = channels_for(self.policy, draft["category"], saved, self.channels)
        deliveries = {}
        for channel in targets:
            answer = outcomes.get(channel, "sent")
            deliveries[channel] = answer if answer in OUTCOMES else "failed"
        return {**added, "attempted": targets, "deliveries": deliveries}


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


def recorrido_sin_canales() -> dict:
    r = Recorrido(RECORRIDO_POLITICA, RECORRIDO_CATALOGO, [])
    login = {"type": "login.new", "recipient_id": "ana", "payload": {"device": "Firefox"}}
    a = r.notify("sin ningún canal configurado, el aviso se guarda y no se entrega", login, "n1", 1000)
    assert a["created"] and a["attempted"] == [] and a["deliveries"] == {}
    return recorrido_vector("recorrido/sin-canales", r)


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


def vector(vector_id: str, kind: str, cases: list, policy: dict | None = None) -> dict:
    out: dict = {"spec": SPEC, "id": vector_id, "kind": kind}
    if policy is not None:
        out["policy"] = policy
    out["steps" if kind in ("bandeja", "preferencias-guardadas") else "cases"] = cases
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
    recorrido_sin_canales,
    canales_configurados,
    email_mensaje,
    email_direcciones,
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
