"""Arranque del bot: python -m bot"""
from __future__ import annotations

import json
import logging

import discord
from discord.ext import commands

from .config import Config
from .db import init_db, make_engine
from .gamedata import DATA_DIR
from .importers.merge import save_recipe_names
from .models import RecipeName
from .permisos import is_officer
from .web import Directory, WebUnavailable
from .local import LocalDirectory, LocalGroupsApi
from .web_api import WebApi

log = logging.getLogger("bot")

# recetas y sync (recetas del addon por Discord) están desactivados: el sincronizador envía ahora a la web.
COGS = ["roles", "personajes", "grupos", "profesiones"]


class GuildBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.members = True          # para /roles-sincronizar
        intents.message_content = config.read_messages  # para /registro importar
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.config = config
        self.sessions = None
        self.directory: Directory | None = None
        self.web_api: WebApi | None = None

    async def setup_hook(self) -> None:
        self.config.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions = await init_db(make_engine(self.config.database_path))
        if self.config.local_mode:
            log.warning("Modo local: sin datos de la web en el .env, el bot guarda él los personajes y los grupos")
            self.directory = LocalDirectory(self.sessions)
            self.web_api = LocalGroupsApi(self.sessions, self.is_officer)
            await self._load_recipe_names()
            await self._load_cogs([*COGS, "registro"])
            await self._sync_commands()
            return
        if not self.config.web_guild_slug:
            raise SystemExit("Falta WEB_GUILD_SLUG en el .env: el subdominio (slug) de la hermandad en la web")
        self.web_api = WebApi(self.config.web_api_url, self.config.web_api_token, self.config.web_guild_slug)
        self.directory = Directory(self.web_api.list_members, self.config.web_refresh_seconds)
        try:
            snapshot = await self.directory.snapshot()
            log.info("Web: hermandad «%s», %d personajes", snapshot.guild_name, len(snapshot.characters))
        except WebUnavailable as e:
            log.error("No puedo leer la web todavía (%s). El bot arranca y lo reintentará.", e)
        await self._load_recipe_names()
        await self._load_cogs(COGS)
        await self._sync_commands()

    async def _load_cogs(self, cogs: list[str]) -> None:
        for cog in cogs:
            await self.load_extension(f"bot.cogs.{cog}")

    async def _sync_commands(self) -> None:
        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("%d comandos registrados", len(synced))

    async def _load_recipe_names(self) -> None:
        """Nombres de recetas de vanilla (bot/data/recetas_es.json, si existe): {"id": {"n": nombre, "sl": skill_line}}."""
        path = DATA_DIR / "recetas_es.json"
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        async with self.sessions() as session:
            if await session.get(RecipeName, int(next(iter(data)))) is not None:
                return
            await save_recipe_names(
                session, {int(k): v["n"] for k, v in data.items()}, {int(k): v["sl"] for k, v in data.items() if v.get("sl")}
            )
            await session.commit()
        log.info("%d nombres de recetas cargados", len(data))

    def is_officer(self, discord_id: int) -> bool:
        """Modo local: oficiales son quienes pueden gestionar el servidor o tienen un rol de ROLES_OFICIAL."""
        guild = self.get_guild(self.config.guild_id) if self.config.guild_id else (self.guilds[0] if self.guilds else None)
        return is_officer(guild.get_member(discord_id) if guild else None, self.config)

    async def close(self) -> None:
        if self.web_api:
            await self.web_api.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Conectado como %s en %d servidores", self.user, len(self.guilds))


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.load()
    GuildBot(config).run(config.token, log_handler=None)
