"""Roles de Discord automáticos, a partir de la web: clase y rol del main, y profesiones principales de todos tus
personajes. La web no avisa de los cambios, así que se repasan todos cada pocos minutos."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..gamedata import GAME, ROLE_EMOJI, ROLES
from ..web import Snapshot, WebUnavailable

log = logging.getLogger(__name__)

REFRESH_MINUTES = 10
PROFESSION_EMOJI = {
    "alquimia": "⚗️", "herreria": "⚒️", "encantamiento": "✨", "ingenieria": "⚙️", "herboristeria": "🌿",
    "peleteria": "👢", "mineria": "⛏️", "desuello": "🔪", "sastreria": "🧵",
}


def wanted_roles(snapshot: Snapshot, discord_id: int) -> set[str]:
    chars = snapshot.characters_of(discord_id)
    wanted: set[str] = set()
    if not chars:
        return wanted
    main = chars[0]
    cls = GAME.classes.get(main.class_key or "")
    if cls:
        wanted.add(cls.name)
    if main.role:
        wanted.add(ROLES[main.role])
    for char in chars:
        for prof in char.professions:
            if GAME.professions[prof.key].primary:
                wanted.add(prof.name)
    return wanted


class Roles(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.refresh_all.start()

    async def cog_unload(self) -> None:
        self.refresh_all.cancel()

    def _entries(self) -> list[tuple[str, int | None, str | None]]:
        """(nombre, color, emoji) de cada rol automático de las categorías activas en el .env."""
        cfg = self.bot.config
        entries: list[tuple[str, int | None, str | None]] = []
        if cfg.roles_class:
            entries += [(c.name, c.color, None) for c in GAME.classes.values()]
        if cfg.roles_role:
            entries += [(name, None, ROLE_EMOJI[key]) for key, name in ROLES.items()]
        if cfg.roles_profession:
            entries += [(p.name, None, PROFESSION_EMOJI.get(p.key)) for p in GAME.professions.values() if p.primary]
        return entries

    @staticmethod
    def _icons(guild: discord.Guild) -> bool:
        """Los iconos de rol de verdad solo existen en servidores con nivel 2 de mejoras."""
        return "ROLE_ICONS" in guild.features

    def _display(self, guild: discord.Guild, name: str, emoji: str | None) -> str:
        return f"{emoji} {name}" if emoji and not self._icons(guild) else name

    def _existing(self, roles, guild: discord.Guild, name: str, emoji: str | None) -> discord.Role | None:
        """El rol, se llame «🛡️ Tanque» o «Tanque» (por si se creó antes de tener iconos, o al revés)."""
        return discord.utils.get(roles, name=self._display(guild, name, emoji)) or discord.utils.get(roles, name=name)

    async def _role(self, guild: discord.Guild, name: str, color: int | None, emoji: str | None) -> tuple[discord.Role | None, bool]:
        """(el rol, si se acaba de crear)."""
        role = self._existing(guild.roles, guild, name, emoji)
        if role:
            return role, False
        # El icono de rol no admite el selector de variante (U+FE0F) que llevan emojis como 🛡️.
        extra = {"display_icon": emoji.replace(chr(0xFE0F), "")} if emoji and self._icons(guild) else {}
        try:
            role = await guild.create_role(
                name=self._display(guild, name, emoji),
                colour=discord.Colour(color) if color is not None else discord.Colour.default(),
                mentionable=True,
                reason="Rol automático del bot de la hermandad",
                **extra,
            )
            return role, True
        except discord.Forbidden:
            log.warning("Sin permiso para crear el rol %s (falta «Gestionar roles»)", name)
            return None, False

    async def update_member(self, member: discord.Member, snapshot: Snapshot) -> bool:
        """Pone y quita los roles automáticos de un miembro. Devuelve True si cambió algo."""
        if member.bot:
            return False
        wanted = wanted_roles(snapshot, member.id)
        to_add, to_remove = [], []
        for name, color, emoji in self._entries():
            has = self._existing(member.roles, member.guild, name, emoji)
            if name in wanted and not has:
                role, _ = await self._role(member.guild, name, color, emoji)
                if role:
                    to_add.append(role)
            elif name not in wanted and has:
                to_remove.append(has)
        try:
            if to_add:
                await member.add_roles(*to_add, reason="Personajes de la hermandad")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Personajes de la hermandad")
        except discord.Forbidden:
            log.warning("Sin permiso para cambiar los roles de %s: el rol del bot debe estar por encima", member)
            return False
        return bool(to_add or to_remove)

    async def create_all(self, guild: discord.Guild) -> tuple[int, int]:
        """Crea los roles que falten de las categorías activas: (creados, que ya estaban)."""
        created = existing = 0
        for name, color, emoji in self._entries():
            role, new = await self._role(guild, name, color, emoji)
            created += new
            existing += bool(role) and not new
        return created, existing

    def _guild(self) -> discord.Guild | None:
        if self.bot.config.guild_id:
            return self.bot.get_guild(self.bot.config.guild_id)
        return self.bot.guilds[0] if self.bot.guilds else None

    async def update_all(self, guild: discord.Guild) -> int:
        snapshot = await self.bot.directory.snapshot(fresh=True)
        changed = 0
        for member in guild.members:
            changed += await self.update_member(member, snapshot)
        return changed

    @tasks.loop(minutes=REFRESH_MINUTES)
    async def refresh_all(self) -> None:
        guild = self._guild()
        if guild is None:
            return
        try:
            changed = await self.update_all(guild)
        except WebUnavailable as e:
            log.warning("Roles sin repasar: %s", e)
            return
        if changed:
            log.info("Roles actualizados en %d miembros", changed)

    @refresh_all.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            await self.update_member(member, await self.bot.directory.snapshot())
        except WebUnavailable:
            pass

    @app_commands.command(name="roles-crear", description="(Oficiales) Crea los roles de clase, función y profesión, y los reparte")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def create_roles(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        created, existing = await self.create_all(interaction.guild)
        try:
            changed = await self.update_all(interaction.guild)
        except WebUnavailable:
            changed = 0
        icons = "con icono" if self._icons(interaction.guild) else "con el emoji delante del nombre (el servidor no tiene iconos de rol: hacen falta 2 mejoras de nivel)"
        await interaction.followup.send(
            f"Roles creados: {created} (ya estaban: {existing}), {icons}. Repartidos a {changed} miembros.\n"
            "Recuerda: el rol del bot tiene que estar por encima de estos en Ajustes del servidor → Roles.",
            ephemeral=True,
        )

    @app_commands.command(name="roles-sincronizar", description="(Oficiales) Recalcula ya los roles automáticos de todos")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def sync_all(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            changed = await self.update_all(interaction.guild)
        except WebUnavailable as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await interaction.followup.send(f"Roles revisados; cambiados en {changed} miembros.", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Roles(bot))
