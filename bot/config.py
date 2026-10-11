"""Configuración del bot, leída del archivo .env (ver .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .permisos import parse_roles

ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "si", "sí", "yes", "on")


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int | None
    raid_channel_id: int | None
    dungeon_channel_id: int | None
    lfg_channel_id: int | None
    sync_channel_id: int | None
    officer_role_id: int | None
    officer_roles: tuple[str, ...]
    raid_creator_roles: tuple[str, ...]
    wow_guild_name: str | None
    web_database_url: str
    web_guild_slug: str | None
    web_url: str | None
    web_refresh_seconds: int
    web_api_url: str
    web_api_token: str
    read_messages: bool
    timezone: ZoneInfo
    database_path: Path
    uploads_dir: Path
    roles_class: bool
    roles_role: bool
    roles_profession: bool
    reminder_minutes: int
    lfg_hours: int

    @property
    def local_mode(self) -> bool:
        return not (self.web_database_url and self.web_api_token)

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(ROOT / ".env")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("Falta DISCORD_TOKEN en el archivo .env (copia .env.example a .env y rellénalo)")
        # Sin los dos datos de la web, el bot funciona en modo local (bot/local.py): guarda él los personajes y grupos.
        web_database_url = os.getenv("WEB_DATABASE_URL", "").strip()
        web_api_token = os.getenv("WEB_API_TOKEN", "").strip()
        data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "datos")))
        return cls(
            token=token,
            guild_id=_int("GUILD_ID"),
            raid_channel_id=_int("RAID_CHANNEL_ID"),
            dungeon_channel_id=_int("DUNGEON_CHANNEL_ID"),
            lfg_channel_id=_int("LFG_CHANNEL_ID"),
            sync_channel_id=_int("SYNC_CHANNEL_ID"),
            officer_role_id=_int("OFFICER_ROLE_ID"),
            # Roles de Discord (nombres o IDs, separados por comas): ver bot/permisos.py.
            officer_roles=parse_roles(os.getenv("ROLES_OFICIAL", "")),
            raid_creator_roles=parse_roles(os.getenv("PERMISO_CREAR_RAID", "")),
            wow_guild_name=os.getenv("WOW_GUILD_NAME", "").strip() or None,
            web_database_url=web_database_url,
            web_guild_slug=os.getenv("WEB_GUILD_SLUG", "").strip() or None,
            web_url=os.getenv("WEB_URL", "").strip().rstrip("/") or None,
            web_refresh_seconds=_int("WEB_REFRESCO_SEGUNDOS", 60),
            # La API vive en la misma web; dentro del servidor puede ir por la red interna (p. ej. http://web:3000).
            web_api_url=(os.getenv("WEB_API_URL", "").strip() or os.getenv("WEB_URL", "").strip()).rstrip("/"),
            web_api_token=web_api_token,
            # Leer el texto de los mensajes (para /registro importar): hace falta Message Content Intent en el portal.
            read_messages=_bool("LEER_MENSAJES", False),
            timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Madrid")),
            database_path=data_dir / "hermandad.db",
            uploads_dir=data_dir / "subidas",
            roles_class=_bool("ROLES_CLASE", True),
            roles_role=_bool("ROLES_ROL", True),
            roles_profession=_bool("ROLES_PROFESION", False),
            reminder_minutes=_int("RECORDATORIO_MINUTOS", 30),
            lfg_hours=_int("LFG_HORAS", 2),
        )
