"""Por qué canales sale un aviso y qué puede cambiar el usuario.

Son funciones puras a propósito: no tocan base de datos ni red, y por eso se
prueban como una tabla de casos (los vectores de `spec/vectors/`).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from ._policy import Policy
from ._types import INBOX, Cell, Change, MatrixCell, Overrides, PatchRejected

_NO_OVERRIDES: Overrides = {}


def resolve_channels(
    policy: Policy,
    category: str,
    overrides: Overrides = _NO_OVERRIDES,
    available: Collection[str] = (),
) -> frozenset[str]:
    """Los canales de entrega por los que sale un aviso de `category`.

    Nunca incluye `inapp`: la notificación guardada ya es la entrega in-app.
    Una categoría que no está en la política no sale por ningún canal.
    """
    cells = policy.get(category)
    if cells is None:
        return frozenset()
    mine = overrides.get(category, {})
    return frozenset(
        channel
        for channel, cell in cells.items()
        if channel != INBOX and channel in available and _enabled(cell, mine, channel)
    )


def effective_matrix(
    policy: Policy,
    overrides: Overrides = _NO_OVERRIDES,
    available: Collection[str] = (),
) -> dict[str, dict[str, MatrixCell]]:
    """La matriz completa que muestra una pantalla de preferencias.

    La interfaz no recalcula la política: la muestra. Un canal que no está
    disponible se informa bloqueado y conserva su `enabled`.
    """
    return {
        category: {
            channel: MatrixCell(
                enabled=_enabled(cell, overrides.get(category, {}), channel),
                locked=cell.locked or (channel != INBOX and channel not in available),
            )
            for channel, cell in cells.items()
        }
        for category, cells in policy.items()
    }


def validate_patch(
    policy: Policy,
    patch: Mapping[str, Mapping[str, object]],
    available: Collection[str] = (),
) -> tuple[Change, ...]:
    """Revisa un cambio de preferencias y devuelve lo que hay que guardar.

    Lanza `PatchRejected` ante la primera celda que no se puede cambiar. Una
    celda bloqueada no se ignora en silencio: si una interfaz la mandó, tiene un
    error y conviene que se entere.
    """
    changes: list[Change] = []
    # `sorted` sobre `str` ordena por punto de código, que es lo que pide el spec.
    for category in sorted(patch):
        cells = policy.get(category)
        if cells is None:
            raise PatchRejected(category, None, "unknown_category")
        for channel in sorted(patch[category]):
            value = patch[category][channel]
            cell = cells.get(channel)
            if cell is None:
                raise PatchRejected(category, channel, "unknown_channel")
            if not isinstance(value, bool):
                raise PatchRejected(category, channel, "invalid_value")
            if cell.locked:
                raise PatchRejected(category, channel, "locked")
            if channel not in available:
                raise PatchRejected(category, channel, "unavailable")
            changes.append(Change(category, channel, value))
    return tuple(changes)


def _enabled(cell: Cell, overrides: Mapping[str, bool], channel: str) -> bool:
    if cell.locked:
        return cell.default
    return overrides.get(channel, cell.default)
