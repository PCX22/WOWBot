"""Reglas que no dependen de Discord: fechas escritas por la gente y reparto de plazas de un grupo."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol, Sequence
from zoneinfo import ZoneInfo

from .gamedata import ROLES, normalize

WEEKDAYS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


class WhenError(ValueError):
    pass


def parse_date(text: str, today: date) -> date:
    """'hoy', 'mañana', 'pasado mañana', un día de la semana, 'DD/MM' o 'DD/MM/AAAA'."""
    t = normalize(text)
    if t in ("hoy", ""):
        return today
    if t == "manana":
        return today + timedelta(days=1)
    if t == "pasado manana":
        return today + timedelta(days=2)
    if t in WEEKDAYS:
        ahead = (WEEKDAYS.index(t) - today.weekday()) % 7
        return today + timedelta(days=ahead)
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?", t)
    if not m:
        raise WhenError(f"No entiendo la fecha «{text}». Usa por ejemplo 'hoy', 'viernes' o '24/10'.")
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else today.year
    if year < 100:
        year += 2000
    try:
        result = date(year, month, day)
    except ValueError:
        raise WhenError(f"La fecha «{text}» no existe.") from None
    if not m.group(3) and result < today - timedelta(days=1):
        # «05/01» escrito en diciembre es el año que viene.
        result = date(year + 1, month, day)
    return result


def parse_time(text: str) -> time:
    """'21:30', '21.30', '21h', '21'."""
    t = normalize(text).replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?:[:.h](\d{2}))?h?", t)
    if not m:
        raise WhenError(f"No entiendo la hora «{text}». Usa por ejemplo '21:30'.")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if hour > 23 or minute > 59:
        raise WhenError(f"La hora «{text}» no existe.")
    return time(hour, minute)


def parse_when(date_text: str, time_text: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """La fecha y hora escritas (hora local de la hermandad), en UTC."""
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(tz)
    day = parse_date(date_text, local_now.date())
    at = parse_time(time_text)
    local = datetime.combine(day, at, tzinfo=tz)
    if normalize(date_text) in WEEKDAYS and local < local_now:
        # «viernes» dicho un viernes después de la hora es el de la semana que viene.
        local += timedelta(days=7)
    if local < local_now - timedelta(minutes=5):
        raise WhenError("Esa fecha ya ha pasado.")
    return local.astimezone(timezone.utc)


# Plazas ----------------------------------------------------------------------


class SignupLike(Protocol):
    role: str
    bench: bool
    created_at: datetime


@dataclass
class Roster:
    confirmed: dict[str, list] = field(default_factory=lambda: {role: [] for role in ROLES})
    reserves: list = field(default_factory=list)

    @property
    def confirmed_count(self) -> int:
        return sum(len(v) for v in self.confirmed.values())


def build_roster(signups: Sequence[SignupLike], slots: dict[str, int]) -> Roster:
    """Por cada rol, entran los primeros en apuntarse hasta llenar las plazas; el resto queda en reserva.
    Si alguien se va, el primero de la reserva con ese rol sube solo."""
    roster = Roster()
    for signup in sorted(signups, key=lambda s: s.created_at):
        places = roster.confirmed.setdefault(signup.role, [])
        if not signup.bench and len(places) < slots.get(signup.role, 0):
            places.append(signup)
        else:
            roster.reserves.append(signup)
    return roster


def slots_for(group) -> dict[str, int]:
    return {"tanque": group.tanks, "sanador": group.healers, "dps": group.dps}
