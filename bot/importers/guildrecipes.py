"""Lector de GuildRecipesDB, el addon propio de la hermandad (ver addon_ref/Data.lua).

GuildRecipesDB.guilds[nombre].members[GUID] = { t, av, n, p = { [skillLine] = { r, m, o, lk, k } } }
k es la lista de recetas: una tabla de IDs, false si la profesión no tiene recetas, o (en disco)
un texto base64 empaquetado por Pack.lua.

PENDIENTE: el formato de Pack (Pack.Writer/WriteList/ToBase64) no lo tenemos todavía; hasta que
llegue Pack.lua, las listas empaquetadas se cuentan pero no se leen.
"""
from __future__ import annotations

import re

from .luasv import as_list
from .types import ImportedCharacter, ImportedProfession, ImportResult

GUID_RE = re.compile(r"^Player-\d+-[0-9A-Fa-f]+$")
MIN_TIME = 1600000000
MAX_RECIPES = 5000


def detect(variables: dict) -> bool:
    return isinstance(variables.get("GuildRecipesDB"), dict)


def unpack_list(text: str) -> list[int] | None:
    """Port de Pack.FromBase64 + Pack.ReadList. Falta Pack.lua para escribirlo."""
    return None


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**31


def _recipes(k) -> tuple[list[int] | None, bool]:
    """(lista de recetas o None si no se sabe, True si venía empaquetada y no se pudo leer)."""
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


def pick_guild(guilds: dict, guild_name: str | None) -> tuple[str, dict] | None:
    if guild_name:
        for name, guild in guilds.items():
            if isinstance(guild, dict) and name.strip().lower() == guild_name.strip().lower():
                return name, guild
        return None
    # Sin nombre configurado: la última hermandad que se visitó con ese PC.
    candidates = [(g.get("seen") or 0, name, g) for name, g in guilds.items() if isinstance(g, dict)]
    if not candidates:
        return None
    _, name, guild = max(candidates, key=lambda c: c[0] if isinstance(c[0], (int, float)) else 0)
    return name, guild


LOCALES = ("esES", "esMX")


def read_names(cache) -> dict[int, str]:
    """cache.names = { [locale] = { [recipeID] = nombre } }, que el addon apunta al ver cada receta en el juego.
    Así salen también las recetas nuevas de WoW Forever, que no están en ninguna base de datos.
    Primero el español; de lo que no haya en español, el nombre en el idioma que sea."""
    names_by_locale = cache.get("names") if isinstance(cache, dict) and isinstance(cache.get("names"), dict) else {}
    ordered = [l for l in LOCALES if l in names_by_locale] + [l for l in names_by_locale if l not in LOCALES]
    names: dict[int, str] = {}
    for locale in ordered:
        table = names_by_locale[locale]
        if not isinstance(table, dict):
            continue
        for recipe_id, name in table.items():
            if _is_id(recipe_id) and isinstance(name, str) and name.strip() and "|" not in name and recipe_id not in names:
                names[recipe_id] = name.strip()[:128]
    return names


def read_catalog(cache) -> tuple[dict[int, list[int]], int]:
    """cache.catalog = { [skillLine] = todas las recetas de la profesión }: (catálogo, cuántas listas no se pudieron leer)."""
    raw = cache.get("catalog") if isinstance(cache, dict) and isinstance(cache.get("catalog"), dict) else {}
    catalog: dict[int, list[int]] = {}
    packed = 0
    for skill_line, value in raw.items():
        if not _is_id(skill_line):
            continue
        recipes, was_packed = _recipes(value)
        packed += was_packed
        if recipes:
            catalog[skill_line] = recipes
    return catalog, packed


def read(variables: dict, guild_name: str | None = None) -> ImportResult:
    db = variables["GuildRecipesDB"]
    result = ImportResult(addon="GuildRecipes")
    guilds = db.get("guilds") if isinstance(db.get("guilds"), dict) else {}
    picked = pick_guild(guilds, guild_name)
    if picked is None:
        result.note = f"El archivo no tiene datos de la hermandad «{guild_name}»." if guild_name else "El archivo no tiene hermandades."
        return result
    name, guild = picked
    members = guild.get("members") if isinstance(guild.get("members"), dict) else {}
    packed = 0
    for guid, record in members.items():
        if not isinstance(guid, str) or not GUID_RE.match(guid) or not isinstance(record, dict):
            continue
        t = record.get("t")
        if not isinstance(t, (int, float)) or t < MIN_TIME:
            continue
        char_name = record.get("n")
        if not isinstance(char_name, str) or not char_name.strip() or "|" in char_name:
            continue
        character = ImportedCharacter(name=" ".join(char_name.split()), guid=guid, t=int(t))
        professions = record.get("p") if isinstance(record.get("p"), dict) else {}
        for skill_line, prof in professions.items():
            if not _is_id(skill_line) or not isinstance(prof, dict):
                continue
            rank, max_rank = prof.get("r"), prof.get("m")
            if not isinstance(rank, int) or not isinstance(max_rank, int):
                continue
            recipes, was_packed = _recipes(prof.get("k"))
            packed += was_packed
            character.professions[skill_line] = ImportedProfession(skill_line, rank, max_rank, recipes)
        result.characters.append(character)
    result.recipe_names = read_names(db.get("cache"))
    result.catalog, packed_catalogs = read_catalog(db.get("cache"))
    if packed or packed_catalogs:
        result.note = (
            f"{packed + packed_catalogs} listas de recetas vienen empaquetadas y aún no se pueden leer (falta Pack.lua del addon). "
            "Las profesiones y niveles sí se han importado."
        )
    result.addon = f"GuildRecipes ({name})"
    return result
