"""Lectura de la web (Guildbook). Las tablas se imitan en SQLite con los mismos nombres de columnas que
src/db/schema.ts de la web; las consultas de bot/web.py son SQL estándar y valen para las dos."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bot.cogs.grupos import candidates
from bot.cogs.roles import wanted_roles
from bot.services import describe
from bot.web import Directory, WebUnavailable

SCHEMA = """
CREATE TABLE guilds (id TEXT PRIMARY KEY, slug TEXT, name TEXT, timezone TEXT);
CREATE TABLE users (id TEXT PRIMARY KEY, discord_id TEXT, email TEXT);
CREATE TABLE ranks (id TEXT PRIMARY KEY, guild_id TEXT, name TEXT, tier TEXT, sort_order INT);
CREATE TABLE memberships (id TEXT PRIMARY KEY, guild_id TEXT, user_id TEXT, rank_id TEXT, status TEXT);
CREATE TABLE characters (
    id TEXT PRIMARY KEY, guild_id TEXT, membership_id TEXT, name TEXT, surname TEXT, faction TEXT, class TEXT,
    spec TEXT, role TEXT, level INT, is_main BOOLEAN, verified BOOLEAN, archived_at TEXT
);
CREATE TABLE character_professions (id TEXT PRIMARY KEY, guild_id TEXT, character_id TEXT, profession TEXT, skill INT);
"""

DATA = """
INSERT INTO guilds VALUES ('g1', 'lds', 'Los de Siempre', 'Europe/Madrid');
INSERT INTO users VALUES ('u1', '100', 'a@a'), ('u2', '200', 'b@b'), ('u3', '300', 'c@c'), ('u4', NULL, 'd@d');
INSERT INTO ranks VALUES ('r-of', 'g1', 'Oficial', 'officer', 1), ('r-mi', 'g1', 'Miembro', 'member', 2);
INSERT INTO memberships VALUES
  ('m1', 'g1', 'u1', 'r-of', 'active'),
  ('m2', 'g1', 'u2', 'r-mi', 'active'),
  ('m3', 'g1', 'u3', 'r-mi', 'former'),
  ('m4', 'g1', 'u4', 'r-mi', 'active');
INSERT INTO characters VALUES
  ('c1', 'g1', 'm1', 'Paco', 'Martillo', 'alliance', 'warrior', 'Protection', 'tank', 60, 1, 1, NULL),
  ('c2', 'g1', 'm1', 'Paquita', 'Martillo', 'alliance', 'priest', 'Holy', 'healer', 42, 0, 0, NULL),
  ('c3', 'g1', 'm2', 'Lucia', 'Fuego', 'alliance', 'mage', 'Fire', 'ranged', 60, 1, 1, NULL),
  ('c4', 'g1', 'm2', 'Borrada', 'Vieja', 'alliance', 'rogue', 'Combat', 'melee', 60, 0, 0, '2026-09-01'),
  ('c5', 'g1', 'm3', 'Exmiembro', 'Ido', 'alliance', 'mage', 'Frost', 'ranged', 60, 1, 0, NULL);
INSERT INTO character_professions VALUES
  ('p1', 'g1', 'c1', 'blacksmithing', 300), ('p2', 'g1', 'c1', 'mining', 280), ('p3', 'g1', 'c3', 'tailoring', NULL);
"""


@pytest.fixture
async def web_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for statement in (SCHEMA + DATA).split(";"):
            if statement.strip():
                await conn.execute(text(statement))
    yield engine
    await engine.dispose()


@pytest.fixture
async def directory(web_engine):
    return Directory(web_engine, "lds", max_age=60)


async def test_snapshot_reads_active_members_only(directory):
    snapshot = await directory.snapshot()
    assert snapshot.guild_name == "Los de Siempre"
    # Ni personajes archivados ni de miembros que se fueron.
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


async def test_unknown_guild_and_cache(web_engine):
    with pytest.raises(WebUnavailable):
        await Directory(web_engine, "otra").snapshot()
    directory = Directory(web_engine, None)  # una sola hermandad: no hace falta el slug
    first = await directory.snapshot()
    assert await directory.snapshot() is first  # copia en memoria
    async with web_engine.begin() as conn:
        await conn.execute(text("UPDATE characters SET level = 61 WHERE id = 'c2'"))
    assert (await directory.snapshot(fresh=True)).by_id["c2"].level == 61


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
