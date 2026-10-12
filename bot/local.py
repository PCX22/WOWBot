"""Modo local: mientras no esté la web, el bot guarda los personajes y los grupos en su propia base de datos.

LocalDirectory hace de bot/web.py (Directory) y LocalGroupsApi de bot/web_api.py (WebApi): los mismos métodos y
los mismos objetos, para que los comandos funcionen igual en los dos modos. Las reglas son las de la web
(src/server/services/groups.ts del fork): solo con un personaje propio, con un rol que su clase pueda llevar, y
gestionar solo el líder o un oficial. Cuando llegue la web se ponen WEB_URL, WEB_API_TOKEN y WEB_GUILD_SLUG en el
.env y el bot deja de usar esto.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .gamedata import GAME, ROLES
from .models import LocalCharacter, LocalGroup, LocalSignup, as_utc, utcnow
from .web import Snapshot, WebCharacter, WebProfession
from .web_api import Group, GroupSignup, WebApiError

ROLE_TO_WEB_ROLE = {"tanque": "tank", "sanador": "healer", "dps": "dps"}
ACTIVE = ("abierto", "cerrado")
FINISH_AFTER = timedelta(hours=5)
NO_CHARACTER = "Primero registra tu personaje con /registro añadir."
NOT_MANAGER = "Solo el líder del grupo o un oficial pueden hacer eso."


def to_web_character(c: LocalCharacter) -> WebCharacter:
    return WebCharacter(
        id=str(c.id),
        name=c.name,
        surname="",
        class_key=c.class_key,
        spec=c.spec or "",
        web_role=ROLE_TO_WEB_ROLE.get(c.role, "dps"),
        level=c.level,
        is_main=c.is_main,
        verified=False,
        discord_id=c.discord_id,
        professions=tuple(sorted((WebProfession(p.key, p.skill) for p in c.professions), key=lambda p: -(p.skill or 0))),
        race_key=c.race_key,
    )


class LocalDirectory:
    """Los personajes registrados en el bot, con la misma forma que los de la web."""

    def __init__(self, sessions, guild_name: str = "Hermandad") -> None:
        self.sessions = sessions
        self.guild_name = guild_name

    async def snapshot(self, fresh: bool = False) -> Snapshot:
        async with self.sessions() as session:
            rows = list(await session.scalars(select(LocalCharacter)))
        return Snapshot(guild_name=self.guild_name, guild_slug="local", characters=[to_web_character(c) for c in rows], loaded_at=time.monotonic())


def _group(row: LocalGroup) -> Group:
    return Group(
        id=row.id,
        kind=row.kind,
        instance_key=row.instance_key,
        instance_name=row.instance_name,
        starts_at=as_utc(row.starts_at),
        expires_at=as_utc(row.expires_at) if row.expires_at else None,
        leader_id=row.leader_id,
        tanks=row.tanks,
        healers=row.healers,
        dps=row.dps,
        notes=row.notes or "",
        status=row.status,
        reminded=row.reminded,
        channel_id=row.channel_id,
        message_id=row.message_id,
        signups=[
            GroupSignup(
                discord_id=s.discord_id,
                character_id=str(s.character_id),
                character_name=s.character.name,
                class_key=s.character.class_key,
                level=s.character.level,
                role=s.role,
                bench=s.bench,
                created_at=as_utc(s.created_at),
            )
            for s in sorted(row.signups, key=lambda s: as_utc(s.created_at))
        ],
    )


class LocalGroupsApi:
    """Los grupos guardados en el bot. `is_officer(discord_id)` dice quién puede gestionar cualquier grupo."""

    def __init__(self, sessions, is_officer: Callable[[int], bool]) -> None:
        self.sessions = sessions
        self.is_officer = is_officer

    async def close(self) -> None:
        pass

    async def _load(self, session, group_id: str) -> LocalGroup:
        """El grupo con sus inscripciones y sus personajes, leídos de nuevo de la base de datos."""
        row = await session.scalar(
            select(LocalGroup)
            .where(LocalGroup.id == group_id)
            .options(selectinload(LocalGroup.signups).selectinload(LocalSignup.character))
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise WebApiError("No encuentro ese grupo.", 404)
        return row

    def _is_manager(self, actor: int | None, row: LocalGroup) -> bool:
        return actor is not None and (actor == row.leader_id or self.is_officer(actor))

    async def list_groups(self) -> list[Group]:
        now = utcnow()
        async with self.sessions() as session:
            rows = list(await session.scalars(select(LocalGroup).where(LocalGroup.status.in_(ACTIVE)).order_by(LocalGroup.starts_at)))
        return [_group(r) for r in rows if as_utc(r.starts_at) > now - FINISH_AFTER or (r.expires_at and as_utc(r.expires_at) > now)]

    async def get_group(self, group_id: str) -> Group:
        async with self.sessions() as session:
            return _group(await self._load(session, group_id))

    async def create_group(self, actor: int, *, kind: str, instance_key: str, instance_name: str, starts_at: datetime,
                           expires_at: datetime | None, tanks: int, healers: int, dps: int, notes: str) -> Group:
        if not 1 <= tanks + healers + dps <= 40:
            raise WebApiError("Un grupo tiene entre 1 y 40 plazas.", 400)
        async with self.sessions() as session:
            if not await session.scalar(select(LocalCharacter.id).where(LocalCharacter.discord_id == actor).limit(1)):
                raise WebApiError(NO_CHARACTER, 401)
            row = LocalGroup(
                id=str(uuid.uuid4()), kind=kind, instance_key=instance_key, instance_name=instance_name[:120],
                starts_at=starts_at, expires_at=expires_at, leader_id=actor, tanks=tanks, healers=healers, dps=dps,
                notes=notes or "", status="abierto", reminded=False, signups=[],
            )
            session.add(row)
            await session.commit()
            return _group(row)

    async def update_group(self, group_id: str, actor: int | None = None, *, status: str | None = None,
                           channel_id: int | None = None, message_id: int | None = None, reminded: bool = False) -> Group:
        async with self.sessions() as session:
            row = await self._load(session, group_id)
            if status and status != row.status:
                if not (actor is None and status == "terminado") and not self._is_manager(actor, row):
                    raise WebApiError(NOT_MANAGER, 403)
                row.status = status
            if channel_id:
                row.channel_id = channel_id
            if message_id:
                row.message_id = message_id
            if reminded:
                row.reminded = True
            await session.commit()
            return _group(await self._load(session, group_id))

    async def sign_up(self, group_id: str, actor: int, character_id: str, role: str, bench: bool = False) -> Group:
        async with self.sessions() as session:
            row = await self._load(session, group_id)
            if row.status != "abierto":
                raise WebApiError("Las inscripciones de este grupo no están abiertas.", 400)
            character = await session.get(LocalCharacter, int(character_id)) if character_id.isdigit() else None
            if character is None or character.discord_id != actor:
                raise WebApiError("Ese personaje no es tuyo.", 400)
            cls = GAME.classes.get(character.class_key)
            if cls is None or role not in cls.roles:
                raise WebApiError(f"Ese personaje no puede ir de {ROLES[role].lower()}.", 400)
            signup = next((s for s in row.signups if s.discord_id == actor), None)
            if signup is None:
                signup = LocalSignup(discord_id=actor, character_id=character.id, role=role, bench=bench, created_at=utcnow())
                row.signups.append(signup)
            else:
                if signup.role != role or signup.character_id != character.id:
                    signup.created_at = utcnow()  # cambiar de rol o de personaje te pone al final de la cola
                signup.character_id, signup.role, signup.bench = character.id, role, bench
            signup.character = character  # ya cargado: que no haga falta volver a leerlo al pintar el grupo
            await session.commit()
            return _group(await self._load(session, group_id))

    async def _target(self, row: LocalGroup, actor: int, target: int | None) -> tuple[LocalSignup, bool]:
        self_ = target is None or target == actor
        if not self_ and not self._is_manager(actor, row):
            raise WebApiError(NOT_MANAGER, 403)
        signup = next((s for s in row.signups if s.discord_id == (actor if self_ else target)), None)
        if signup is None:
            raise WebApiError("No estás apuntado a este grupo." if self_ else "Esa persona no está apuntada.", 400)
        if self_ and row.status != "abierto":
            raise WebApiError("Las inscripciones de este grupo no están abiertas.", 400)
        return signup, self_

    async def set_bench(self, group_id: str, actor: int, target: int | None = None, bench: bool | None = None) -> Group:
        async with self.sessions() as session:
            row = await self._load(session, group_id)
            signup, _ = await self._target(row, actor, target)
            signup.bench = (not signup.bench) if bench is None else bench
            await session.commit()
            return _group(await self._load(session, group_id))

    async def remove(self, group_id: str, actor: int, target: int | None = None) -> Group:
        async with self.sessions() as session:
            row = await self._load(session, group_id)
            signup, _ = await self._target(row, actor, target)
            row.signups.remove(signup)
            await session.commit()
            return _group(await self._load(session, group_id))
