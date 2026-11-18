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


def vector(vector_id: str, kind: str, cases: list, policy: dict | None = None) -> dict:
    out: dict = {"spec": SPEC, "id": vector_id, "kind": kind}
    if policy is not None:
        out["policy"] = policy
    out["cases"] = cases
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
    total = sum(len(json.loads(text)["cases"]) for text in expected.values())
    print(f"{len(expected)} vectores, {total} casos, en {VECTORS.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
