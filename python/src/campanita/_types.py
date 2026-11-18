"""Tipos y errores del núcleo."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

#: La bandeja. Está en la política y en la matriz de preferencias, pero no es un
#: canal de entrega: la notificación guardada ES la entrega in-app.
INBOX = "inapp"

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
