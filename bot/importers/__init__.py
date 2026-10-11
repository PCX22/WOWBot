"""Lectores de los SavedVariables de los addons del juego.

Hoy solo hay uno: GuildRecipesDB, el addon de recetas de la hermandad. Los perfiles (clase, spec, rol, nivel,
profesiones) no salen de aquí sino de la web (bot/web.py).
"""
from __future__ import annotations

from . import guildrecipes
from .types import ImportedCharacter, ImportedProfession, ImportResult

__all__ = ["ImportedCharacter", "ImportedProfession", "ImportResult", "read_all"]

# (detecta si el archivo es de este addon, lo lee)
READERS = [
    (guildrecipes.detect, guildrecipes.read),
]


def read_all(variables: dict, guild_name: str | None = None) -> list[ImportResult]:
    return [read(variables, guild_name) for detect, read in READERS if detect(variables)]
