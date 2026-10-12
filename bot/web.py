"""La web de la hermandad (fork de Guildbook) manda en miembros y personajes; el bot los lee de su API.

/api/bot/members (src/server/services/bot-members.ts en la web) da los miembros activos que han entrado con Discord,
el nivel de su rango y sus personajes con sus profesiones. Se lee todo de una vez y se guarda en memoria unos
segundos: una hermandad son unos cientos de personajes, y así los autocompletados no preguntan a la web en cada tecla.
El bot no tiene acceso a la base de datos de la web.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

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


def _professions(rows) -> tuple[WebProfession, ...]:
    found = [WebProfession(key, p.get("skill")) for p in rows or () if (key := PROFESSION_FROM_WEB.get(str(p.get("profession"))))]
    return tuple(sorted(found, key=lambda p: -(p.skill or 0)))


def build_snapshot(data: dict) -> Snapshot:
    """La respuesta de /api/bot/members: {guild: {slug, name, timezone}, members: [{discordId, tier, characters}]}."""
    guild = data.get("guild") or {}
    characters, tiers = [], {}
    for member in data.get("members") or ():
        discord_id = _discord_id(member.get("discordId"))
        if discord_id is None:
            continue
        tiers[discord_id] = str(member.get("tier") or "")
        for c in member.get("characters") or ():
            characters.append(
                WebCharacter(
                    id=str(c["id"]),
                    name=c["name"],
                    surname=c.get("surname") or "",
                    class_key=CLASS_FROM_WEB.get(str(c.get("class"))),
                    spec=c.get("spec") or "",
                    web_role=str(c.get("role")),
                    level=int(c.get("level") or 0),
                    is_main=bool(c.get("isMain")),
                    verified=bool(c.get("verified")),
                    discord_id=discord_id,
                    professions=_professions(c.get("professions")),
                )
            )
    return Snapshot(
        guild_name=guild.get("name") or "", guild_slug=guild.get("slug") or "", characters=characters, tiers=tiers,
        loaded_at=time.monotonic(),
    )


class Directory:
    """Lo que la web sabe de la hermandad, con una copia en memoria que se renueva cada `max_age` segundos.

    `fetch` pide /api/bot/members a la web (WebApi.list_members).
    """

    def __init__(self, fetch: Callable[[], Awaitable[dict]], max_age: float = 60) -> None:
        self.fetch = fetch
        self.max_age = max_age
        self._snapshot: Snapshot | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> Snapshot:
        return build_snapshot(await self.fetch())

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
