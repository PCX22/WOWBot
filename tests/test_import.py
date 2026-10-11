from sqlalchemy import select

from bot.importers import read_all
from bot.importers.luasv import parse
from bot.importers.merge import apply_results
from bot.models import AddonCharacter, KnownRecipe, RecipeName
from bot.uploads import handle_upload

from .test_luasv import SAMPLE

GUID = "Player-1234-0ABCDEF0"


def guildrecipes_vars(t=1790000000, recipes=(2330, 2331)):
    return {
        "GuildRecipesDB": {
            "guilds": {
                "Vieja": {"seen": 1, "members": {}},
                "Los de Siempre": {
                    "seen": 1790000000,
                    "members": {
                        GUID: {"t": t, "n": "Tio  Paco", "p": {171: {"r": 250, "m": 300, "k": dict(enumerate(recipes, 1))}, 186: {"r": 300, "m": 300, "k": False}}},
                        "basura": {"t": t},
                        "Player-1-AB": {"t": 5, "n": "Viejo"},  # sin hora válida: se ignora
                    },
                },
            }
        }
    }


def test_guildrecipes_reader_picks_latest_guild():
    [result] = read_all(guildrecipes_vars())
    assert result.addon == "GuildRecipes (Los de Siempre)"
    [char] = result.characters
    assert char.name == "Tio Paco"
    assert char.professions[171].recipes == [2330, 2331]
    assert char.professions[186].recipes == []


def test_guildrecipes_packed_lists_are_reported():
    variables = guildrecipes_vars()
    variables["GuildRecipesDB"]["guilds"]["Los de Siempre"]["members"][GUID]["p"][171]["k"] = "QUJD"
    [result] = read_all(variables)
    assert result.characters[0].professions[171].recipes is None
    assert "Pack.lua" in result.note


async def test_recipe_names_from_addon_cache(sessions):
    variables = guildrecipes_vars()
    variables["GuildRecipesDB"]["cache"] = {
        "names": {
            "enUS": {2330: "Minor Healing Potion", 777: "New Forever Recipe"},
            "esES": {2330: "Poción de sanación menor", 2331: "Poción de maná menor|cff"},
        }
    }
    [result] = read_all(variables)
    # Español primero; si no hay, el otro idioma; nada con códigos de color.
    assert result.recipe_names == {2330: "Poción de sanación menor", 777: "New Forever Recipe"}
    async with sessions() as session:
        await apply_results(session, [result])
        await session.commit()
        rows = {r.recipe_id: r.name_key for r in await session.scalars(select(RecipeName))}
    assert rows[2330] == "pocion de sanacion menor"


def test_unknown_addons_are_ignored():
    assert read_all({"GUILDBOOK_GLOBAL": {}}) == []


async def test_merge_creates_and_respects_age(sessions):
    async with sessions() as session:
        summary = await apply_results(session, read_all(guildrecipes_vars()))
        await session.commit()
    assert summary.created == 1 and summary.recipes == 2

    # Una copia más vieja con otras recetas no pisa lo que ya hay.
    async with sessions() as session:
        summary = await apply_results(session, read_all(guildrecipes_vars(t=1780000000, recipes=(1,))))
        await session.commit()
        assert summary.skipped_old == 1
        ids = sorted(await session.scalars(select(KnownRecipe.recipe_id)))
    assert ids == [2330, 2331]


async def test_unknown_list_keeps_recipes_and_dropped_profession_goes(sessions):
    async with sessions() as session:
        await apply_results(session, read_all(guildrecipes_vars()))
        await session.commit()
    # Copia más nueva: Alquimia sin leer (se conservan sus recetas) y sin Minería (se va).
    newer = guildrecipes_vars(t=1795000000)
    member = newer["GuildRecipesDB"]["guilds"]["Los de Siempre"]["members"][GUID]
    member["p"] = {171: {"r": 260, "m": 300, "k": "QUJD"}}
    async with sessions() as session:
        await apply_results(session, read_all(newer))
        await session.commit()
        char = await session.scalar(select(AddonCharacter))
        ids = sorted(await session.scalars(select(KnownRecipe.recipe_id)))
    assert ids == [2330, 2331]
    assert {p.skill_line: p.rank for p in char.professions} == {171: 260}
    assert (char.guid, char.data_t) == (GUID, 1795000000)


async def test_handle_upload_end_to_end(sessions, tmp_path):
    outcome = await handle_upload(sessions, tmp_path, 42, "GuildRecipes.lua", SAMPLE, None)
    assert outcome.ok, outcome.text
    assert "1 personajes nuevos" in outcome.text
    assert len(list(tmp_path.iterdir())) == 1
    again = await handle_upload(sessions, tmp_path, 42, "GuildRecipes.lua", SAMPLE, None)
    assert "ya estaba subido" in again.text
    bad = await handle_upload(sessions, tmp_path, 42, "x.lua", b"Nada = 1", None)
    assert not bad.ok and "No reconozco" in bad.text


def test_sample_parses_into_reader():
    [result] = read_all(parse(SAMPLE))
    assert result.characters[0].professions[171].rank == 250
