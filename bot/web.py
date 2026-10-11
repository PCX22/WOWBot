"""La web de la hermandad (fork de Guildbook) manda en miembros y personajes; el bot la lee en solo lectura.

Las consultas solo usan las columnas que necesita el bot (ver README: permisos del usuario de Postgres) y SQL
estándar. Se lee todo de una vez y se guarda en memoria unos segundos: una hermandad son unos cientos de filas,
y así los autocompletados no consultan la base de datos en cada tecla.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .gamedata import GAME, ROLE_EMOJI, ROLES, normalize

log = logging.getLogger(__name__)

# Valores de la web (src/lib/game.ts) → claves del bot (bot/data/*.yaml).
CLASS_FROM_WEB = {
    "warrior": "guerrero", "paladin": "paladin", "hunter": "cazador", "rogue": "picaro", "priest": "sacerdote",
    "shaman": "chaman", "mage": "mago", "warlock": "brujo", "druid": "druida",
}
PROFESSION_FROM_WEB = {
    "alchemy": "alquimia", "blacksmithing": "herreria", "enchanting": "encantamiento", "engineering": "ingenieria",
    "herbalism": "herboristeria", "leatherworking": "peleteria", "mining": "mineria", "skinning": "desuello",
    "tailoring": "sastreria", "cooking": "cocina", "first_aid": "primeros_auxilios", "fishing": "pesca",
}
# "dps" sin más es de los personajes del modo local (bot/local.py), que no distinguen cuerpo a cuerpo y distancia.
ROLE_FROM_WEB = {"tank": "tanque", "healer": "sanador", "melee": "dps", "ranged": "dps", "dps": "dps"}
WEB_ROLE_LABELS = {"tank": "Tanque", "healer": "Sanador", "melee": "DPS cuerpo a cuerpo", "ranged": "DPS a distancia", "dps": "DPS"}
# La web guarda la spec en inglés (CLASS_INFO en src/lib/game.ts); estos son sus nombres en el cliente español.
SPEC_LABELS = {
    "Arms": "Armas", "Fury": "Furia", "Protection": "Protección", "Holy": "Sagrado", "Retribution": "Reprensión",
    "Beast Mastery": "Dominio de bestias", "Marksmanship": "Puntería", "Survival": "Supervivencia",
    "Assassination": "Asesinato", "Combat": "Combate", "Subtlety": "Sutileza", "Discipline": "Disciplina",
    "Shadow": "Sombra", "Elemental": "Elemental", "Enhancement": "Mejora", "Restoration": "Restauración",
    "Arcane": "Arcano", "Fire": "Fuego", "Frost": "Escarcha", "Affliction": "Aflicción", "Demonology": "Demonología",
    "Destruction": "Destrucción", "Balance": "Equilibrio", "Feral": "Feral",
}
OFFICER_TIERS = ("officer", "admin")

GUILD_SQL = "SELECT id, name, slug, timezone FROM guilds"
MEMBERS_SQL = """
SELECT u.discord_id, r.tier
FROM memberships m
JOIN users u ON u.id = m.user_id
JOIN ranks r ON r.id = m.rank_id AND r.guild_id = m.guild_id
WHERE m.guild_id = :guild_id AND m.status = 'active' AND u.discord_id IS NOT NULL
"""
CHARACTERS_SQL = """
SELECT c.id, c.name, c.surname, c.class, c.spec, c.role, c.level, c.is_main, c.verified, u.discord_id
FROM characters c
JOIN memberships m ON m.id = c.membership_id AND m.guild_id = c.guild_id
JOIN users u ON u.id = m.user_id
WHERE c.guild_id = :guild_id AND c.archived_at IS NULL AND m.status = 'active'
"""
PROFESSIONS_SQL = "SELECT character_id, profession, skill FROM character_professions WHERE guild_id = :guild_id"


class WebUnavailable(RuntimeError):
    """No se puede leer la web y no hay ninguna copia anterior."""


@dataclass(frozen=True)
class WebProfession:
    key: str  # clave de profesiones.yaml
    skill: int | None

    @property
    def name(self) -> str:
        return GAME.professions[self.key].name

    @property
    def skill_line(self) -> int:
        return GAME.professions[self.key].skill_line


@dataclass(frozen=True)
class WebCharacter:
    id: str
    name: str
    surname: str
    class_key: str | None
    spec: str
    web_role: str
    level: int
    is_main: bool
    verified: bool
    discord_id: int | None
    professions: tuple[WebProfession, ...] = ()
    # Solo en el modo local: la web no guarda la raza.
    race_key: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.name} {self.surname}".strip()

    @property
    def role(self) -> str | None:
        return ROLE_FROM_WEB.get(self.web_role)

    @property
    def class_name(self) -> str:
        cls = GAME.classes.get(self.class_key or "")
        return cls.name if cls else "¿Clase?"

    @property
    def spec_label(self) -> str:
        return SPEC_LABELS.get(self.spec, self.spec)

    def can_play(self, role: str) -> bool:
        cls = GAME.classes.get(self.class_key or "")
        return cls is not None and role in cls.roles

    def profession(self, skill_line: int) -> WebProfession | None:
        return next((p for p in self.professions if p.skill_line == skill_line), None)


def name_key(name: str) -> str:
    return normalize(" ".join(name.split()))


@dataclass
class Snapshot:
    guild_name: str = ""
    guild_slug: str = ""
    characters: list[WebCharacter] = field(default_factory=list)
    tiers: dict[int, str] = field(default_factory=dict)  # discord_id → nivel del rango
    loaded_at: float = 0.0

    def __post_init__(self) -> None:
        self.by_id = {c.id: c for c in self.characters}
        self.by_key = {name_key(c.full_name): c for c in self.characters}
        first: dict[str, list[WebCharacter]] = {}
        for c in self.characters:
            first.setdefault(name_key(c.name), []).append(c)
        self._by_first = first

    def characters_of(self, discord_id: int) -> list[WebCharacter]:
        """Personajes de un miembro: primero el main, luego por nivel."""
        own = [c for c in self.characters if c.discord_id == discord_id]
        return sorted(own, key=lambda c: (not c.is_main, -c.level, c.full_name))

    def main_of(self, discord_id: int) -> WebCharacter | None:
        own = self.characters_of(discord_id)
        return own[0] if own else None

    def is_officer(self, discord_id: int) -> bool:
        return self.tiers.get(discord_id) in OFFICER_TIERS

    def find(self, name: str) -> WebCharacter | None:
        """Por nombre y apellido; si solo se da el nombre, el único personaje que se llame así."""
        key = name_key(name)
        if key in self.by_key:
            return self.by_key[key]
        same_first = self._by_first.get(key, [])
        return same_first[0] if len(same_first) == 1 else None


def _discord_id(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_snapshot(guild_row, member_rows, character_rows, profession_rows) -> Snapshot:
    professions: dict[str, list[WebProfession]] = {}
    for character_id, profession, skill in profession_rows:
        key = PROFESSION_FROM_WEB.get(str(profession))
        if key:
            professions.setdefault(str(character_id), []).append(WebProfession(key, skill))
    characters = [
        WebCharacter(
            id=str(cid),
            name=name,
            surname=surname or "",
            class_key=CLASS_FROM_WEB.get(str(wow_class)),
            spec=spec or "",
            web_role=str(role),
            level=int(level),
            is_main=bool(is_main),
            verified=bool(verified),
            discord_id=_discord_id(discord_id),
            professions=tuple(sorted(professions.get(str(cid), []), key=lambda p: -(p.skill or 0))),
        )
        for cid, name, surname, wow_class, spec, role, level, is_main, verified, discord_id in character_rows
    ]
    tiers = {d: str(tier) for discord_id, tier in member_rows if (d := _discord_id(discord_id)) is not None}
    return Snapshot(
        guild_name=guild_row[1], guild_slug=guild_row[2], characters=characters, tiers=tiers, loaded_at=time.monotonic()
    )


def make_web_engine(url: str) -> AsyncEngine:
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return create_async_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)


class Directory:
    """Lo que la web sabe de la hermandad, con una copia en memoria que se renueva cada `max_age` segundos."""

    def __init__(self, engine: AsyncEngine, guild_slug: str | None, max_age: float = 60) -> None:
        self.engine = engine
        self.guild_slug = guild_slug
        self.max_age = max_age
        self._snapshot: Snapshot | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> Snapshot:
        async with self.engine.connect() as conn:
            guilds = (await conn.execute(text(GUILD_SQL))).all()
            if self.guild_slug:
                guilds = [g for g in guilds if g[2] == self.guild_slug]
            if len(guilds) != 1:
                raise WebUnavailable(
                    f"No encuentro la hermandad «{self.guild_slug}» en la web."
                    if self.guild_slug
                    else f"La web tiene {len(guilds)} hermandades: pon WEB_GUILD_SLUG en el .env."
                )
            guild = guilds[0]
            params = {"guild_id": guild[0]}
            members = (await conn.execute(text(MEMBERS_SQL), params)).all()
            characters = (await conn.execute(text(CHARACTERS_SQL), params)).all()
            professions = (await conn.execute(text(PROFESSIONS_SQL), params)).all()
        return build_snapshot(guild, members, characters, professions)

    async def snapshot(self, fresh: bool = False) -> Snapshot:
        current = self._snapshot
        if current and not fresh and time.monotonic() - current.loaded_at < self.max_age:
            return current
        async with self._lock:
            current = self._snapshot
            if current and not fresh and time.monotonic() - current.loaded_at < self.max_age:
                return current
            try:
                self._snapshot = await self._load()
            except WebUnavailable:
                raise
            except Exception as e:  # la web caída no debe tumbar el bot: se sigue con la última copia
                if self._snapshot is None:
                    raise WebUnavailable(f"No puedo leer la web: {e}") from e
                log.warning("No pude leer la web (%s); sigo con los datos de hace %.0f s", e, time.monotonic() - self._snapshot.loaded_at)
                self._snapshot.loaded_at = time.monotonic() - self.max_age + 15  # reintentar en 15 s
            return self._snapshot


def role_text(character: WebCharacter) -> str:
    role = character.role
    return f"{ROLE_EMOJI[role]} {WEB_ROLE_LABELS.get(character.web_role, ROLES.get(role, ''))}" if role else ""
