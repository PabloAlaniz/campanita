"""El catálogo de tipos y el armado del aviso.

Quien emite manda `type` y `payload`, y no sabe nada de categorías ni de textos:
eso lo decide el catálogo, en un solo lugar.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, final

from ._templates import Parsed, parse_template, render_parsed
from ._types import EventRejected, InvalidCatalog, InvalidTemplate, TemplateError

if TYPE_CHECKING:
    from ._policy import Policy

_TYPE_ID = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*")
_MAX_TYPE_ID = 128
_ENTRY_KEYS = frozenset({"category", "title", "body", "audience"})
_AUDIENCES = frozenset({"person", "group"})
_OPTIONAL_TEXT = ("action_url", "scope", "dedupe_key")
_EVENT_KEYS = frozenset({"type", "recipient_id", "payload", *_OPTIONAL_TEXT})
#: Lo que cuenta como espacio para decidir si un título está vacío. Es una
#: lista fija a propósito: "espacio" no significa lo mismo en cada lenguaje.
_BLANK = " \t\n\r"

#: A quién le habla un tipo de aviso.
Audience = Literal["person", "group"]


@dataclass(frozen=True, slots=True)
class TypeSpec:
    """Un tipo de aviso: a qué categoría pertenece y cómo se redacta."""

    category: str
    title: str
    body: str | None = None
    #: `person` le habla a una persona; `group`, a un grupo: sale una sola vez,
    #: por los canales del grupo, y a los miembros les queda una copia.
    audience: Audience = "person"


@dataclass(frozen=True, slots=True)
class Draft:
    """Un aviso armado y validado, antes de guardarse.

    No tiene `id` ni fecha: los pone quien guarda.
    """

    recipient_id: str
    type: str
    category: str
    title: str
    body: str | None
    action_url: str | None
    payload: Mapping[str, object]
    scope: str | None
    dedupe_key: str | None


@final
class Catalog(Mapping[str, TypeSpec]):
    """Un catálogo ya validado contra una política. Se construye con `parse_catalog`."""

    __slots__ = ("_parsed", "_types")

    def __init__(
        self,
        types: Mapping[str, TypeSpec],
        parsed: Mapping[str, tuple[Parsed, Parsed | None]],
        /,
        *,
        _validated: bool = False,
    ) -> None:
        if not _validated:
            raise TypeError("Catalog no se construye a mano: usá parse_catalog()")
        self._types: Mapping[str, TypeSpec] = MappingProxyType(dict(types))
        self._parsed = dict(parsed)

    def __getitem__(self, type_id: str) -> TypeSpec:
        return self._types[type_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._types)

    def __len__(self) -> int:
        return len(self._types)

    def __repr__(self) -> str:
        return f"Catalog({dict(self._types)!r})"

    def _templates(self, type_id: str) -> tuple[Parsed, Parsed | None]:
        return self._parsed[type_id]


def parse_catalog(raw: object, policy: Policy) -> Catalog:
    """Valida un catálogo con forma de JSON contra `policy`.

    Lanza `InvalidCatalog` con la ubicación del primer problema.
    """
    if not isinstance(raw, Mapping):
        raise InvalidCatalog("el catálogo tiene que ser un objeto de tipos")

    types: dict[str, TypeSpec] = {}
    parsed: dict[str, tuple[Parsed, Parsed | None]] = {}
    for type_id, entry in raw.items():
        if not isinstance(type_id, str) or len(type_id) > _MAX_TYPE_ID or not _TYPE_ID.fullmatch(type_id):
            raise InvalidCatalog(f"tipo {type_id!r}: no es un identificador de tipo")
        types[type_id], parsed[type_id] = _parse_entry(type_id, entry, policy)

    return Catalog(types, parsed, _validated=True)


def _parse_entry(
    type_id: str, entry: object, policy: Policy
) -> tuple[TypeSpec, tuple[Parsed, Parsed | None]]:
    """Valida un tipo del catálogo. Los errores salen en el orden que fija el spec."""
    if not isinstance(entry, Mapping):
        raise InvalidCatalog(f"{type_id}: tiene que ser un objeto")
    extra = sorted(str(key) for key in entry.keys() - _ENTRY_KEYS)
    if extra:
        raise InvalidCatalog(f"{type_id}: clave desconocida `{extra[0]}`")

    category = entry.get("category")
    if not isinstance(category, str) or category not in policy:
        raise InvalidCatalog(f"{type_id}: `category` tiene que ser una categoría de la política")
    if "title" not in entry:
        raise InvalidCatalog(f"{type_id}: falta `title`")
    title, body = entry["title"], entry.get("body")
    try:
        title_parts = parse_template(title)
        body_parts = parse_template(body) if "body" in entry else None
    except InvalidTemplate as error:
        raise InvalidCatalog(f"{type_id}: {error}") from error
    if not title_parts:
        raise InvalidCatalog(f"{type_id}: `title` no puede ser vacío")

    audience = entry.get("audience", "person")
    if not isinstance(audience, str) or audience not in _AUDIENCES:
        raise InvalidCatalog(f"{type_id}: `audience` tiene que ser `person` o `group`")

    # parse_template ya rechazó lo que no es texto; esto sólo se lo dice al tipado.
    if not isinstance(title, str) or not (body is None or isinstance(body, str)):
        raise InvalidCatalog(f"{type_id}: `title` y `body` tienen que ser texto")
    spec = TypeSpec(
        category=category, title=title, body=body, audience="group" if audience == "group" else "person"
    )
    return spec, (title_parts, body_parts)


def build_notification(catalog: Catalog, event: Mapping[str, object]) -> Draft:
    """Arma el aviso de un evento, o lanza `EventRejected`.

    El orden de las revisiones es el del spec: el tipo, los campos conocidos,
    los desconocidos, el título y por último el cuerpo.
    """
    type_id = event.get("type")
    if not isinstance(type_id, str) or type_id not in catalog:
        raise EventRejected("unknown_type", type_id if isinstance(type_id, str) else None)

    recipient_id = event.get("recipient_id")
    if not isinstance(recipient_id, str) or not recipient_id:
        raise EventRejected("invalid_field", "recipient_id")
    payload = event.get("payload", {}) if "payload" in event else {}
    if not isinstance(payload, Mapping):
        raise EventRejected("invalid_field", "payload")
    optional: dict[str, str | None] = {}
    for name in _OPTIONAL_TEXT:
        value = event.get(name)
        if value is not None and (not isinstance(value, str) or not value):
            raise EventRejected("invalid_field", name)
        optional[name] = value
    # Un campo que no existe se rechaza: `dedup_key` mal escrito no puede
    # perderse en silencio y dejar pasar duplicados.
    unknown = sorted(str(key) for key in event.keys() - _EVENT_KEYS)
    if unknown:
        raise EventRejected("invalid_field", unknown[0])

    spec = catalog[type_id]
    title_parts, body_parts = catalog._templates(type_id)
    try:
        title = render_parsed(title_parts, payload)
        if not title.strip(_BLANK):
            raise EventRejected("empty_title", None)
        body = None if body_parts is None else render_parsed(body_parts, payload)
    except TemplateError as error:
        raise EventRejected(error.reason, error.name) from error

    return Draft(
        recipient_id=recipient_id,
        type=type_id,
        category=spec.category,
        title=title,
        body=body,
        action_url=optional["action_url"],
        # Una copia: cambiar el evento después no cambia el aviso.
        payload=dict(payload),
        scope=optional["scope"],
        dedupe_key=optional["dedupe_key"],
    )
