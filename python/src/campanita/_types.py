"""Tipos y errores del núcleo."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeVar, cast

_T = TypeVar("_T")


async def maybe_await(value: _T | Awaitable[_T]) -> _T:
    """El valor de una función que puede ser `async` o no: quien integra elige."""
    if inspect.isawaitable(value):
        return await cast("Awaitable[_T]", value)
    return value


#: La bandeja. Está en la política y en la matriz de preferencias, pero no es un
#: canal de entrega: la notificación guardada ES la entrega in-app.
INBOX = "inapp"

#: Un identificador de categoría o de canal.
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")

#: Las celdas que el usuario cambió: categoría → canal → habilitado.
Overrides = Mapping[str, Mapping[str, bool]]

#: Por qué se rechazó un cambio de preferencias. Son los literales del spec.
RejectionReason = Literal["unknown_category", "unknown_channel", "invalid_value", "locked", "unavailable"]


@dataclass(frozen=True, slots=True)
class Cell:
    """La política de un canal para una categoría."""

    #: Qué pasa si el usuario nunca tocó esta celda.
    default: bool
    #: El usuario no puede cambiarla.
    locked: bool = False
    #: Se entrega aunque la persona esté en su horario de no molestar.
    urgent: bool = False


@dataclass(frozen=True, slots=True)
class MatrixCell:
    """Una celda de la matriz efectiva, lista para mostrar."""

    enabled: bool
    #: No se puede tocar: la política la bloquea o el canal no está disponible.
    locked: bool


@dataclass(frozen=True, slots=True)
class Change:
    """Un cambio de preferencias ya validado, listo para guardar."""

    category: str
    channel: str
    enabled: bool


class InvalidPolicy(ValueError):
    """La política no cumple el spec. Se detecta al cargarla, no al resolver."""


class PatchRejected(ValueError):
    """El cambio de preferencias se rechazó entero. No hay que guardar nada."""

    def __init__(self, category: str, channel: str | None, reason: RejectionReason) -> None:
        where = category if channel is None else f"{category}.{channel}"
        super().__init__(f"cambio de preferencias rechazado en {where}: {_MOTIVOS[reason]}")
        self.category = category
        #: `None` cuando lo que no existe es la categoría.
        self.channel = channel
        self.reason: RejectionReason = reason


_MOTIVOS: dict[RejectionReason, str] = {
    "unknown_category": "la categoría no está en la política",
    "unknown_channel": "el canal no está en esa categoría",
    "invalid_value": "el valor no es un booleano",
    "locked": "la celda está bloqueada",
    "unavailable": "el canal no está disponible",
}


class InvalidTemplate(ValueError):
    """La plantilla está mal escrita: una llave suelta o un nombre de variable inválido."""


#: Por qué no se pudo armar un texto.
TemplateReason = Literal["missing_variable", "invalid_variable"]


class TemplateError(ValueError):
    """Falta una variable o su valor no se puede mostrar."""

    def __init__(self, reason: TemplateReason, name: str) -> None:
        motivo = "falta la variable" if reason == "missing_variable" else "no se puede mostrar el valor de"
        super().__init__(f"{motivo} `{name}`")
        self.reason: TemplateReason = reason
        self.name = name


class InvalidCatalog(ValueError):
    """El catálogo no cumple el spec. Se detecta al cargarlo."""


#: Por qué se rechazó un evento. Son los literales del spec.
EventReason = Literal["unknown_type", "invalid_field", "missing_variable", "invalid_variable", "empty_title"]

_MOTIVOS_EVENTO: dict[EventReason, str] = {
    "unknown_type": "el tipo no está en el catálogo",
    "invalid_field": "campo inválido o desconocido",
    "missing_variable": "falta una variable",
    "invalid_variable": "el valor de una variable no se puede mostrar",
    "empty_title": "el título queda vacío",
}


class EventRejected(ValueError):
    """El evento no se puede convertir en un aviso. No hay que guardar nada."""

    def __init__(self, reason: EventReason, detail: str | None) -> None:
        text = _MOTIVOS_EVENTO[reason]
        super().__init__(f"evento rechazado: {text}" + (f" ({detail})" if detail is not None else ""))
        self.reason: EventReason = reason
        #: El tipo, el campo o la variable del problema, según el motivo.
        self.detail = detail
