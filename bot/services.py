"""Consultas y textos que usan varios comandos."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .gamedata import GAME, normalize
from .models import AddonCharacter, KnownRecipe, RecipeCatalog, RecipeName
from .web import Snapshot, WebCharacter, name_key, role_text

__all__ = ["name_key", "describe", "recipe_counts", "missing_recipes", "web_match"]


def describe(character: WebCharacter, with_professions: bool = False, recipes: dict[int, int] | None = None) -> str:
    """'**Nombre Apellido** — Guerrero 60 · 🛡️ Tanque (Protección) ⭐ ✔'."""
    race = GAME.races.get(character.race_key or "")
    text = f"**{character.full_name}** — {race.name + ' ' if race else ''}{character.class_name} {character.level}"
    role = role_text(character)
    if role:
        text += f" · {role}"
    if character.spec:
        text += f" ({character.spec_label})"
    if character.is_main:
        text += " ⭐"
    if character.verified:
        text += " ✔"
    if with_professions and character.professions:
        parts = []
        for prof in character.professions:
            part = prof.name + (f" {prof.skill}" if prof.skill else "")
            if recipes and recipes.get(prof.skill_line):
                part += f" ({recipes[prof.skill_line]} recetas)"
            parts.append(part)
        text += "\n   " + ", ".join(parts)
    return text


def web_match(snapshot: Snapshot, addon_name: str) -> WebCharacter | None:
    """El personaje de la web que corresponde a uno del addon (por nombre y apellido)."""
    return snapshot.find(addon_name)


async def recipe_counts(session: AsyncSession, character: WebCharacter) -> dict[int, int]:
    """Recetas que el addon sabe de un personaje de la web, por profesión: {skill_line: cuántas}."""
    addon = await session.scalar(select(AddonCharacter).where(AddonCharacter.name_key == name_key(character.full_name)))
    if addon is None:
        return {}
    rows = await session.execute(
        select(KnownRecipe.skill_line, func.count()).where(KnownRecipe.character_id == addon.id).group_by(KnownRecipe.skill_line)
    )
    return dict(rows.all())


async def missing_recipes(session: AsyncSession, skill_line: int) -> tuple[list[tuple[int, str | None]], int]:
    """Recetas del catálogo de una profesión que no conoce ningún personaje: ([(id, nombre o None)], total del catálogo)."""
    catalog = set(await session.scalars(select(RecipeCatalog.recipe_id).where(RecipeCatalog.skill_line == skill_line)))
    known = set(await session.scalars(select(KnownRecipe.recipe_id).where(KnownRecipe.recipe_id.in_(catalog)))) if catalog else set()
    missing = catalog - known
    names = {}
    if missing:
        rows = await session.execute(select(RecipeName.recipe_id, RecipeName.name).where(RecipeName.recipe_id.in_(missing)))
        names = dict(rows.all())
    # Primero las que tienen nombre, por orden alfabético; luego las demás por número.
    ordered = sorted(missing, key=lambda i: (names.get(i) is None, normalize(names.get(i) or ""), i))
    return [(i, names.get(i)) for i in ordered], len(catalog)


def profession_name(skill_line: int) -> str:
    known = GAME.profession_by_skill_line(skill_line)
    return known.name if known else f"Profesión {skill_line}"
