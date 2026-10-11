"""/personaje y /miembros: quién juega qué. Los personajes se gestionan en la web; aquí solo se consultan."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..gamedata import GAME, ROLES
from ..services import describe, recipe_counts
from ..web import WebUnavailable

CLASS_CHOICES = [app_commands.Choice(name=c.name, value=c.key) for c in GAME.classes.values()]
ROLE_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in ROLES.items()]
PROFESSION_CHOICES = [app_commands.Choice(name=p.name, value=p.key) for p in GAME.professions.values()]
MAX_PAGES = 5
WEB_UNAVAILABLE = "Ahora mismo no puedo leer la web de la hermandad. Prueba en un rato."


def characters_url(bot) -> str | None:
    return f"{bot.config.web_url}/members/characters" if bot.config.web_url else None


def manage_hint(bot) -> str:
    if bot.config.local_mode:
        return "Mientras no esté la web, los personajes se registran aquí: /registro añadir."
    url = characters_url(bot)
    return f"Los personajes se gestionan en la web: {url}" if url else "Los personajes se gestionan en la web de la hermandad."


def _paginate(lines: list[str], limit: int) -> list[str]:
    pages, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > limit:
            pages.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        pages.append(current)
    return pages


async def send_pages(interaction: discord.Interaction, title: str, lines: list[str], footer: str, colour: discord.Colour) -> None:
    """Una página por mensaje: un mensaje admite 6000 caracteres de embeds."""
    pages = _paginate(lines, 4000)
    shown = pages[:MAX_PAGES]
    for i, page in enumerate(shown):
        embed = discord.Embed(title=title if i == 0 else None, description=page, colour=colour)
        if i == len(shown) - 1:
            embed.set_footer(text=footer + (" (lista recortada)" if len(pages) > MAX_PAGES else ""))
        send = interaction.followup.send if i else interaction.response.send_message
        await send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


class Personajes(commands.GroupCog, group_name="personaje", group_description="Personajes de la hermandad (de la web)"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="ver", description="Muestra los personajes de un miembro")
    async def show(self, interaction: discord.Interaction, usuario: discord.Member | None = None) -> None:
        user = usuario or interaction.user
        try:
            snapshot = await self.bot.directory.snapshot()
        except WebUnavailable:
            await interaction.response.send_message(WEB_UNAVAILABLE, ephemeral=True)
            return
        chars = snapshot.characters_of(user.id)
        if not chars:
            who = "No tienes" if user == interaction.user else f"{user.display_name} no tiene"
            await interaction.response.send_message(f"{who} personajes registrados. {manage_hint(self.bot)}", ephemeral=True)
            return
        async with self.bot.sessions() as session:
            lines = [describe(c, with_professions=True, recipes=await recipe_counts(session, c)) for c in chars]
        main_class = GAME.classes.get(chars[0].class_key or "")
        embed = discord.Embed(
            title=f"Personajes de {user.display_name}",
            description="\n".join(lines)[:4000],
            colour=main_class.color if main_class else discord.Colour.blurple(),
            url=characters_url(self.bot),
        )
        embed.set_footer(text="⭐ main · ✔ verificado con Battle.net")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="gestionar", description="Dónde añadir o cambiar tus personajes")
    async def manage(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(manage_hint(self.bot), ephemeral=True)


class Miembros(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="miembros", description="Lista los personajes de la hermandad")
    @app_commands.describe(solo_mains="Mostrar solo los personajes principales")
    @app_commands.choices(clase=CLASS_CHOICES, rol=ROLE_CHOICES, profesion=PROFESSION_CHOICES)
    async def members(
        self,
        interaction: discord.Interaction,
        clase: app_commands.Choice[str] | None = None,
        rol: app_commands.Choice[str] | None = None,
        profesion: app_commands.Choice[str] | None = None,
        solo_mains: bool = False,
    ) -> None:
        try:
            snapshot = await self.bot.directory.snapshot()
        except WebUnavailable:
            await interaction.response.send_message(WEB_UNAVAILABLE, ephemeral=True)
            return
        chars = snapshot.characters
        if clase:
            chars = [c for c in chars if c.class_key == clase.value]
        if rol:
            chars = [c for c in chars if c.role == rol.value]
        if solo_mains:
            chars = [c for c in chars if c.is_main]
        if profesion:
            skill_line = GAME.professions[profesion.value].skill_line
            chars = [c for c in chars if c.profession(skill_line)]
            chars.sort(key=lambda c: -(c.profession(skill_line).skill or 0))
        else:
            chars = sorted(chars, key=lambda c: (-c.level, c.full_name))
        filters = [c.name for c in (clase, rol, profesion) if c] + (["solo mains"] if solo_mains else [])
        title = "Miembros" + (f" · {', '.join(filters)}" if filters else "")
        if not chars:
            await interaction.response.send_message(f"{title}: nadie.", ephemeral=True)
            return
        lines = [describe(c, with_professions=bool(profesion)) + (f" · <@{c.discord_id}>" if c.discord_id else "") for c in chars]
        await send_pages(interaction, title, lines, f"{len(chars)} personajes", discord.Colour.dark_gold())


async def setup(bot) -> None:
    await bot.add_cog(Personajes(bot))
    await bot.add_cog(Miembros(bot))
