"""Lectura de la web (Guildbook): la respuesta de /api/bot/members, con la misma forma que listBotMembers en
src/server/services/bot-members.ts de la web (que ya deja fuera a los que se fueron y los personajes archivados)."""
import copy

import pytest

from bot.cogs.grupos import candidates
from bot.cogs.roles import wanted_roles
from bot.services import describe
from bot.web import Directory, WebUnavailable


def char(id, name, surname, wow_class, spec, role, level, is_main, verified, professions=()):
    return {
        "id": id, "name": name, "surname": surname, "fullName": f"{name} {surname}", "class": wow_class, "spec": spec,
        "role": role, "level": level, "isMain": is_main, "verified": verified,
        "professions": [{"profession": p, "skill": s} for p, s in professions],
    }


MEMBERS = {
    "guild": {"slug": "lds", "name": "Los de Siempre", "timezone": "Europe/Madrid"},
    "members": [
        {"discordId": "100", "tier": "officer", "characters": [
            char("c1", "Paco", "Martillo", "warrior", "Protection", "tank", 60, True, True, [("blacksmithing", 300), ("mining", 280)]),
            char("c2", "Paquita", "Martillo", "priest", "Holy", "healer", 42, False, False),
        ]},
        {"discordId": "200", "tier": "member", "characters": [
            char("c3", "Lucia", "Fuego", "mage", "Fire", "ranged", 60, True, True, [("tailoring", None), ("juggling", 5)]),
        ]},
        {"discordId": "300", "tier": "member", "characters": []},
    ],
}


class FakeWeb:
    def __init__(self):
        self.data = copy.deepcopy(MEMBERS)
        self.calls = 0
        self.fail = False

    async def list_members(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("la web no responde")
        return copy.deepcopy(self.data)


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
async def directory(web):
    return Directory(web.list_members, max_age=60)


async def test_snapshot_reads_members(directory):
    snapshot = await directory.snapshot()
    assert (snapshot.guild_name, snapshot.guild_slug) == ("Los de Siempre", "lds")
    assert sorted(c.full_name for c in snapshot.characters) == ["Lucia Fuego", "Paco Martillo", "Paquita Martillo"]
    paco = snapshot.by_id["c1"]
    assert (paco.class_key, paco.role, paco.web_role, paco.level, paco.is_main, paco.verified) == ("guerrero", "tanque", "tank", 60, True, True)
    assert paco.spec_label == "Protección"
    assert [(p.name, p.skill) for p in paco.professions] == [("Herrería", 300), ("Minería", 280)]
    assert snapshot.by_id["c3"].role == "dps"  # «ranged» en la web
    assert [c.full_name for c in snapshot.characters_of(100)] == ["Paco Martillo", "Paquita Martillo"]  # main primero


async def test_officers_and_lookup(directory):
    snapshot = await directory.snapshot()
    assert snapshot.is_officer(100) and not snapshot.is_officer(200) and not snapshot.is_officer(300)
    assert snapshot.find("paco  martillo").id == "c1"
    assert snapshot.find("Lucia").id == "c3"  # nombre suelto, si solo hay una
    assert snapshot.find("Martillo") is None


async def test_cache_and_web_down(web, directory):
    first = await directory.snapshot()
    assert await directory.snapshot() is first and web.calls == 1  # copia en memoria
    web.data["members"][0]["characters"][1]["level"] = 61
    assert (await directory.snapshot(fresh=True)).by_id["c2"].level == 61
    web.fail = True
    assert (await directory.snapshot(fresh=True)).by_id["c2"].level == 61  # caída: sigue con la última copia
    with pytest.raises(WebUnavailable):
        await Directory(web.list_members).snapshot()  # sin copia anterior no hay nada que enseñar


async def test_describe_and_roles(directory):
    snapshot = await directory.snapshot()
    text_ = describe(snapshot.by_id["c1"], with_professions=True, recipes={164: 12})
    assert text_.startswith("**Paco Martillo** — Guerrero 60 · 🛡️ Tanque (Protección) ⭐ ✔")
    assert "Herrería 300 (12 recetas)" in text_
    assert wanted_roles(snapshot, 100) == {"Guerrero", "Tanque", "Herrería", "Minería"}
    assert wanted_roles(snapshot, 999) == set()


async def test_candidates_prefer_web_role(directory):
    snapshot = await directory.snapshot()
    chars = snapshot.characters_of(100)
    assert [c.name for c in candidates(chars, "sanador")] == ["Paquita"]  # el guerrero no cura
    assert [c.name for c in candidates(chars, "dps")] == ["Paco", "Paquita"]
    assert [c.name for c in candidates(chars, "dps", min_level=50)] == ["Paco", "Paquita"]
