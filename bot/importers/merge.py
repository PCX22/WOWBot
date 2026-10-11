"""Mete en la base de datos del bot lo que trae el addon de recetas.

- Unos datos más viejos que los ya guardados no pisan a los nuevos: varios miembros pueden subir copias de la
  misma hermandad tomadas en momentos distintos (como Data.Compare en addon_ref/Data.lua).
- Si una copia no sabe las recetas de una profesión (lista sin leer), se conservan las que ya había.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..gamedata import normalize
from ..models import AddonCharacter, AddonProfession, KnownRecipe, RecipeCatalog, RecipeName
from ..web import name_key
from .types import ImportedCharacter, ImportResult


@dataclass
class MergeSummary:
    created: int = 0
    updated: int = 0
    skipped_old: int = 0
    recipes: int = 0
    catalog_new: int = 0
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"{self.created} personajes nuevos", f"{self.updated} actualizados", f"{self.recipes} recetas"]
        if self.catalog_new:
            parts.append(f"{self.catalog_new} recetas nuevas en el catálogo")
        if self.skipped_old:
            parts.append(f"{self.skipped_old} ignorados por ser datos más viejos")
        return ", ".join(parts) + "".join(f"\n• {n}" for n in self.notes)


async def _find(session: AsyncSession, imported: ImportedCharacter) -> AddonCharacter | None:
    if imported.guid:
        found = await session.scalar(select(AddonCharacter).where(AddonCharacter.guid == imported.guid))
        if found:
            return found
    return await session.scalar(select(AddonCharacter).where(AddonCharacter.name_key == name_key(imported.name)))


async def _apply_character(session: AsyncSession, imported: ImportedCharacter, summary: MergeSummary) -> None:
    character = await _find(session, imported)
    if character is None:
        character = AddonCharacter(name=imported.name, name_key=name_key(imported.name), data_t=0, professions=[])
        session.add(character)
        summary.created += 1
    elif imported.t and imported.t < character.data_t:
        summary.skipped_old += 1
        return
    else:
        summary.updated += 1
    character.name = imported.name
    character.name_key = name_key(imported.name)
    if imported.guid:
        character.guid = imported.guid
    if imported.t:
        character.data_t = imported.t
    await session.flush()

    existing = {p.skill_line: p for p in character.professions}
    kept = []
    for skill_line, prof in imported.professions.items():
        row = existing.get(skill_line) or AddonProfession(skill_line=skill_line)
        row.rank, row.max_rank = prof.rank, prof.max_rank
        kept.append(row)
        if prof.recipes is None:
            continue
        await session.execute(
            delete(KnownRecipe).where(KnownRecipe.character_id == character.id, KnownRecipe.skill_line == skill_line)
        )
        for recipe_id in prof.recipes:
            session.add(KnownRecipe(character_id=character.id, recipe_id=recipe_id, skill_line=skill_line))
        summary.recipes += len(prof.recipes)
    # Las profesiones que ya no tiene se van, con sus recetas.
    character.professions = kept
    await session.execute(
        delete(KnownRecipe).where(
            KnownRecipe.character_id == character.id, KnownRecipe.skill_line.not_in(list(imported.professions) or [0])
        )
    )


async def save_recipe_names(session: AsyncSession, names: dict[int, str], skill_lines: dict[int, int] | None = None) -> None:
    for recipe_id, name in names.items():
        row = await session.get(RecipeName, recipe_id)
        if row is None:
            row = RecipeName(recipe_id=recipe_id)
            session.add(row)
        row.name = name[:128]
        row.name_key = normalize(name)[:128]
        if skill_lines and recipe_id in skill_lines:
            row.skill_line = skill_lines[recipe_id]


async def save_catalog(session: AsyncSession, catalog: dict[int, list[int]]) -> int:
    """Añade al catálogo las recetas que no estaban (nunca quita: cada PC solo ve lo que ha abierto)."""
    added = 0
    for skill_line, recipe_ids in catalog.items():
        known = set(await session.scalars(select(RecipeCatalog.recipe_id).where(RecipeCatalog.skill_line == skill_line)))
        for recipe_id in set(recipe_ids) - known:
            session.add(RecipeCatalog(skill_line=skill_line, recipe_id=recipe_id))
            added += 1
    return added


async def apply_results(session: AsyncSession, results: list[ImportResult]) -> MergeSummary:
    summary = MergeSummary()
    for result in results:
        if result.note:
            summary.notes.append(f"{result.addon}: {result.note}")
        for imported in result.characters:
            await _apply_character(session, imported, summary)
        if result.recipe_names:
            await save_recipe_names(session, result.recipe_names)
        if result.catalog:
            summary.catalog_new += await save_catalog(session, result.catalog)
    await session.flush()
    return summary
