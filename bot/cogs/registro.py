"""/registro (solo en el modo local, sin web): cada uno registra su personaje en el bot, o un oficial importa el
canal donde la gente contó su clase y su raza. Cuando llegue la web, los personajes se gestionarán allí."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from ..gamedata import GAME, ROLES, normalize
from ..intencion import Intencion, parse
from ..models import LocalCharacter, LocalProfession
from ..web import name_key

CLASS_CHOICES = [app_commands.Choice(name=c.name, value=c.key) for c in GAME.classes.values()]
PROFESSION_CHOICES = [app_commands.Choice(name=p.name, value=p.key) for p in GAME.professions.values()]
MAX_CHARACTERS = 10
HISTORY_LIMIT = 500


def _matches(current: str, text: str) -> bool:
    return not current or normalize(current) in normalize(text)


async def race_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    class_key = getattr(interaction.namespace, "clase", None)
    races = GAME.races_for_class(class_key) if class_key in GAME.classes else GAME.races.values()
    return [app_commands.Choice(name=f"{r.name} ({r.faction.capitalize()})", value=r.key) for r in races if _matches(current, r.name)][:25]


async def role_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    class_key = getattr(interaction.namespace, "clase", None)
    if class_key not in GAME.classes:
        name = getattr(interaction.namespace, "personaje", None)
        if name:
            async with interaction.client.sessions() as session:
                char = await session.scalar(select(LocalCharacter).where(LocalCharacter.name_key == name_key(name)))
            class_key = char.class_key if char else None
    roles = GAME.classes[class_key].roles if class_key in GAME.classes else tuple(ROLES)
    return [app_commands.Choice(name=ROLES[r], value=r) for r in roles if _matches(current, ROLES[r])]


async def own_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    async with interaction.client.sessions() as session:
        chars = list(await session.scalars(select(LocalCharacter).where(LocalCharacter.discord_id == interaction.user.id)))
    return [app_commands.Choice(name=c.name, value=c.name) for c in chars if _matches(current, c.name)][:25]


async def save_character(session, discord_id: int, name: str, class_key: str, race_key: str | None, role: str,
                         level: int | None = None, professions: list[str] | None = None, main: bool | None = None) -> tuple[LocalCharacter | None, str]:
    """Crea o actualiza un personaje. Devuelve (personaje, problema); el personaje es None si no se pudo."""
    name = " ".join(name.split())[:32]
    existing = await session.scalar(select(LocalCharacter).where(LocalCharacter.name_key == name_key(name)))
    if existing and existing.discord_id != discord_id:
        return None, f"«{existing.name}» ya es de otro miembro"
    own = list(await session.scalars(select(LocalCharacter).where(LocalCharacter.discord_id == discord_id)))
    if existing is None and len(own) >= MAX_CHARACTERS:
        return None, f"máximo {MAX_CHARACTERS} personajes por persona"
    char = existing or LocalCharacter(discord_id=discord_id, name_key=name_key(name), professions=[])
    char.name, char.class_key, char.race_key, char.role = name, class_key, race_key, role
    if level:
        char.level = level
    if existing is None:
        char.level = level or 1
        session.add(char)
    if professions is not None:
        have = {p.key for p in char.professions}
        for key in professions:
            if key not in have:
                char.professions.append(LocalProfession(key=key, skill=None))
    await session.flush()
    if main or not own or (existing is None and not any(c.is_main for c in own)):
        for other in own:
            other.is_main = False
        char.is_main = True
    return char, ""


class ImportView(discord.ui.View):
    """Confirmar lo que se ha entendido del canal antes de guardarlo."""

    def __init__(self, cog: "Registro", author_id: int, found: list[tuple[discord.Member | discord.User, Intencion, str]]) -> None:
        super().__init__(timeout=600)
        self.cog, self.author_id, self.found = cog, author_id, found

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Solo quien lanzó la importación puede confirmarla.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Guardar y poner roles", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Guardando…", view=None)
        saved, problems = 0, []
        async with self.cog.bot.sessions() as session:
            for user, intent, name in self.found:
                char, problem = await save_character(session, user.id, name, intent.class_key, intent.race_key, intent.role or GAME.classes[intent.class_key].roles[0], professions=intent.professions)
                if char:
                    saved += 1
                else:
                    problems.append(f"{user.display_name}: {problem}")
            await session.commit()
        roles = self.cog.bot.get_cog("Roles")
        if roles and interaction.guild:
            for user, _, _ in self.found:
                await roles.update_user(interaction.guild, user.id)
        text = f"Guardados {saved} personajes y puestos sus roles."
        if problems:
            text += "\nSin guardar: " + "; ".join(problems)
        await interaction.edit_original_response(content=text[:2000])

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Importación cancelada; no se ha guardado nada.", view=None, embed=None)


class Registro(commands.GroupCog, group_name="registro", group_description="Tus personajes (mientras no esté la web)"):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _refresh_roles(self, interaction: discord.Interaction, user_id: int | None = None) -> None:
        roles = self.bot.get_cog("Roles")
        if roles and interaction.guild:
            await roles.update_user(interaction.guild, user_id or interaction.user.id)

    async def _own(self, session, interaction: discord.Interaction, name: str) -> LocalCharacter | None:
        char = await session.scalar(select(LocalCharacter).where(LocalCharacter.name_key == name_key(name)))
        if char is None or char.discord_id != interaction.user.id:
            await interaction.response.send_message(f"No tienes ningún personaje llamado «{name}».", ephemeral=True)
            return None
        return char

    @app_commands.command(name="añadir", description="Registra un personaje tuyo")
    @app_commands.describe(nombre="Nombre del personaje (con apellido, si ya lo sabes)", raza="Solo salen las que pueden ser esa clase", rol="Lo que haces en grupo", main="¿Es tu personaje principal?")
    @app_commands.choices(clase=CLASS_CHOICES)
    @app_commands.autocomplete(raza=race_autocomplete, rol=role_autocomplete)
    async def add(self, interaction: discord.Interaction, nombre: app_commands.Range[str, 2, 32], clase: app_commands.Choice[str],
                  raza: str, rol: str, nivel: app_commands.Range[int, 1, 60] | None = None, main: bool | None = None) -> None:
        if not GAME.is_valid_combo(raza, clase.value):
            race = GAME.races.get(raza)
            await interaction.response.send_message(f"Un {race.name if race else raza} no puede ser {clase.name}.", ephemeral=True)
            return
        if rol not in GAME.classes[clase.value].roles:
            await interaction.response.send_message(f"Un {clase.name} no puede ir de {ROLES.get(rol, rol)}.", ephemeral=True)
            return
        async with self.bot.sessions() as session:
            char, problem = await save_character(session, interaction.user.id, nombre, clase.value, raza, rol, nivel, main=main)
            if char is None:
                await interaction.response.send_message(f"No se ha guardado: {problem}.", ephemeral=True)
                return
            await session.commit()
            text = f"Guardado: **{char.name}**, {GAME.races[raza].name} {clase.name}, {ROLES[rol]}" + (" ⭐" if char.is_main else "")
        await interaction.response.send_message(text, ephemeral=True)
        await self._refresh_roles(interaction)

    @app_commands.command(name="editar", description="Cambia el nivel, el rol o la spec de un personaje tuyo")
    @app_commands.autocomplete(personaje=own_autocomplete, rol=role_autocomplete)
    async def edit(self, interaction: discord.Interaction, personaje: str, nivel: app_commands.Range[int, 1, 60] | None = None,
                   rol: str | None = None, spec: app_commands.Range[str, 1, 40] | None = None) -> None:
        async with self.bot.sessions() as session:
            char = await self._own(session, interaction, personaje)
            if char is None:
                return
            if rol is not None:
                if rol not in GAME.classes[char.class_key].roles:
                    await interaction.response.send_message(f"Un {GAME.classes[char.class_key].name} no puede ir de {ROLES.get(rol, rol)}.", ephemeral=True)
                    return
                char.role = rol
            if nivel is not None:
                char.level = nivel
            if spec is not None:
                char.spec = spec
            await session.commit()
        await interaction.response.send_message(f"{char.name} actualizado.", ephemeral=True)
        await self._refresh_roles(interaction)

    @app_commands.command(name="main", description="Elige tu personaje principal")
    @app_commands.autocomplete(personaje=own_autocomplete)
    async def main(self, interaction: discord.Interaction, personaje: str) -> None:
        async with self.bot.sessions() as session:
            char = await self._own(session, interaction, personaje)
            if char is None:
                return
            for other in await session.scalars(select(LocalCharacter).where(LocalCharacter.discord_id == interaction.user.id)):
                other.is_main = other.id == char.id
            await session.commit()
        await interaction.response.send_message(f"⭐ {char.name} es ahora tu main.", ephemeral=True)
        await self._refresh_roles(interaction)

    @app_commands.command(name="borrar", description="Quita un personaje tuyo")
    @app_commands.autocomplete(personaje=own_autocomplete)
    async def delete(self, interaction: discord.Interaction, personaje: str) -> None:
        async with self.bot.sessions() as session:
            char = await self._own(session, interaction, personaje)
            if char is None:
                return
            was_main = char.is_main
            await session.delete(char)
            await session.flush()
            if was_main:
                rest = list(await session.scalars(select(LocalCharacter).where(LocalCharacter.discord_id == interaction.user.id)))
                if rest:
                    rest[0].is_main = True
            await session.commit()
        await interaction.response.send_message(f"«{personaje}» borrado.", ephemeral=True)
        await self._refresh_roles(interaction)

    @app_commands.command(name="profesion", description="Pon o cambia una profesión de un personaje tuyo")
    @app_commands.choices(profesion=PROFESSION_CHOICES)
    @app_commands.autocomplete(personaje=own_autocomplete)
    async def profession(self, interaction: discord.Interaction, personaje: str, profesion: app_commands.Choice[str],
                         nivel: app_commands.Range[int, 1, 300] | None = None) -> None:
        async with self.bot.sessions() as session:
            char = await self._own(session, interaction, personaje)
            if char is None:
                return
            current = next((p for p in char.professions if p.key == profesion.value), None)
            if current is None:
                primaries = [p for p in char.professions if GAME.professions[p.key].primary]
                if GAME.professions[profesion.value].primary and len(primaries) >= 2:
                    await interaction.response.send_message(f"{char.name} ya tiene dos profesiones principales; quita una con /registro quitar-profesion.", ephemeral=True)
                    return
                current = LocalProfession(key=profesion.value)
                char.professions.append(current)
            current.skill = nivel
            await session.commit()
        await interaction.response.send_message(f"{char.name}: {profesion.name}{f' {nivel}' if nivel else ''}.", ephemeral=True)
        await self._refresh_roles(interaction)

    @app_commands.command(name="quitar-profesion", description="Quita una profesión de un personaje tuyo")
    @app_commands.choices(profesion=PROFESSION_CHOICES)
    @app_commands.autocomplete(personaje=own_autocomplete)
    async def remove_profession(self, interaction: discord.Interaction, personaje: str, profesion: app_commands.Choice[str]) -> None:
        async with self.bot.sessions() as session:
            char = await self._own(session, interaction, personaje)
            if char is None:
                return
            char.professions = [p for p in char.professions if p.key != profesion.value]
            await session.commit()
        await interaction.response.send_message(f"{char.name} ya no tiene {profesion.name}.", ephemeral=True)
        await self._refresh_roles(interaction)

    @app_commands.command(name="importar", description="(Oficiales) Lee un canal donde la gente contó su clase y raza")
    @app_commands.describe(canal="El canal con los mensajes", limite="Cuántos mensajes leer, de los más nuevos (por defecto 500)")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def import_channel(self, interaction: discord.Interaction, canal: discord.TextChannel,
                             limite: app_commands.Range[int, 10, 2000] = HISTORY_LIMIT) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not self.bot.intents.message_content:
            await interaction.followup.send(
                "No puedo leer el texto de los mensajes: activa **Message Content Intent** en el portal de Discord "
                "(Bot → Privileged Gateway Intents) y pon `LEER_MENSAJES=1` en el .env del bot.", ephemeral=True)
            return
        latest: dict[int, discord.Message] = {}
        intents: dict[int, Intencion] = {}
        try:
            async for message in canal.history(limit=limite, oldest_first=False):
                if message.author.bot or message.author.id in intents or not message.content:
                    continue
                intent = parse(message.content)
                if intent.ok:
                    intents[message.author.id] = intent
                    latest[message.author.id] = message
                elif message.author.id not in latest:
                    latest[message.author.id] = message  # para enseñar qué no se entendió
        except discord.Forbidden:
            await interaction.followup.send(f"No tengo permiso para leer {canal.mention}.", ephemeral=True)
            return
        found, lines, unknown = [], [], []
        for author_id, message in latest.items():
            author = message.author
            intent = intents.get(author_id)
            if intent is None:
                unknown.append(f"{author.display_name}: «{message.content[:60]}»")
                continue
            name = intent.name or author.display_name
            found.append((author, intent, name))
            warn = f" _({'; '.join(intent.warnings)})_" if intent.warnings else ""
            lines.append(f"**{author.display_name}** → {name}: {intent.summary()}{warn}")
        if not found:
            await interaction.followup.send(f"No he entendido ningún mensaje de {canal.mention}." + (f"\nPor ejemplo: {unknown[0]}" if unknown else ""), ephemeral=True)
            return
        embed = discord.Embed(title=f"Esto es lo que he entendido de #{canal.name}", description="\n".join(lines)[:4000], colour=discord.Colour.dark_gold())
        if unknown:
            embed.add_field(name=f"Sin entender ({len(unknown)})", value="\n".join(unknown)[:1024], inline=False)
        embed.set_footer(text="Los «¿rol?» se guardan con el primer rol de su clase; luego cada uno puede cambiarlo con /registro editar.")
        await interaction.followup.send(embed=embed, view=ImportView(self, interaction.user.id, found), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Registro(bot))
