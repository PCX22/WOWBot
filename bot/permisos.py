"""Quién puede hacer qué según sus roles de Discord (PERMISO_CREAR_RAID y ROLES_OFICIAL en el .env).

Los roles se escriben por nombre o por ID, separados por comas. Al comparar nombres dan igual las mayúsculas, las
tildes y los emojis: «Oficial» vale para un rol llamado «⚔️ oficial».
"""
from __future__ import annotations

import re

import discord

from .gamedata import normalize


def role_key(text: str) -> str:
    """El nombre de un rol sin emojis ni signos: solo letras, números y espacios simples."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", normalize(text)).split())


def parse_roles(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def has_any_role(member: discord.Member | discord.User | None, wanted: tuple[str, ...]) -> bool:
    """Si tiene alguno de los roles pedidos (por ID o por nombre)."""
    roles = getattr(member, "roles", None)
    if not roles or not wanted:
        return False
    ids = {w for w in wanted if w.isdigit()}
    names = {role_key(w) for w in wanted if not w.isdigit()}
    return any(str(r.id) in ids or role_key(r.name) in names for r in roles)


def is_officer(member: discord.Member | discord.User | None, config) -> bool:
    """Oficial para el bot: quien puede gestionar el servidor, o tiene un rol de ROLES_OFICIAL (u OFFICER_ROLE_ID)."""
    perms = getattr(member, "guild_permissions", None)  # solo los miembros del servidor la tienen
    if perms is None:
        return False
    if perms.manage_guild:
        return True
    wanted = config.officer_roles + ((str(config.officer_role_id),) if config.officer_role_id else ())
    return has_any_role(member, wanted)


def can_create_raid(member: discord.Member | discord.User | None, config) -> bool:
    """Sin PERMISO_CREAR_RAID, cualquiera; con él, solo esos roles y los oficiales."""
    if not config.raid_creator_roles:
        return True
    return is_officer(member, config) or has_any_role(member, config.raid_creator_roles)
