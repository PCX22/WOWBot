"""/profesion ver: quién tiene una profesión, de más a menos nivel. Los niveles están en la web (el
sincronizador del addon los mantiene al día)."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..gamedata import GAME
from ..web import WebUnavailable
from .personajes import PROFESSION_CHOICES, WEB_UNAVAILABLE, send_pages


class Profesiones(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    profesion = app_commands.Group(name="profesion", description="Profesiones de la hermandad")

    @profesion.command(name="ver", description="Quién tiene una profesión, de más a menos nivel")
    @app_commands.choices(profesion=PROFESSION_CHOICES)
    async def show(self, interaction: discord.Interaction, profesion: app_commands.Choice[str]) -> None:
        prof = GAME.professions[profesion.value]
        try:
            snapshot = await self.bot.directory.snapshot()
        except WebUnavailable:
            await interaction.response.send_message(WEB_UNAVAILABLE, ephemeral=True)
            return
        found = [(c, c.profession(prof.skill_line)) for c in snapshot.characters]
        found = sorted(((c, p) for c, p in found if p), key=lambda cp: (-(cp[1].skill or 0), cp[0].full_name))
        if not found:
            await interaction.response.send_message(f"Nadie tiene {prof.name} en la web todavía.", ephemeral=True)
            return
        lines = [
            f"**{c.full_name}** {p.skill or ''}" + (f" · <@{c.discord_id}>" if c.discord_id else "")
            for c, p in found
        ]
        await send_pages(interaction, prof.name, lines, f"{len(found)} personajes", discord.Colour.dark_green())


async def setup(bot) -> None:
    await bot.add_cog(Profesiones(bot))
