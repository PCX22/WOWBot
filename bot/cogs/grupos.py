"""/grupo y /lfg: raids y mazmorras con plazas por rol, reservas y recordatorio.

Los grupos se guardan en la web (bot/web_api.py); el bot solo pinta los mensajes y recoge los botones. La web
decide quién puede hacer qué, así que sus errores se enseñan tal cual a quien pulsó.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..gamedata import GAME, ROLE_EMOJI, ROLES, Instance, normalize
from ..logic import WhenError, build_roster, parse_when, slots_for
from ..models import utcnow
from ..permisos import can_create_raid
from ..web import WebCharacter, WebUnavailable
from ..web_api import Group, GroupSignup, WebApiError

log = logging.getLogger(__name__)

KIND_CHOICES = [app_commands.Choice(name="Raid", value="raid"), app_commands.Choice(name="Mazmorra", value="mazmorra")]
ROLE_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in ROLES.items()]
KIND_STYLE = {"raid": ("🐉", discord.Colour.dark_red()), "mazmorra": ("🗝️", discord.Colour.dark_teal()), "lfg": ("📣", discord.Colour.gold())}
STATUS_TEXT = {"cerrado": "🔒 Inscripciones cerradas", "cancelado": "❌ Cancelado", "terminado": "✅ Terminado"}
ACTIVE = ("abierto", "cerrado")
# Cuánto después de empezar se da un grupo por terminado (lo mismo que FINISH_AFTER_MS en la web).
FINISH_AFTER = timedelta(hours=5)
GROUP_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
NO_CHARACTERS = "No tienes ningún personaje que pueda ir de {role}. {hint}"


def class_label(class_key: str | None) -> str:
    cls = GAME.classes.get(class_key or "")
    return cls.name if cls else "¿Clase?"


def signup_label(signup: GroupSignup) -> str:
    return f"{signup.character_name} ({class_label(signup.class_key)})"


def candidates(chars: list[WebCharacter], role: str, min_level: int = 0) -> list[WebCharacter]:
    """Personajes con los que alguien puede ir de ese rol: primero los que lo llevan en la web, luego por nivel."""
    able = [c for c in chars if c.can_play(role)]
    return sorted(able, key=lambda c: (c.role != role, c.level < min_level, not c.is_main, -c.level))


def manage_hint(bot) -> str:
    if bot.config.local_mode:
        return "Regístralo con /registro añadir."
    url = f"{bot.config.web_url}/members/characters" if bot.config.web_url else None
    return f"Añádelo en la web: {url}" if url else "Añádelo en la web de la hermandad."


def _field_lines(lines: list[str], limit: int = 1024) -> str:
    text = ""
    for i, line in enumerate(lines):
        extra = ("\n" if text else "") + line
        if len(text) + len(extra) > limit - 20:
            return text + f"\n… y {len(lines) - i} más"
        text += extra
    return text or "—"


def render(group: Group) -> discord.Embed:
    instance = GAME.instances.get(group.instance_key)
    emoji, colour = KIND_STYLE.get(group.kind, ("", discord.Colour.default()))
    starts = int(group.starts_at.timestamp())
    leader = f"<@{group.leader_id}>" if group.leader_id else "alguien que ya no está"
    if group.kind == "lfg":
        lines = [f"Busca grupo <t:{starts}:R> · lo abrió {leader}"]
        if group.expires_at:
            lines.append(f"Caduca <t:{int(group.expires_at.timestamp())}:R>")
    else:
        lines = [f"📅 <t:{starts}:F> (<t:{starts}:R>)", f"👑 Líder: {leader}"]
    if group.status in STATUS_TEXT:
        lines.insert(0, f"**{STATUS_TEXT[group.status]}**")
    if group.notes:
        lines.append(f"\n{group.notes}")
    embed = discord.Embed(title=f"{emoji} {group.instance_name}", description="\n".join(lines), colour=colour)

    slots = slots_for(group)
    roster = build_roster(group.signups, slots)
    for role, name in ROLES.items():
        people = roster.confirmed.get(role, [])
        embed.add_field(
            name=f"{ROLE_EMOJI[role]} {name} ({len(people)}/{slots[role]})",
            value=_field_lines([f"{i}. {signup_label(s)}" for i, s in enumerate(people, 1)]),
            inline=True,
        )
    if roster.reserves:
        embed.add_field(
            name=f"🪑 Reserva ({len(roster.reserves)})",
            value=_field_lines([f"{signup_label(s)} · {ROLES.get(s.role, s.role)}" for s in roster.reserves]),
            inline=False,
        )
    classes = Counter(class_label(s.class_key) for row in roster.confirmed.values() for s in row)
    if classes:
        embed.add_field(name="Clases", value=" · ".join(f"{name} {n}" for name, n in classes.most_common()), inline=False)
    footer = f"{roster.confirmed_count}/{sum(slots.values())}"
    if instance and instance.levels[0] != instance.levels[1]:
        footer += f" · niveles {instance.levels[0]}-{instance.levels[1]}"
    embed.set_footer(text=footer + " · también en la web, en Grupos")
    return embed


def make_view(group: Group) -> discord.ui.View | None:
    if group.status != "abierto":
        return None
    view = discord.ui.View(timeout=None)
    for role in ROLES:
        view.add_item(GroupButton(group.id, "join", role))
    view.add_item(GroupButton(group.id, "bench"))
    view.add_item(GroupButton(group.id, "leave"))
    return view


async def refresh_message(bot, group: Group) -> None:
    """Vuelve a pintar el mensaje del grupo."""
    if not group.channel_id or not group.message_id:
        return
    channel = bot.get_channel(group.channel_id)
    if channel is None:
        return
    try:
        await channel.get_partial_message(group.message_id).edit(embed=render(group), view=make_view(group))
    except discord.NotFound:
        log.info("El mensaje del grupo %s ya no existe", group.id)
    except discord.HTTPException as e:
        log.warning("No pude actualizar el grupo %s: %s", group.id, e)


def due(groups: list[Group], now: datetime, reminder_minutes: int) -> tuple[list[Group], list[Group]]:
    """(grupos a los que toca avisar, grupos que ya han terminado)."""
    remind_at = now + timedelta(minutes=reminder_minutes)
    to_remind, to_finish = [], []
    for g in groups:
        if g.status not in ACTIVE:
            continue
        if g.kind == "lfg":
            if g.expires_at and g.expires_at <= now:
                to_finish.append(g)
            continue
        if g.starts_at <= now - FINISH_AFTER:
            to_finish.append(g)
        elif not g.reminded and g.starts_at <= remind_at:
            to_remind.append(g)
    return to_remind, to_finish


async def sign_up(bot, group_id: str, user_id: int, character_id: str, role: str, bench: bool = False) -> str:
    """Apunta (o cambia) a alguien. Devuelve el texto para responderle."""
    try:
        group = await bot.web_api.sign_up(group_id, user_id, character_id, role, bench)
    except WebApiError as e:
        return str(e)
    await refresh_message(bot, group)
    mine = next((s for s in group.signups if s.discord_id == user_id), None)
    roster = build_roster(group.signups, slots_for(group))
    where = "en **reserva**" if mine in roster.reserves else "con plaza"
    name = mine.character_name if mine else "tu personaje"
    return f"Apuntado con {name} como {ROLES[role]} {where}."


class CharacterPick(discord.ui.Select):
    def __init__(self, group_id: str, role: str, characters: list[WebCharacter]) -> None:
        self.group_id = group_id
        self.role = role
        options = [
            discord.SelectOption(label=c.full_name[:100], value=c.id, description=f"{c.class_name} {c.level}{' · main' if c.is_main else ''}")
            for c in characters[:25]
        ]
        super().__init__(placeholder="¿Con qué personaje vas?", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        text = await sign_up(interaction.client, self.group_id, interaction.user.id, self.values[0], self.role)
        await interaction.edit_original_response(content=text, view=None)


class GroupButton(discord.ui.DynamicItem[discord.ui.Button], template=rf"grp:(?P<action>join|bench|leave):(?P<gid>{GROUP_ID})(?::(?P<role>[a-z]+))?"):
    """Botones del mensaje de un grupo; siguen funcionando aunque se reinicie el bot."""

    def __init__(self, group_id: str, action: str, role: str | None = None) -> None:
        self.group_id = group_id
        self.action = action
        self.role = role
        if action == "join":
            button = discord.ui.Button(label=ROLES[role], emoji=ROLE_EMOJI[role], style=discord.ButtonStyle.primary, custom_id=f"grp:join:{group_id}:{role}")
        elif action == "bench":
            button = discord.ui.Button(label="Reserva", emoji="🪑", style=discord.ButtonStyle.secondary, custom_id=f"grp:bench:{group_id}")
        else:
            button = discord.ui.Button(label="Salir", style=discord.ButtonStyle.danger, custom_id=f"grp:leave:{group_id}")
        super().__init__(button)

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str]):
        role = match["role"]
        if match["action"] == "join" and role not in ROLES:
            raise ValueError(role)
        return cls(match["gid"], match["action"], role)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action == "join":
            await self._join(interaction)
            return
        bot = interaction.client
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.action == "leave":
                group = await bot.web_api.remove(self.group_id, interaction.user.id)
                text = "Te has desapuntado."
            else:
                group = await bot.web_api.set_bench(self.group_id, interaction.user.id)
                mine = next((s for s in group.signups if s.discord_id == interaction.user.id), None)
                text = "Pasas a reserva." if mine and mine.bench else "Ya no estás en reserva por elección propia."
        except WebApiError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await refresh_message(bot, group)
        await interaction.followup.send(text, ephemeral=True)

    async def _join(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        try:
            snapshot = await bot.directory.snapshot()
        except WebUnavailable:
            await interaction.response.send_message("Ahora mismo no puedo leer la web de la hermandad.", ephemeral=True)
            return
        chars = candidates(snapshot.characters_of(interaction.user.id), self.role)
        if not chars:
            await interaction.response.send_message(NO_CHARACTERS.format(role=ROLES[self.role], hint=manage_hint(bot)), ephemeral=True)
            return
        if len(chars) == 1:
            await interaction.response.defer(ephemeral=True, thinking=True)
            text = await sign_up(bot, self.group_id, interaction.user.id, chars[0].id, self.role)
            await interaction.followup.send(text, ephemeral=True)
            return
        view = discord.ui.View(timeout=120)
        view.add_item(CharacterPick(self.group_id, self.role, chars))
        await interaction.response.send_message(view=view, ephemeral=True)


# Autocompletados -----------------------------------------------------------------


async def instance_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    kind = getattr(interaction.namespace, "tipo", None)
    found = GAME.search_instances(current, kind if kind in ("raid", "mazmorra") else None)
    return [app_commands.Choice(name=f"{i.name} ({i.size})", value=i.key) for i in found][:25]


async def managed_group_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    cog: Grupos = interaction.client.get_cog("Grupos")
    groups = await cog.active_groups()
    choices = []
    for g in groups:
        if g.kind == "lfg":
            continue
        local = g.starts_at.astimezone(interaction.client.config.timezone)
        label = f"{g.instance_name} — {local:%d/%m %H:%M}"
        if not current or normalize(current) in normalize(label):
            choices.append(app_commands.Choice(name=label[:100], value=g.id))
    return choices[:25]


class Grupos(commands.GroupCog, group_name="grupo", group_description="Raids y mazmorras"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._active: tuple[float, list[Group]] = (0.0, [])

    async def cog_load(self) -> None:
        self.housekeeping.start()

    async def cog_unload(self) -> None:
        self.housekeeping.cancel()

    async def active_groups(self, max_age: float = 30) -> list[Group]:
        """Los grupos en marcha, con una copia de 30 s para los autocompletados."""
        loaded_at, groups = self._active
        if time.monotonic() - loaded_at > max_age:
            try:
                groups = await self.bot.web_api.list_groups()
                self._active = (time.monotonic(), groups)
            except WebApiError:
                pass
        return groups

    def _channel_for(self, interaction: discord.Interaction, kind: str):
        cfg = self.bot.config
        channel_id = {"raid": cfg.raid_channel_id, "mazmorra": cfg.dungeon_channel_id, "lfg": cfg.lfg_channel_id}[kind]
        return (self.bot.get_channel(channel_id) if channel_id else None) or interaction.channel

    async def post(self, interaction: discord.Interaction, group: Group) -> discord.Message:
        """Publica el mensaje del grupo y le dice a la web dónde está."""
        channel = self._channel_for(interaction, group.kind)
        message = await channel.send(embed=render(group), view=make_view(group))
        try:
            await self.bot.web_api.update_group(group.id, channel_id=channel.id, message_id=message.id)
        except WebApiError as e:
            log.warning("No pude guardar en la web el mensaje del grupo %s: %s", group.id, e)
        self._active = (0.0, [])
        return message

    @app_commands.command(name="crear", description="Organiza una raid o una mazmorra")
    @app_commands.describe(
        tipo="Raid o mazmorra",
        instancia="Dónde vais",
        fecha="hoy, mañana, viernes, 24/10…",
        hora="Hora de la hermandad, p. ej. 21:30",
        notas="Requisitos, punto de encuentro, consumibles…",
        tanques="Plazas de tanque (si no, las normales del tamaño)",
        sanadores="Plazas de sanador",
        dps="Plazas de DPS",
    )
    @app_commands.choices(tipo=KIND_CHOICES)
    @app_commands.autocomplete(instancia=instance_autocomplete)
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
        tipo: app_commands.Choice[str],
        instancia: str,
        fecha: str,
        hora: str,
        notas: app_commands.Range[str, 0, 500] | None = None,
        tanques: app_commands.Range[int, 0, 40] | None = None,
        sanadores: app_commands.Range[int, 0, 40] | None = None,
        dps: app_commands.Range[int, 0, 40] | None = None,
    ) -> None:
        instance = GAME.find_instance(instancia)
        if instance is None:
            await interaction.response.send_message(f"No conozco la instancia «{instancia}».", ephemeral=True)
            return
        if instance.kind == "raid" and not can_create_raid(interaction.user, self.bot.config):
            roles = ", ".join(self.bot.config.raid_creator_roles)
            await interaction.response.send_message(
                f"Las raids solo las pueden crear: {roles} (y los oficiales). Las mazmorras y /lfg, cualquiera.", ephemeral=True
            )
            return
        try:
            starts = parse_when(fecha, hora, self.bot.config.timezone)
        except WhenError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        slots = {
            "tanque": tanques if tanques is not None else instance.slots["tanque"],
            "sanador": sanadores if sanadores is not None else instance.slots["sanador"],
            "dps": dps if dps is not None else instance.slots["dps"],
        }
        if not 1 <= sum(slots.values()) <= 40:
            await interaction.response.send_message("El grupo tiene que tener entre 1 y 40 plazas.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            group = await self.bot.web_api.create_group(
                interaction.user.id, kind=instance.kind, instance_key=instance.key, instance_name=instance.name,
                starts_at=starts, expires_at=None, tanks=slots["tanque"], healers=slots["sanador"], dps=slots["dps"], notes=notas or "",
            )
        except WebApiError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        message = await self.post(interaction, group)
        await interaction.followup.send(f"Grupo creado: {message.jump_url}", ephemeral=True)

    async def _manage(self, interaction: discord.Interaction, grupo: str, action, done: str) -> Group | None:
        """Una acción de líder: la web comprueba que lo es (o que es oficial)."""
        if not re.fullmatch(GROUP_ID, grupo):
            await interaction.response.send_message("Elige el grupo de la lista.", ephemeral=True)
            return None
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            group = await action(grupo)
        except WebApiError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return None
        self._active = (0.0, [])
        await refresh_message(self.bot, group)
        await interaction.followup.send(done, ephemeral=True)
        return group

    @app_commands.command(name="cerrar", description="(Líder) Cierra las inscripciones")
    @app_commands.autocomplete(grupo=managed_group_autocomplete)
    async def close(self, interaction: discord.Interaction, grupo: str) -> None:
        await self._manage(interaction, grupo, lambda g: self.bot.web_api.update_group(g, interaction.user.id, status="cerrado"), "Inscripciones cerradas.")

    @app_commands.command(name="abrir", description="(Líder) Vuelve a abrir las inscripciones")
    @app_commands.autocomplete(grupo=managed_group_autocomplete)
    async def reopen(self, interaction: discord.Interaction, grupo: str) -> None:
        await self._manage(interaction, grupo, lambda g: self.bot.web_api.update_group(g, interaction.user.id, status="abierto"), "Inscripciones abiertas.")

    @app_commands.command(name="cancelar", description="(Líder) Cancela el grupo y avisa a los apuntados")
    @app_commands.autocomplete(grupo=managed_group_autocomplete)
    async def cancel(self, interaction: discord.Interaction, grupo: str) -> None:
        group = await self._manage(interaction, grupo, lambda g: self.bot.web_api.update_group(g, interaction.user.id, status="cancelado"), "Grupo cancelado.")
        if group is None:
            return
        mentions = " ".join(f"<@{s.discord_id}>" for s in group.signups if s.discord_id)
        channel = self.bot.get_channel(group.channel_id) if group.channel_id else None
        if channel and mentions:
            await channel.send(f"❌ Se cancela **{group.instance_name}**. {mentions}")

    @app_commands.command(name="reserva", description="(Líder) Pasa a alguien a reserva, o lo devuelve")
    @app_commands.autocomplete(grupo=managed_group_autocomplete)
    async def bench(self, interaction: discord.Interaction, grupo: str, usuario: discord.Member) -> None:
        await self._manage(interaction, grupo, lambda g: self.bot.web_api.set_bench(g, interaction.user.id, target=usuario.id), f"Cambiada la reserva de {usuario.display_name}.")

    @app_commands.command(name="quitar", description="(Líder) Saca a alguien del grupo")
    @app_commands.autocomplete(grupo=managed_group_autocomplete)
    async def kick(self, interaction: discord.Interaction, grupo: str, usuario: discord.Member) -> None:
        await self._manage(interaction, grupo, lambda g: self.bot.web_api.remove(g, interaction.user.id, target=usuario.id), f"{usuario.display_name} ya no está en el grupo.")

    @app_commands.command(name="lista", description="Próximas raids y mazmorras")
    async def upcoming(self, interaction: discord.Interaction) -> None:
        groups = [g for g in await self.active_groups(max_age=0) if g.kind != "lfg"]
        if not groups:
            await interaction.response.send_message("No hay nada organizado. ¡Crea algo con `/grupo crear`!", ephemeral=True)
            return
        lines = []
        for g in groups[:20]:
            roster = build_roster(g.signups, slots_for(g))
            link = f"https://discord.com/channels/{interaction.guild_id}/{g.channel_id}/{g.message_id}" if g.message_id else ""
            name = f"[{g.instance_name}]({link})" if link else g.instance_name
            lines.append(f"<t:{int(g.starts_at.timestamp())}:f> · {name} · {roster.confirmed_count}/{g.tanks + g.healers + g.dps}" + (" 🔒" if g.status == "cerrado" else ""))
        embed = discord.Embed(title="Próximos grupos", description="\n".join(lines), colour=discord.Colour.dark_gold())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Tareas de fondo ---------------------------------------------------------

    @tasks.loop(minutes=1)
    async def housekeeping(self) -> None:
        try:
            groups = await self.bot.web_api.list_groups()
        except WebApiError as e:
            log.warning("Sin repasar los grupos: %s", e)
            return
        to_remind, to_finish = due(groups, utcnow(), self.bot.config.reminder_minutes)
        for group in to_remind:
            try:
                await self.bot.web_api.update_group(group.id, reminded=True)
            except WebApiError:
                continue  # sin marcar no se avisa: así no se repite el aviso
            if group.starts_at > utcnow() - timedelta(minutes=10):
                await self._remind(group)
        for group in to_finish:
            try:
                await refresh_message(self.bot, await self.bot.web_api.update_group(group.id, status="terminado"))
            except WebApiError:
                pass

    @housekeeping.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @housekeeping.error
    async def _housekeeping_error(self, error: BaseException) -> None:
        log.exception("Fallo en las tareas de los grupos", exc_info=error)

    async def _remind(self, group: Group) -> None:
        channel = self.bot.get_channel(group.channel_id) if group.channel_id else None
        if channel is None:
            return
        roster = build_roster(group.signups, slots_for(group))
        people = [s for row in roster.confirmed.values() for s in row if s.discord_id]
        if not people:
            return
        mentions = " ".join(f"<@{s.discord_id}>" for s in people)
        jump = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{group.message_id}"
        await channel.send(f"⏰ **{group.instance_name}** empieza <t:{int(group.starts_at.timestamp())}:R> ({jump})\n{mentions}")


class Lfg(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="lfg", description="Busco grupo ya: aviso rápido para una mazmorra o raid")
    @app_commands.describe(instancia="Dónde quieres ir", rol="Con qué rol vas tú", nota="Algo más (opcional)")
    @app_commands.choices(rol=ROLE_CHOICES)
    @app_commands.autocomplete(instancia=instance_autocomplete)
    @app_commands.guild_only()
    async def lfg(self, interaction: discord.Interaction, instancia: str, rol: app_commands.Choice[str], nota: app_commands.Range[str, 0, 200] | None = None) -> None:
        instance: Instance | None = GAME.find_instance(instancia)
        if instance is None:
            await interaction.response.send_message(f"No conozco la instancia «{instancia}».", ephemeral=True)
            return
        try:
            snapshot = await self.bot.directory.snapshot()
        except WebUnavailable:
            await interaction.response.send_message("Ahora mismo no puedo leer la web de la hermandad.", ephemeral=True)
            return
        # El que lleva ese rol en la web y tiene nivel para la instancia, si lo hay.
        chars = candidates(snapshot.characters_of(interaction.user.id), rol.value, instance.levels[0] - 3)
        if not chars:
            await interaction.response.send_message(NO_CHARACTERS.format(role=rol.name, hint=manage_hint(self.bot)), ephemeral=True)
            return
        character = chars[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        now = utcnow()
        try:
            group = await self.bot.web_api.create_group(
                interaction.user.id, kind="lfg", instance_key=instance.key, instance_name=instance.name, starts_at=now,
                expires_at=now + timedelta(hours=self.bot.config.lfg_hours), tanks=instance.slots["tanque"],
                healers=instance.slots["sanador"], dps=instance.slots["dps"], notes=nota or "",
            )
            group = await self.bot.web_api.sign_up(group.id, interaction.user.id, character.id, rol.value)
        except WebApiError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        grupos: Grupos = self.bot.get_cog("Grupos")
        message = await grupos.post(interaction, group)
        await interaction.followup.send(f"Aviso publicado con {character.full_name}: {message.jump_url}", ephemeral=True)


async def setup(bot) -> None:
    bot.add_dynamic_items(GroupButton)
    await bot.add_cog(Grupos(bot))
    await bot.add_cog(Lfg(bot))
