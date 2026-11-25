"""La política: qué pasa por defecto en cada categoría y canal, y qué se puede cambiar."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import final

from ._types import ID, INBOX, Cell, InvalidPolicy

_CELL_KEYS = frozenset({"default", "locked", "urgent"})


@final
class Policy(Mapping[str, Mapping[str, Cell]]):
    """Una política ya validada e inmutable: categoría → canal → celda.

    Se construye con `parse_policy`. Tenerla en la mano garantiza las reglas del
    spec, así que el resto del núcleo no las vuelve a revisar.
    """

    __slots__ = ("_categories",)

    def __init__(self, categories: Mapping[str, Mapping[str, Cell]], /, *, _validated: bool = False) -> None:
        if not _validated:
            raise TypeError("Policy no se construye a mano: usá parse_policy()")
        self._categories: Mapping[str, Mapping[str, Cell]] = MappingProxyType(
            {category: MappingProxyType(dict(cells)) for category, cells in categories.items()}
        )

    def __getitem__(self, category: str) -> Mapping[str, Cell]:
        return self._categories[category]

    def __iter__(self) -> Iterator[str]:
        return iter(self._categories)

    def __len__(self) -> int:
        return len(self._categories)

    def __repr__(self) -> str:
        plain = {category: dict(cells) for category, cells in self._categories.items()}
        return f"Policy({plain!r})"


def parse_policy(raw: object) -> Policy:
    """Valida una política con forma de JSON y la devuelve inmutable.

    Lanza `InvalidPolicy` con la ubicación del primer problema.
    """
    if not isinstance(raw, Mapping):
        raise InvalidPolicy("la política tiene que ser un objeto de categorías")
    if not raw:
        raise InvalidPolicy("la política no tiene categorías")

    categories: dict[str, dict[str, Cell]] = {}
    for category, cells in raw.items():
        _check_id(category, "categoría")
        if not isinstance(cells, Mapping):
            raise InvalidPolicy(f"{category}: tiene que ser un objeto de canales")

        parsed: dict[str, Cell] = {}
        for channel, cell in cells.items():
            _check_id(channel, f"{category}: canal")
            parsed[channel] = _parse_cell(cell, f"{category}.{channel}")

        # La bandeja siempre recibe: no es un canal opcional, es donde la
        # notificación vive.
        inbox = parsed.get(INBOX)
        if inbox is None or not inbox.default or not inbox.locked:
            raise InvalidPolicy(f"{category}: falta `{INBOX}` con `default: true` y `locked: true`")
        categories[category] = parsed

    return Policy(categories, _validated=True)


def _check_id(value: object, what: str) -> None:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise InvalidPolicy(
            f"{what} {value!r}: no es un identificador ([a-z][a-z0-9_-]*, hasta 64 caracteres)"
        )


def _parse_cell(cell: object, where: str) -> Cell:
    if not isinstance(cell, Mapping):
        raise InvalidPolicy(f"{where}: la celda tiene que ser un objeto")
    extra = sorted(str(key) for key in cell.keys() - _CELL_KEYS)
    if extra:
        raise InvalidPolicy(f"{where}: clave desconocida `{extra[0]}`")
    if "default" not in cell:
        raise InvalidPolicy(f"{where}: falta `default`")
    default, locked, urgent = cell["default"], cell.get("locked", False), cell.get("urgent", False)
    # `isinstance(1, bool)` es False, que es lo que queremos: 1 no es un booleano.
    if not isinstance(default, bool):
        raise InvalidPolicy(f"{where}: `default` tiene que ser un booleano")
    if not isinstance(locked, bool):
        raise InvalidPolicy(f"{where}: `locked` tiene que ser un booleano")
    if not isinstance(urgent, bool):
        raise InvalidPolicy(f"{where}: `urgent` tiene que ser un booleano")
    return Cell(default=default, locked=locked, urgent=urgent)
