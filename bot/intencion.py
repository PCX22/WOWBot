"""Entiende los mensajes del canal donde cada uno contó qué personaje se va a hacer, escritos a su manera:
«Guerrero tanque, humano, minero y herrero», «pala heal enano», «Rogue dps (gnoma)»…

Busca palabras completas, sin tildes ni mayúsculas, en español, en inglés y en las abreviaturas habituales.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .gamedata import GAME, ROLES, normalize

CLASS_WORDS = {
    "guerrero": ["guerrero", "guerrera", "warrior", "warri", "warr"],
    "paladin": ["paladin", "paladina", "pala", "paly", "palad"],
    "cazador": ["cazador", "cazadora", "hunter", "hunt"],
    "picaro": ["picaro", "picara", "rogue", "rogu"],
    "sacerdote": ["sacerdote", "sacerdotisa", "priest", "sacer"],
    "chaman": ["chaman", "chamana", "shaman", "shamy", "chami"],
    "mago": ["mago", "maga", "mage"],
    "brujo": ["brujo", "bruja", "warlock", "lock"],
    "druida": ["druida", "druid", "dudu"],
}
RACE_WORDS = {
    "humano": ["humano", "humana", "human"],
    "enano": ["enano", "enana", "dwarf"],
    "elfo_noche": ["elfo de la noche", "elfa de la noche", "elfo nocturno", "elfa nocturna", "night elf", "nightelf", "elfo", "elfa"],
    "gnomo": ["gnomo", "gnoma", "gnome"],
    "orco": ["orco", "orca", "orc"],
    "no_muerto": ["no muerto", "no muerta", "no-muerto", "no-muerta", "nomuerto", "undead", "renegado", "renegada"],
    "tauren": ["tauren"],
    "trol": ["trol", "troll", "trola"],
}
ROLE_WORDS = {
    "tanque": ["tanque", "tank", "tankear", "tanquear", "tankeo", "prote", "proteccion", "prot"],
    "sanador": ["sanador", "sanadora", "healer", "heal", "heals", "curandero", "curandera", "curar", "curacion", "healeo", "sanacion"],
    "dps": ["dps", "damage", "dano", "danos", "melee", "caster", "pegar"],
}
PROFESSION_WORDS = {
    "alquimia": ["alquimia", "alquimista", "alchemy", "alchemist", "alqui"],
    "herreria": ["herreria", "herrero", "herrera", "blacksmithing", "blacksmith", "bs"],
    "encantamiento": ["encantamiento", "encantador", "encantadora", "enchanting", "enchanter", "encanta", "enchant"],
    "ingenieria": ["ingenieria", "ingeniero", "ingeniera", "engineering", "engineer", "inge", "ingi"],
    "herboristeria": ["herboristeria", "herborista", "herbalism", "herbalist", "herbo", "hierbas"],
    "peleteria": ["peleteria", "peletero", "peletera", "leatherworking", "leatherworker", "lw"],
    "mineria": ["mineria", "minero", "minera", "mining", "miner"],
    "desuello": ["desuello", "desollador", "desolladora", "desollar", "skinning", "skinner"],
    "sastreria": ["sastreria", "sastre", "tailoring", "tailor"],
    "cocina": ["cocina", "cocinero", "cocinera", "cooking", "cook"],
    "primeros_auxilios": ["primeros auxilios", "first aid", "vendas", "auxilios"],
    "pesca": ["pesca", "pescador", "pescadora", "fishing", "fisher"],
}
NAME_RE = re.compile(r"(?:nombre|personaje|pj|nick)\s*[:=\-]\s*([^\n,;|]+)", re.IGNORECASE)


@dataclass
class Intencion:
    class_key: str | None = None
    race_key: str | None = None
    role: str | None = None
    professions: list[str] = field(default_factory=list)
    name: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.class_key is not None

    def summary(self) -> str:
        cls = GAME.classes[self.class_key].name if self.class_key else "¿clase?"
        race = GAME.races[self.race_key].name if self.race_key else "¿raza?"
        role = ROLES[self.role] if self.role else "¿rol?"
        text = f"{cls}, {race}, {role}"
        if self.professions:
            text += " · " + ", ".join(GAME.professions[p].name for p in self.professions)
        return text


def _find(text: str, words: dict[str, list[str]]) -> list[str]:
    """Las claves cuyas palabras aparecen enteras en el texto, en el orden en que aparecen."""
    found: list[tuple[int, str]] = []
    for key, options in words.items():
        positions = [m.start() for w in options for m in re.finditer(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text)]
        if positions:
            found.append((min(positions), key))
    return [key for _, key in sorted(found)]


def parse(message: str) -> Intencion:
    text = normalize(message)
    result = Intencion()
    classes = _find(text, CLASS_WORDS)
    if classes:
        result.class_key = classes[0]
        if len(classes) > 1:
            result.warnings.append("nombra varias clases; uso la primera")
    races = _find(text, RACE_WORDS)
    if races:
        result.race_key = races[0]
    roles = _find(text, ROLE_WORDS)
    if result.class_key:
        allowed = GAME.classes[result.class_key].roles
        roles = [r for r in roles if r in allowed]
        result.role = roles[0] if roles else (allowed[0] if len(allowed) == 1 else None)
        if result.role is None:
            result.warnings.append("no dice el rol")
        if result.race_key and not GAME.is_valid_combo(result.race_key, result.class_key):
            result.warnings.append(f"un {GAME.races[result.race_key].name} no puede ser {GAME.classes[result.class_key].name}")
            result.race_key = None
    result.professions = _find(text, PROFESSION_WORDS)
    match = NAME_RE.search(message)
    if match:
        result.name = " ".join(match.group(1).split())[:32] or None
    return result
