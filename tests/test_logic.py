from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bot.logic import WhenError, build_roster, parse_date, parse_time, parse_when

MADRID = ZoneInfo("Europe/Madrid")
# Sábado 3 de octubre de 2026, 18:00 en Madrid (16:00 UTC, horario de verano).
NOW = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 3)


def test_parse_date_words():
    assert parse_date("hoy", TODAY) == TODAY
    assert parse_date("Mañana", TODAY) == date(2026, 10, 4)
    assert parse_date("pasado mañana", TODAY) == date(2026, 10, 5)
    assert parse_date("viernes", TODAY) == date(2026, 10, 9)
    assert parse_date("sábado", TODAY) == TODAY


def test_parse_date_numbers():
    assert parse_date("24/10", TODAY) == date(2026, 10, 24)
    assert parse_date("4-11-2026", TODAY) == date(2026, 11, 4)
    assert parse_date("5/1", TODAY) == date(2027, 1, 5)  # enero ya pasó: el año que viene
    with pytest.raises(WhenError):
        parse_date("31/02", TODAY)
    with pytest.raises(WhenError):
        parse_date("cuando sea", TODAY)


def test_parse_time():
    assert parse_time("21:30").hour == 21
    assert parse_time("21.30").minute == 30
    assert parse_time("21h").hour == 21
    assert parse_time("9").hour == 9
    with pytest.raises(WhenError):
        parse_time("25:00")


def test_parse_when_is_utc_and_local():
    starts = parse_when("hoy", "21:00", MADRID, NOW)
    assert starts == datetime(2026, 10, 3, 19, 0, tzinfo=timezone.utc)
    # En noviembre ya es horario de invierno (UTC+1).
    assert parse_when("4/11", "21:00", MADRID, NOW) == datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)


def test_parse_when_past():
    with pytest.raises(WhenError):
        parse_when("hoy", "10:00", MADRID, NOW)
    # «sábado» dicho el sábado pasada la hora es el de la semana que viene.
    assert parse_when("sábado", "10:00", MADRID, NOW).date() == date(2026, 10, 10)


@dataclass(eq=False)
class FakeSignup:
    name: str
    role: str
    created_at: datetime
    bench: bool = False


def signups(*specs):
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    return [FakeSignup(name, role, base + timedelta(minutes=i), bench) for i, (name, role, bench) in enumerate(specs)]


def names(items):
    return [s.name for s in items]


def test_roster_fills_in_order_and_overflows_to_reserve():
    people = signups(("a", "tanque", False), ("b", "tanque", False), ("c", "dps", False), ("d", "sanador", False))
    roster = build_roster(people, {"tanque": 1, "sanador": 1, "dps": 3})
    assert names(roster.confirmed["tanque"]) == ["a"]
    assert names(roster.reserves) == ["b"]
    assert roster.confirmed_count == 3


def test_roster_bench_and_promotion():
    people = signups(("a", "tanque", True), ("b", "tanque", False), ("c", "tanque", False))
    roster = build_roster(people, {"tanque": 1, "sanador": 0, "dps": 0})
    assert names(roster.confirmed["tanque"]) == ["b"]
    assert names(roster.reserves) == ["a", "c"]
    # Si «b» se va, sube el siguiente que no eligió reserva.
    roster = build_roster([people[0], people[2]], {"tanque": 1, "sanador": 0, "dps": 0})
    assert names(roster.confirmed["tanque"]) == ["c"]
