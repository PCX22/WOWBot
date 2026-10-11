"""Lo que devuelven los lectores de addons."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImportedProfession:
    skill_line: int
    rank: int
    max_rank: int
    # None: no se sabe qué recetas tiene. Lista vacía: sabe que no tiene ninguna.
    recipes: list[int] | None = None


@dataclass
class ImportedCharacter:
    name: str
    guid: str | None = None
    t: int = 0  # cuándo se tomaron los datos (hora del addon)
    professions: dict[int, ImportedProfession] = field(default_factory=dict)


@dataclass
class ImportResult:
    addon: str
    characters: list[ImportedCharacter] = field(default_factory=list)
    # Nombres de recetas que traiga el archivo: {recipe_id: nombre}.
    recipe_names: dict[int, str] = field(default_factory=dict)
    # Todas las recetas de cada profesión que el juego conoce: {skill_line: [recipe_id, ...]}.
    catalog: dict[int, list[int]] = field(default_factory=dict)
    note: str | None = None
