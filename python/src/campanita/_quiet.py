"""No molestar: el horario en el que los canales de entrega no interrumpen a una persona."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from datetime import datetime

_TIME = re.compile(r"[0-9]{2}:[0-9]{2}", re.ASCII)
_ZONE = re.compile(r"[A-Za-z0-9_+/-]{1,64}", re.ASCII)
_HOURS_PER_DAY = 24
_MINUTES_PER_HOUR = 60
_KEYS = frozenset({"enabled", "start", "end", "timezone"})
_DAY = 24 * 60


class InvalidQuietHours(ValueError):
    """El horario de no molestar no cumple el spec."""


@dataclass(frozen=True, slots=True)
class QuietHours:
    """El horario de no molestar de una persona. Se construye con `parse_quiet_hours`."""

    #: Apagado, el horario queda guardado pero no silencia nada.
    enabled: bool
    #: `HH:MM`, en la hora local de la persona. Entra en el horario.
    start: str
    #: `HH:MM`. No entra en el horario.
    end: str
    #: El nombre de la zona horaria de la persona. El núcleo no lo interpreta.
    timezone: str


def parse_quiet_hours(raw: object) -> QuietHours:
    """Valida un horario con forma de JSON. Lanza `InvalidQuietHours` con el primer problema."""
    if not isinstance(raw, Mapping):
        raise InvalidQuietHours("el horario tiene que ser un objeto")
    extra = sorted(str(key) for key in raw.keys() - _KEYS)
    if extra:
        raise InvalidQuietHours(f"clave desconocida `{extra[0]}`")
    for key in ("enabled", "start", "end", "timezone"):
        if key not in raw:
            raise InvalidQuietHours(f"falta `{key}`")
    enabled, start, end, timezone = raw["enabled"], raw["start"], raw["end"], raw["timezone"]
    if not isinstance(enabled, bool):
        raise InvalidQuietHours("`enabled` tiene que ser un booleano")
    for key, value in (("start", start), ("end", end)):
        if _minute_of(value) is None:
            raise InvalidQuietHours(f"`{key}` tiene que ser una hora `HH:MM`, de 00:00 a 23:59")
    if not isinstance(timezone, str) or not _ZONE.fullmatch(timezone):
        raise InvalidQuietHours("`timezone` tiene que ser el nombre de una zona horaria")
    return QuietHours(enabled=enabled, start=start, end=end, timezone=timezone)


def is_quiet(quiet_hours: QuietHours, minute: int) -> bool:
    """Si el minuto local `minute` (de 0 a 1439) cae dentro del horario.

    `start` entra y `end` no. Un horario con `start` mayor que `end` cruza la
    medianoche; uno con los dos iguales está vacío.
    """
    if isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute < _DAY:
        raise ValueError("el minuto tiene que ser un entero de 0 a 1439")
    start, end = _minute_of(quiet_hours.start), _minute_of(quiet_hours.end)
    if start is None or end is None:
        raise ValueError("el horario no salió de parse_quiet_hours()")
    if not quiet_hours.enabled or start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def local_minute(timezone: str, moment: datetime) -> int | None:
    """El minuto del día de `moment` en la zona `timezone`, o `None` si la plataforma no la conoce.

    Es lo único de no molestar que depende de la plataforma: usa su base de zonas horarias.
    """
    try:
        local = moment.astimezone(ZoneInfo(timezone))
    except (KeyError, ValueError, OSError):
        # `ZoneInfoNotFoundError` es un `KeyError`; un nombre con forma rara, un `ValueError`.
        return None
    return local.hour * 60 + local.minute


def _minute_of(text: object) -> int | None:
    if not isinstance(text, str) or not _TIME.fullmatch(text):
        return None
    hours, minutes = int(text[:2]), int(text[3:])
    if hours >= _HOURS_PER_DAY or minutes >= _MINUTES_PER_HOUR:
        return None
    return hours * _MINUTES_PER_HOUR + minutes
