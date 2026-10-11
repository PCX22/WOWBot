from bot.importers import read_all
from bot.importers.guildrecipes import read_catalog
from bot.importers.merge import apply_results, save_recipe_names
from bot.services import missing_recipes

from .test_import import guildrecipes_vars

ALCHEMY = 171


def test_read_catalog():
    catalog, packed = read_catalog(
        {
            "catalog": {
                171: {1: 3, 2: 1, 3: 2, 4: 2},  # tabla: se ordena y se quitan repetidas
                164: "QUJD",                    # empaquetada: falta Pack.lua
                "x": {1: 5},                    # basura
                202: {1: "no es un id"},
            }
        }
    )
    assert catalog == {171: [1, 2, 3]}
    assert packed == 1
    assert read_catalog(None) == ({}, 0)


def test_packed_catalog_is_reported_in_note():
    variables = guildrecipes_vars()
    variables["GuildRecipesDB"]["cache"] = {"catalog": {171: "QUJD"}}
    [result] = read_all(variables)
    assert "Pack.lua" in result.note


def with_catalog(ids, recipes=(2330,)):
    variables = guildrecipes_vars(recipes=recipes)
    variables["GuildRecipesDB"]["cache"] = {"catalog": {ALCHEMY: dict(enumerate(ids, 1))}}
    return read_all(variables)


async def test_catalogs_are_merged_and_missing_listed(sessions):
    # Dos PCs que han visto partes distintas del catálogo.
    async with sessions() as session:
        first = await apply_results(session, with_catalog([2330, 2331, 9000]))
        await session.commit()
    assert first.catalog_new == 3
    async with sessions() as session:
        second = await apply_results(session, with_catalog([2330, 9001]))
        await save_recipe_names(session, {9001: "Elixir nuevo de Forever", 2331: "Poción de maná menor"})
        await session.commit()
    assert second.catalog_new == 1

    async with sessions() as session:
        missing, total = await missing_recipes(session, ALCHEMY)
    # Tio Paco conoce la 2330. Primero las que tienen nombre (por orden alfabético), luego las demás.
    assert total == 4
    assert missing == [(9001, "Elixir nuevo de Forever"), (2331, "Poción de maná menor"), (9000, None)]


async def test_missing_without_catalog(sessions):
    async with sessions() as session:
        assert await missing_recipes(session, ALCHEMY) == ([], 0)
