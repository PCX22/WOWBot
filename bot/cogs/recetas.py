"""DESACTIVADO (no está en COGS de bot/main.py): de momento solo se sincronizan las profesiones,
y las recetas, cuando se activen, irán del sincronizador a la web. Se conserva para entonces.

/receta y /profesion: quién sabe hacer qué.

Las recetas las sabe el addon (personajes del addon, en la base de datos del bot); quién es cada personaje lo
sabe la web. Se cruzan por nombre y apellido.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from ..gamedata import GAME, normalize
from ..models import AddonCharacter, AddonProfession, KnownRecipe, RecipeName
from ..services import missing_recipes, web_match
from ..web import Snapshot, WebUnavailable, name_key
from .personajes import PROFESSION_CHOICES, send_pages


def recipe_label(recipe_id: int, name: str | None) -> str:
    return name or f"Receta #{recipe_id}"


def who(snapshot: Snapshot | None, addon_name: str) -> str:
    """'Nombre Apellido · @dueño' si la web lo conoce; si no, el nombre tal como lo trae el addon."""
    character = web_match(snapshot, addon_name) if snapshot else None
    if character is None:
        return addon_name
    return character.full_name + (f" · <@{character.discord_id}>" if character.discord_id else "")


async def recipe_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    wanted = normalize(current)
    if len(wanted) < 2:
        return []
    async with interaction.client.sessions() as session:
        rows = list(
            await session.scalars(
                select(RecipeName).where(RecipeName.name_key.contains(wanted)).order_by(func.length(RecipeName.name)).limit(25)
            )
        )
    return [app_commands.Choice(name=r.name[:100], value=str(r.recipe_id)) for r in rows]


class Recetas(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _snapshot(self) -> Snapshot | None:
        try:
            return await self.bot.directory.snapshot()
        except WebUnavailable:
            return None  # sin la web se enseñan los nombres del addon, sin dueño

    @app_commands.command(name="receta", description="¿Quién de la hermandad tiene esta receta?")
    @app_commands.describe(receta="Nombre de la receta (o su número)")
    @app_commands.autocomplete(receta=recipe_autocomplete)
    async def recipe(self, interaction: discord.Interaction, receta: str) -> None:
        async with self.bot.sessions() as session:
            if receta.strip().isdigit():
                ids = [int(receta)]
            else:
                ids = list(
                    await session.scalars(
                        select(RecipeName.recipe_id).where(RecipeName.name_key.contains(normalize(receta))).limit(10)
                    )
                )
            if not ids:
                total = await session.scalar(select(func.count()).select_from(KnownRecipe))
                hint = "" if total else " Todavía no ha llegado ningún dato del addon de recetas."
                await interaction.response.send_message(f"No encuentro ninguna receta «{receta}».{hint}", ephemeral=True)
                return
            names = {r.recipe_id: r.name for r in await session.scalars(select(RecipeName).where(RecipeName.recipe_id.in_(ids)))}
            rows = (
                await session.execute(
                    select(KnownRecipe.recipe_id, AddonCharacter.name, AddonProfession.rank)
                    .join(AddonCharacter, AddonCharacter.id == KnownRecipe.character_id)
                    .outerjoin(
                        AddonProfession,
                        (AddonProfession.character_id == AddonCharacter.id) & (AddonProfession.skill_line == KnownRecipe.skill_line),
                    )
                    .where(KnownRecipe.recipe_id.in_(ids))
                    .order_by(AddonProfession.rank.desc().nulls_last(), AddonCharacter.name)
                )
            ).all()
        snapshot = await self._snapshot()
        holders: dict[int, list[str]] = {i: [] for i in ids}
        for recipe_id, addon_name, rank in rows:
            holders[recipe_id].append(who(snapshot, addon_name) + (f" ({rank})" if rank else ""))
        title = recipe_label(ids[0], names.get(ids[0])) if receta.strip().isdigit() else receta
        embed = discord.Embed(title=f"🔨 {title}", colour=discord.Colour.dark_green())
        for recipe_id in ids[:10]:
            people = holders[recipe_id]
            value = "\n".join(people[:15]) + (f"\n… y {len(people) - 15} más" if len(people) > 15 else "")
            embed.add_field(name=recipe_label(recipe_id, names.get(recipe_id)), value=value or "Nadie la tiene (que sepamos)", inline=False)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    profesion = app_commands.Group(name="profesion", description="Profesiones de la hermandad")

    @profesion.command(name="ver", description="Quién tiene una profesión, de más a menos nivel")
    @app_commands.choices(profesion=PROFESSION_CHOICES)
    async def profession(self, interaction: discord.Interaction, profesion: app_commands.Choice[str]) -> None:
        prof = GAME.professions[profesion.value]
        snapshot = await self._snapshot()
        async with self.bot.sessions() as session:
            counts = dict(
                (
                    await session.execute(
                        select(AddonCharacter.name_key, func.count())
                        .join(KnownRecipe, KnownRecipe.character_id == AddonCharacter.id)
                        .where(KnownRecipe.skill_line == prof.skill_line)
                        .group_by(AddonCharacter.name_key)
                    )
                ).all()
            )
            addon_rows = (
                await session.execute(
                    select(AddonCharacter.name, AddonCharacter.name_key, AddonProfession.rank)
                    .join(AddonProfession, AddonProfession.character_id == AddonCharacter.id)
                    .where(AddonProfession.skill_line == prof.skill_line)
                )
            ).all()
        addon_rank = {key: rank for _, key, rank in addon_rows}
        entries: list[tuple[int, str]] = []
        in_web: set[str] = set()
        # Primero lo que dice la web; el nivel y las recetas del addon lo completan.
        for character in snapshot.characters if snapshot else []:
            web_prof = character.profession(prof.skill_line)
            key = name_key(character.full_name)
            if web_prof is None and key not in addon_rank:
                continue
            in_web.add(key)
            skill = (web_prof.skill if web_prof else None) or addon_rank.get(key) or 0
            extra = f" · {counts[key]} recetas" if counts.get(key) else ""
            owner = f" · <@{character.discord_id}>" if character.discord_id else ""
            entries.append((skill, f"**{character.full_name}** {skill or ''}{extra}{owner}"))
        # Personajes que el addon conoce pero la web no (aún no tienen cuenta, o se escriben distinto).
        for addon_name, key, rank in addon_rows:
            if key in in_web or (snapshot and web_match(snapshot, addon_name)):
                continue
            extra = f" · {counts[key]} recetas" if counts.get(key) else ""
            entries.append((rank or 0, f"**{addon_name}** {rank or ''}{extra} · _no está en la web_"))
        if not entries:
            await interaction.response.send_message(f"Nadie tiene {prof.name} todavía.", ephemeral=True)
            return
        entries.sort(key=lambda e: -e[0])
        await send_pages(interaction, prof.name, [line for _, line in entries], f"{len(entries)} personajes", discord.Colour.dark_green())

    @profesion.command(name="faltan", description="Recetas de una profesión que no tiene nadie de la hermandad")
    @app_commands.choices(profesion=PROFESSION_CHOICES)
    async def missing(self, interaction: discord.Interaction, profesion: app_commands.Choice[str]) -> None:
        prof = GAME.professions[profesion.value]
        async with self.bot.sessions() as session:
            missing, total = await missing_recipes(session, prof.skill_line)
        if not total:
            await interaction.response.send_message(
                f"Todavía no sé qué recetas de {prof.name} existen. Hace falta que alguien con el addon de recetas "
                "abra esa profesión en el juego y sincronice (y, por ahora, el Pack.lua del addon para leerlo).",
                ephemeral=True,
            )
            return
        if not missing:
            await interaction.response.send_message(f"🎉 La hermandad tiene las {total} recetas de {prof.name} que conocemos.")
            return
        lines = [f"• {recipe_label(recipe_id, name)}" for recipe_id, name in missing]
        footer = f"{len(missing)} de {total} recetas conocidas en el juego no las tiene nadie"
        await send_pages(interaction, f"{prof.name}: recetas que no tiene nadie", lines, footer, discord.Colour.dark_orange())


async def setup(bot) -> None:
    await bot.add_cog(Recetas(bot))
