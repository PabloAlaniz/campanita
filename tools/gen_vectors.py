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
# Políticas de prueba
# ---------------------------------------------------------------------------

BANDEJA = {"default": True, "locked": True}

#: La política de un producto real: seguridad no se apaga, las invitaciones y el
#: resumen salen por email salvo que la persona diga que no, y la actividad sólo
#: queda en la bandeja salvo que la persona pida más.
REFERENCIA = {
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
        ("la política de referencia", REFERENCIA, True),
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


def canales_referencia() -> dict:
    """Recorrido completo: cada categoría, cada combinación de overrides sobre
    sus canales de entrega y cada subconjunto de canales disponibles."""
    estados = (None, True, False)
    cases = []
    for category, cells in REFERENCIA.items():
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
                        "expect": channels_for(REFERENCIA, category, overrides, available),
                    }
                )
    return vector("canales/referencia", "canales", cases, REFERENCIA)


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


def matriz_referencia() -> dict:
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
            "expect": matrix_for(REFERENCIA, overrides, available),
        }
        for note, overrides in juegos
        for available in subsets(["email", "push"])
    ]
    return vector("matriz/referencia", "matriz", cases, REFERENCIA)


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
            "expect": patch_result(REFERENCIA, patch, available),
        }
        for note, patch, available in casos
    ]
    return vector("preferencias/validacion", "preferencias", cases, REFERENCIA)


def vector(vector_id: str, kind: str, cases: list, policy: dict | None = None) -> dict:
    out: dict = {"spec": SPEC, "id": vector_id, "kind": kind}
    if policy is not None:
        out["policy"] = policy
    out["cases"] = cases
    return out


ALL = [
    politica_validacion,
    canales_referencia,
    canales_bordes,
    matriz_referencia,
    matriz_bordes,
    preferencias_validacion,
    canales_heredados,
    matriz_heredados,
    preferencias_heredados,
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
