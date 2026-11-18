"""Plantillas: texto con variables `{nombre}`.

Es a propósito lo mínimo: sin formatos, sin expresiones, sin acceso a atributos.
Lo que una plantilla puede hacer es igual en todos los lenguajes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ._types import InvalidTemplate, TemplateError

_VAR = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_SAFE = 2**53 - 1

#: Una plantilla ya separada: tramos de texto y nombres de variable.
Parsed = tuple[tuple[bool, str], ...]


def parse_template(template: object) -> Parsed:
    """Separa la plantilla en tramos `(es_variable, valor)`. Lanza `InvalidTemplate`."""
    if not isinstance(template, str):
        raise InvalidTemplate("la plantilla tiene que ser texto")

    parts: list[tuple[bool, str]] = []
    text: list[str] = []
    i, n = 0, len(template)
    while i < n:
        char = template[i]
        if char == "{":
            if template.startswith("{{", i):
                text.append("{")
                i += 2
                continue
            close = template.find("}", i)
            name = template[i + 1 : close] if close != -1 else ""
            if close == -1 or not _VAR.fullmatch(name):
                raise InvalidTemplate(f"llave sin cerrar o nombre de variable inválido en la posición {i}")
            if text:
                parts.append((False, "".join(text)))
                text = []
            parts.append((True, name))
            i = close + 1
        elif char == "}":
            if not template.startswith("}}", i):
                raise InvalidTemplate(f"llave de cierre suelta en la posición {i}")
            text.append("}")
            i += 2
        else:
            text.append(char)
            i += 1
    if text:
        parts.append((False, "".join(text)))
    return tuple(parts)


def render_parsed(parts: Parsed, payload: Mapping[str, object]) -> str:
    out: list[str] = []
    for is_variable, value in parts:
        if not is_variable:
            out.append(value)
        elif value not in payload:
            raise TemplateError("missing_variable", value)
        else:
            out.append(_show(payload[value], value))
    return "".join(out)


def render_template(template: str, payload: Mapping[str, object]) -> str:
    """Arma el texto. La plantilla se revisa entera antes de mirar el payload.

    Lanza `InvalidTemplate` si la plantilla está mal escrita y `TemplateError`
    si falta una variable o su valor no se puede mostrar.
    """
    return render_parsed(parse_template(template), payload)


def _show(value: object, name: str) -> str:
    if isinstance(value, str):
        return value
    # Enteros en el rango que cualquier lenguaje representa exacto. `True` es
    # un `int` en Python, pero no es un número para una plantilla.
    if isinstance(value, bool):
        raise TemplateError("invalid_variable", name)
    if isinstance(value, int) and abs(value) <= _MAX_SAFE:
        return str(value)
    if isinstance(value, float) and value.is_integer() and abs(value) <= _MAX_SAFE:
        return str(int(value))
    raise TemplateError("invalid_variable", name)
