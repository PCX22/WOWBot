"""Lector del addon Guild Professions & Recipes (github.com/Nedyar/GuildProfessionsRecipes).

GuildProfessionsRecipesDB.guilds[nombre].members[GUID] = { t, av, n, p = { [skillLine] = { r, m, o, lk, k } } }
(antes de la versión 0.5.0 la variable se llamaba GuildRecipesDB; el formato es el mismo).

- n: «Nombre Apellido» del personaje; t: hora (Unix) de su último cambio, que solo pone su dueño.
- p: sus profesiones por skillLine: r nivel, m máximo.
- k: sus recetas: una tabla de IDs, false si la profesión no tiene recetas, o (en disco) un texto base64
  empaquetado por Pack.lua (WriteList: el número de recetas y luego las diferencias entre IDs, en LEB128).
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field

from .luasv import as_list

VARIABLES = ("GuildProfessionsRecipesDB", "GuildRecipesDB")
GUID_RE = re.compile(r"^Player-\d+-[0-9A-Fa-f]+$")
MIN_TIME = 1600000000  # como el addon: las horas anteriores a septiembre de 2020 no valen
MAX_RECIPES = 5000      # el catálogo de una profesión, en Data.lua (MAX_CATALOG)
MAX_PROFESSIONS = 10    # Pack.LIMITS.professions
MAX_TEXT = 64           # Pack.LIMITS.text


@dataclass
class Profession:
    skill_line: int
    rank: int
    max_rank: int
    # None: no se sabe qué recetas tiene. Lista vacía: no tiene ninguna.
    recipes: list[int] | None = None


@dataclass
class Character:
    guid: str
    name: str
    t: int
    professions: dict[int, Profession] = field(default_factory=dict)


@dataclass
class AddonData:
    guild: str
    characters: list[Character]
    # Listas de recetas que no se pudieron leer (no deberían quedar con unpack_list).
    unreadable: int = 0


class NotAddonFile(ValueError):
    pass


# Pack.lua ----------------------------------------------------------------------


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def uint(self) -> int:
        value, scale = 0, 1
        for _ in range(8):
            if self.pos >= len(self.data):
                raise ValueError("truncated")
            b = self.data[self.pos]
            self.pos += 1
            if b < 128:
                return value + b * scale
            value += (b - 128) * scale
            scale *= 128
        raise ValueError("malformed number")


def unpack_list(text: str, max_count: int = MAX_RECIPES) -> list[int] | None:
    """Pack.FromBase64 + Pack.ReadList: la lista ordenada de IDs, o None si el texto no es válido."""
    if not isinstance(text, str) or len(text) % 4:
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    reader = _Reader(raw)
    try:
        count = reader.uint()
        if count > max_count:
            return None
        ids, previous = [], 0
        for _ in range(count):
            gap = reader.uint()
            if gap == 0:
                return None  # el addon nunca escribe una lista desordenada o con repetidos
            previous += gap
            ids.append(previous)
    except ValueError:
        return None
    return ids


def pack_list(ids: list[int]) -> str:
    """Pack.WriteList + Pack.ToBase64, para los tests."""
    out = bytearray()

    def uint(value: int) -> None:
        while value >= 128:
            out.append(value % 128 + 128)
            value //= 128
        out.append(value)

    uint(len(ids))
    previous = 0
    for i in sorted(set(ids)):
        uint(i - previous)
        previous = i
    return base64.b64encode(bytes(out)).decode()


# Lectura -------------------------------------------------------------------------


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**31


def _recipes(k) -> tuple[list[int] | None, bool]:
    """(recetas o None si no se sabe, True si venía empaquetada y no se pudo leer)."""
    if k is False:
        return [], False
    if isinstance(k, str):
        ids = unpack_list(k)
        return (ids, False) if ids is not None else (None, True)
    if isinstance(k, dict):
        ids = as_list(k) or list(k.values())
        if all(_is_id(i) for i in ids) and len(ids) <= MAX_RECIPES:
            return sorted(set(ids)), False
    return None, False


def find_db(variables: dict) -> dict:
    for name in VARIABLES:
        if isinstance(variables.get(name), dict):
            return variables[name]
    raise NotAddonFile("El archivo no es del addon Guild Professions & Recipes.")


def pick_guild(guilds: dict, guild_name: str | None) -> tuple[str, dict] | None:
    if guild_name:
        for name, guild in guilds.items():
            if isinstance(guild, dict) and isinstance(name, str) and name.strip().lower() == guild_name.strip().lower():
                return name, guild
        return None
    # Sin nombre configurado: la última hermandad que se visitó con ese PC.
    candidates = [(g.get("seen") if isinstance(g.get("seen"), (int, float)) else 0, name, g) for name, g in guilds.items() if isinstance(g, dict) and isinstance(name, str)]
    if not candidates:
        return None
    _, name, guild = max(candidates, key=lambda c: c[0])
    return name, guild


def read(variables: dict, guild_name: str | None = None) -> AddonData:
    db = find_db(variables)
    guilds = db.get("guilds") if isinstance(db.get("guilds"), dict) else {}
    picked = pick_guild(guilds, guild_name)
    if picked is None:
        raise NotAddonFile(f"El archivo no tiene datos de la hermandad «{guild_name}»." if guild_name else "El archivo no tiene datos de ninguna hermandad todavía.")
    name, guild = picked
    members = guild.get("members") if isinstance(guild.get("members"), dict) else {}
    result = AddonData(guild=name, characters=[])
    for guid, record in members.items():
        if not isinstance(guid, str) or not GUID_RE.match(guid) or not isinstance(record, dict):
            continue
        t = record.get("t")
        if not isinstance(t, (int, float)) or t < MIN_TIME:
            continue  # registros que su dueño nunca fechó: el addon tampoco los comparte
        char_name = record.get("n")
        if not isinstance(char_name, str) or not char_name.strip() or "|" in char_name or len(char_name) > MAX_TEXT:
            continue
        character = Character(guid=guid, name=" ".join(char_name.split()), t=int(t))
        professions = record.get("p") if isinstance(record.get("p"), dict) else {}
        for skill_line, prof in list(professions.items())[:MAX_PROFESSIONS]:
            if not _is_id(skill_line) or not isinstance(prof, dict):
                continue
            rank, max_rank = prof.get("r"), prof.get("m")
            if not isinstance(rank, int) or not isinstance(max_rank, int) or rank < 0 or max_rank < 0:
                continue
            recipes, unreadable = _recipes(prof.get("k"))
            result.unreadable += unreadable
            character.professions[skill_line] = Profession(skill_line, rank, max_rank, recipes)
        result.characters.append(character)
    return result


def professions_payload(data: AddonData, addon_version: str | None = None) -> dict:
    """Lo que se envía a la web (POST /api/addon/professions). De momento, solo las profesiones."""
    return {
        "addonVersion": addon_version,
        "guild": data.guild[:64],
        "characters": [
            {
                "guid": c.guid,
                "name": c.name,
                "t": c.t,
                "professions": [
                    {"skillLine": p.skill_line, "rank": min(p.rank, 1000), "maxRank": min(p.max_rank, 1000)}
                    for p in c.professions.values()
                ],
            }
            for c in data.characters
        ],
    }
