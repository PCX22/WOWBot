"""DESACTIVADO (no está en COGS de bot/main.py): el sincronizador ya no envía por Discord, sino
directamente a la web (/api/addon/professions). Se conserva por si vuelve a hacer falta.

/sync: datos de los addons. El sincronizador del PC sube los archivos por un webhook a un canal privado."""
from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from ..models import Player, Upload, as_utc
from ..uploads import handle_upload, hash_token, new_token, reprocess_all

log = logging.getLogger(__name__)

WEBHOOK_NAME = "Sincronizador hermandad"
MAX_ATTACHMENT = 25 * 1024 * 1024


class Sync(commands.GroupCog, group_name="sync", group_description="Datos del addon de recetas"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.webhook: discord.Webhook | None = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._ensure_webhook()

    async def _ensure_webhook(self) -> discord.Webhook | None:
        if self.webhook:
            return self.webhook
        channel_id = self.bot.config.sync_channel_id
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            log.warning("SYNC_CHANNEL_ID no apunta a un canal de texto: el sincronizador no funcionará")
            return None
        try:
            hooks = await channel.webhooks()
            self.webhook = discord.utils.get(hooks, name=WEBHOOK_NAME) or await channel.create_webhook(
                name=WEBHOOK_NAME, reason="Sincronizador de addons"
            )
        except discord.Forbidden:
            log.warning("Sin permiso «Gestionar webhooks» en #%s", channel)
        return self.webhook

    # Mensajes del sincronizador --------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.channel.id != self.bot.config.sync_channel_id or message.webhook_id is None:
            return
        if not self.webhook or message.webhook_id != self.webhook.id:
            return
        try:
            meta = json.loads(message.content or "{}")
            token = str(meta["token"])
        except (ValueError, KeyError, TypeError):
            await self._delete(message)
            return
        async with self.bot.sessions() as session:
            player = await session.scalar(select(Player).where(Player.sync_token_hash == hash_token(token)))
        attachment = message.attachments[0] if message.attachments else None
        # El token no debe quedarse a la vista en el canal.
        await self._delete(message)
        if player is None:
            await message.channel.send("⚠️ Llegó un archivo con un token que no es válido; ignorado.")
            return
        if attachment is None or attachment.size > MAX_ATTACHMENT:
            return
        data = await attachment.read()
        outcome = await handle_upload(
            self.bot.sessions, self.bot.config.uploads_dir, player.discord_id, attachment.filename, data,
            self.bot.config.wow_guild_name,
        )
        icon = "✅" if outcome.ok else "⚠️"
        await message.channel.send(
            f"{icon} Archivo de <@{player.discord_id}> ({attachment.filename}): {outcome.text}"[:2000],
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    async def _delete(message: discord.Message) -> None:
        try:
            await message.delete()
        except discord.HTTPException:
            log.warning("No pude borrar el mensaje del sincronizador (falta «Gestionar mensajes» en #sync)")

    # Comandos -------------------------------------------------------------------

    @app_commands.command(name="token", description="Tu clave para el sincronizador del PC (la anterior deja de valer)")
    async def token(self, interaction: discord.Interaction) -> None:
        webhook = await self._ensure_webhook()
        if webhook is None:
            await interaction.response.send_message(
                "El sincronizador no está configurado en este servidor (falta el canal de sync). Avisa a un oficial.",
                ephemeral=True,
            )
            return
        token = new_token()
        async with self.bot.sessions() as session:
            player = await session.get(Player, interaction.user.id) or Player(discord_id=interaction.user.id)
            player.sync_token_hash = hash_token(token)
            session.add(player)
            await session.commit()
        await interaction.response.send_message(
            "**Datos para el sincronizador** (no los compartas con nadie):\n"
            f"Webhook: ||{webhook.url}||\nToken: ||`{token}`||\n\n"
            "Abre `sincronizador.exe`, pega los dos cuando te los pida y déjalo abierto mientras juegas. "
            "El juego guarda los datos al cerrar sesión o con `/reload`.",
            ephemeral=True,
        )

    @app_commands.command(name="subir", description="Sube a mano el archivo del addon de recetas (SavedVariables)")
    @app_commands.describe(archivo="El .lua de WTF/Account/<CUENTA>/SavedVariables/")
    async def upload(self, interaction: discord.Interaction, archivo: discord.Attachment) -> None:
        if archivo.size > MAX_ATTACHMENT:
            await interaction.response.send_message("El archivo es demasiado grande.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await archivo.read()
        outcome = await handle_upload(
            self.bot.sessions, self.bot.config.uploads_dir, interaction.user.id, archivo.filename, data,
            self.bot.config.wow_guild_name,
        )
        await interaction.followup.send(("✅ " if outcome.ok else "⚠️ ") + outcome.text[:1900], ephemeral=True)

    @app_commands.command(name="estado", description="Últimos archivos recibidos")
    async def status(self, interaction: discord.Interaction) -> None:
        async with self.bot.sessions() as session:
            uploads = list(await session.scalars(select(Upload).order_by(Upload.created_at.desc()).limit(10)))
        if not uploads:
            await interaction.response.send_message("Todavía no ha llegado ningún archivo.", ephemeral=True)
            return
        lines = [
            f"<t:{int(as_utc(u.created_at).timestamp())}:R> · <@{u.discord_id}> · {u.file_name}\n   {(u.result or '')[:150]}"
            for u in uploads
        ]
        await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="reprocesar", description="(Oficiales) Vuelve a importar todos los archivos guardados")
    @app_commands.default_permissions(manage_guild=True)
    async def reprocess(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        count = await reprocess_all(self.bot.sessions, self.bot.config.wow_guild_name)
        await interaction.followup.send(f"{count} archivos procesados de nuevo.", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Sync(bot))
