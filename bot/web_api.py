"""Cliente de la API de la web para el bot (/api/bot/*): los grupos se guardan en la web.

El bot dice siempre qué usuario de Discord está actuando; la web comprueba qué puede hacer (miembro, líder del
grupo, oficial). Sin usuario (el bot por su cuenta) solo se puede guardar el mensaje de Discord, marcar el
recordatorio y dar un grupo por terminado.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

from .web import CLASS_FROM_WEB

log = logging.getLogger(__name__)

# La web usa tank/healer/dps; el bot, las claves de bot/gamedata.py.
ROLE_TO_WEB = {"tanque": "tank", "sanador": "healer", "dps": "dps"}
ROLE_FROM_WEB = {v: k for k, v in ROLE_TO_WEB.items()}
KIND_TO_WEB = {"raid": "raid", "mazmorra": "dungeon", "lfg": "lfg"}
KIND_FROM_WEB = {v: k for k, v in KIND_TO_WEB.items()}
STATUS_TO_WEB = {"abierto": "open", "cerrado": "closed", "cancelado": "cancelled", "terminado": "finished"}
STATUS_FROM_WEB = {v: k for k, v in STATUS_TO_WEB.items()}
RETRY_DELAYS = (1, 3)


class WebApiError(RuntimeError):
    """Un error que se le puede enseñar tal cual a quien pulsó el botón."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


@dataclass
class GroupSignup:
    discord_id: int | None
    character_id: str
    character_name: str
    class_key: str | None
    level: int
    role: str  # tanque, sanador o dps
    bench: bool
    created_at: datetime  # el orden de la cola (para logic.build_roster)


@dataclass
class Group:
    id: str
    kind: str  # raid, mazmorra o lfg
    instance_key: str
    instance_name: str
    starts_at: datetime
    expires_at: datetime | None
    leader_id: int | None
    tanks: int
    healers: int
    dps: int
    notes: str
    status: str  # abierto, cerrado, cancelado o terminado
    reminded: bool
    channel_id: int | None
    message_id: int | None
    signups: list[GroupSignup] = field(default_factory=list)

    @classmethod
    def from_web(cls, data: dict) -> "Group":
        leader = data.get("leader") or {}

        def snowflake(value):
            return int(value) if value and str(value).isdigit() else None

        return cls(
            id=data["id"],
            kind=KIND_FROM_WEB.get(data["kind"], data["kind"]),
            instance_key=data["instanceKey"],
            instance_name=data["instanceName"],
            starts_at=_date(data["startsAt"]),
            expires_at=_date(data.get("expiresAt")),
            leader_id=snowflake(leader.get("discordId")),
            tanks=data["tanks"],
            healers=data["healers"],
            dps=data["dps"],
            notes=data.get("notes") or "",
            status=STATUS_FROM_WEB.get(data["status"], data["status"]),
            reminded=bool(data.get("remindedAt")),
            channel_id=snowflake(data.get("discordChannelId")),
            message_id=snowflake(data.get("discordMessageId")),
            signups=[
                GroupSignup(
                    discord_id=snowflake(s.get("discordId")),
                    character_id=s["characterId"],
                    character_name=s["characterName"],
                    class_key=CLASS_FROM_WEB.get(s.get("wowClass") or ""),
                    level=s.get("level") or 0,
                    role=ROLE_FROM_WEB.get(s["role"], s["role"]),
                    bench=bool(s.get("bench")),
                    created_at=_date(s["queuedAt"]),
                )
                for s in data.get("signups", [])
            ],
        )


class WebApi:
    def __init__(self, base_url: str, token: str, guild_slug: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.guild_slug = guild_slug
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.token}"}, timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def _call(self, method: str, path: str, actor: int | None = None, **body) -> dict:
        if not self.guild_slug:
            raise WebApiError("No sé qué hermandad de la web usar (falta WEB_GUILD_SLUG).")
        payload = {"guild": self.guild_slug, **({"actorDiscordId": str(actor)} if actor else {}), **body}
        kwargs = {"params": {k: str(v) for k, v in payload.items()}} if method == "GET" else {"json": payload}
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                async with self._http().request(method, self.base_url + path, **kwargs) as response:
                    data = await response.json(content_type=None) if response.content_length != 0 else {}
                    if response.status < 400:
                        return data or {}
                    if response.status >= 500 and delay is not None:
                        raise aiohttp.ServerConnectionError(f"HTTP {response.status}")
                    message = (data or {}).get("error") or f"La web respondió {response.status}."
                    if response.status == 404 and message == "Not found":
                        message = "La web no tiene activada la API del bot (BOT_API_TOKEN)."
                    raise WebApiError(message, response.status)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if delay is None:
                    log.warning("La web no responde (%s %s): %s", method, path, e)
                    raise WebApiError("La web de la hermandad no responde ahora mismo. Prueba en un momento.") from None
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    # Grupos -------------------------------------------------------------------

    async def list_groups(self) -> list[Group]:
        data = await self._call("GET", "/api/bot/groups")
        return [Group.from_web(g) for g in data.get("groups", [])]

    async def get_group(self, group_id: str) -> Group:
        return Group.from_web((await self._call("GET", f"/api/bot/groups/{group_id}"))["group"])

    async def create_group(self, actor: int, *, kind: str, instance_key: str, instance_name: str, starts_at: datetime,
                           expires_at: datetime | None, tanks: int, healers: int, dps: int, notes: str) -> Group:
        data = await self._call(
            "POST", "/api/bot/groups", actor,
            kind=KIND_TO_WEB[kind], instanceKey=instance_key, instanceName=instance_name,
            startsAt=starts_at.isoformat(), expiresAt=expires_at.isoformat() if expires_at else None,
            tanks=tanks, healers=healers, dps=dps, notes=notes,
        )
        return Group.from_web(data["group"])

    async def update_group(self, group_id: str, actor: int | None = None, *, status: str | None = None,
                           channel_id: int | None = None, message_id: int | None = None, reminded: bool = False) -> Group:
        body: dict = {}
        if status:
            body["status"] = STATUS_TO_WEB[status]
        if channel_id:
            body["discordChannelId"] = str(channel_id)
        if message_id:
            body["discordMessageId"] = str(message_id)
        if reminded:
            body["reminded"] = True
        return Group.from_web((await self._call("PATCH", f"/api/bot/groups/{group_id}", actor, **body))["group"])

    async def sign_up(self, group_id: str, actor: int, character_id: str, role: str, bench: bool = False) -> Group:
        data = await self._call("POST", f"/api/bot/groups/{group_id}/signups", actor, characterId=character_id, role=ROLE_TO_WEB[role], bench=bench)
        return Group.from_web(data["group"])

    async def set_bench(self, group_id: str, actor: int, target: int | None = None, bench: bool | None = None) -> Group:
        body = {"targetDiscordId": str(target)} if target else {}
        if bench is not None:
            body["bench"] = bench
        return Group.from_web((await self._call("PATCH", f"/api/bot/groups/{group_id}/signups", actor, **body))["group"])

    async def remove(self, group_id: str, actor: int, target: int | None = None) -> Group:
        body = {"targetDiscordId": str(target)} if target else {}
        return Group.from_web((await self._call("DELETE", f"/api/bot/groups/{group_id}/signups", actor, **body))["group"])
