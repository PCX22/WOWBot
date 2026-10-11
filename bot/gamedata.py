"""Datos estáticos del juego (clases, razas, profesiones, instancias), leídos de bot/data/*.yaml."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent / "data"

ROLES = {"tanque": "Tanque", "sanador": "Sanador", "dps": "DPS"}
ROLE_EMOJI = {"tanque": "🛡️", "sanador": "💚", "dps": "⚔️"}


@dataclass(frozen=True)
class WowClass:
    key: str
    name: str
    color: int
    roles: tuple[str, ...]
    specs: tuple[str, ...]


@dataclass(frozen=True)
class Race:
    key: str
    name: str
    faction: str
    classes: tuple[str, ...]


@dataclass(frozen=True)
class Profession:
    key: str
    name: str
    skill_line: int
    primary: bool


@dataclass(frozen=True)
class Instance:
    key: str
    name: str
    kind: str  # "raid" o "mazmorra"
    size: int
    levels: tuple[int, int]
    aliases: tuple[str, ...]
    slots: dict[str, int]


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar lo que escribe la gente."""
    text = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _load(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


class GameData:
    def __init__(self) -> None:
        self.classes = {
            key: WowClass(key, v["nombre"], int(v["color"].lstrip("#"), 16), tuple(v["roles"]), tuple(v["specs"]))
            for key, v in _load("clases.yaml").items()
        }
        self.races = {
            key: Race(key, v["nombre"], v["faccion"], tuple(v["clases"]))
            for key, v in _load("razas.yaml").items()
        }
        self.professions = {
            key: Profession(key, v["nombre"], int(v["skill_line"]), bool(v["principal"]))
            for key, v in _load("profesiones.yaml").items()
        }
        raw = _load("instancias.yaml")
        compositions = {int(size): dict(slots) for size, slots in raw["composiciones"].items()}
        self.instances: dict[str, Instance] = {}
        for section, kind in (("mazmorras", "mazmorra"), ("raids", "raid")):
            for key, v in raw[section].items():
                size = int(v["tamano"])
                slots = dict(v.get("composicion") or compositions[size])
                self.instances[key] = Instance(
                    key, v["nombre"], kind, size, tuple(v["niveles"]), tuple(v.get("alias", [])), slots
                )
        self._check()

    def _check(self) -> None:
        for race in self.races.values():
            for cls in race.classes:
                if cls not in self.classes:
                    raise ValueError(f"razas.yaml: {race.key} usa la clase desconocida {cls}")
        for cls in self.classes.values():
            for role in cls.roles:
                if role not in ROLES:
                    raise ValueError(f"clases.yaml: {cls.key} usa el rol desconocido {role}")
        for inst in self.instances.values():
            if sum(inst.slots.values()) != inst.size or set(inst.slots) != set(ROLES):
                raise ValueError(f"instancias.yaml: la composición de {inst.key} no cuadra con su tamaño")

    # Búsquedas ---------------------------------------------------------------

    def races_for_class(self, class_key: str) -> list[Race]:
        return [r for r in self.races.values() if class_key in r.classes]

    def is_valid_combo(self, race_key: str, class_key: str) -> bool:
        race = self.races.get(race_key)
        return race is not None and class_key in race.classes

    def profession_by_skill_line(self, skill_line: int) -> Profession | None:
        for prof in self.professions.values():
            if prof.skill_line == skill_line:
                return prof
        return None

    def search_instances(self, text: str, kind: str | None = None) -> list[Instance]:
        """Instancias cuyo nombre, clave o alias contienen lo escrito."""
        wanted = normalize(text)
        found = []
        for inst in self.instances.values():
            if kind and inst.kind != kind:
                continue
            haystack = [normalize(inst.name), inst.key, *(normalize(a) for a in inst.aliases)]
            if not wanted or any(wanted in h for h in haystack):
                found.append(inst)
        return found

    def find_instance(self, text: str) -> Instance | None:
        """La instancia escrita: clave exacta, nombre exacto o alias exacto; si no, la única que encaje."""
        if text in self.instances:
            return self.instances[text]
        wanted = normalize(text)
        for inst in self.instances.values():
            if wanted == normalize(inst.name) or wanted in (normalize(a) for a in inst.aliases):
                return inst
        matches = self.search_instances(text)
        return matches[0] if len(matches) == 1 else None


GAME = GameData()
