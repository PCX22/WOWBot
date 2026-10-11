import gzip

import pytest

from bot.importers import luasv
from bot.importers.luasv import SavedVariablesError, as_list, parse

SAMPLE = b"""
GuildRecipesDB = {
    ["schema"] = 2,
    ["guilds"] = {
        ["Los de Siempre"] = {
            ["seen"] = 1790000000,
            ["members"] = {
                ["Player-1234-0ABCDEF0"] = { ["t"] = 1790000000, ["n"] = "Tio Paco", ["p"] = { [171] = { ["r"] = 250, ["m"] = 300, ["k"] = { 2330, 2331, 3171 } } } },
            },
        },
    },
}
OtraVariable = "hola"
"""


def test_parse_sample():
    variables = parse(SAMPLE)
    assert set(variables) == {"GuildRecipesDB", "OtraVariable"}
    member = variables["GuildRecipesDB"]["guilds"]["Los de Siempre"]["members"]["Player-1234-0ABCDEF0"]
    assert member["n"] == "Tio Paco"
    assert as_list(member["p"][171]["k"]) == [2330, 2331, 3171]


def test_parse_gzip():
    assert parse(gzip.compress(SAMPLE))["OtraVariable"] == "hola"


def test_no_libraries_inside_sandbox():
    with pytest.raises(SavedVariablesError):
        parse(b'os.execute("calc")')
    with pytest.raises(SavedVariablesError):
        parse(b'X = io.open("secreto.txt")')
    with pytest.raises(SavedVariablesError):
        parse(b'X = load("return 1")')


def test_bytecode_rejected():
    with pytest.raises(SavedVariablesError):
        parse(b"\x1bLua\x54\x00garbage")


def test_endless_loop_is_stopped(monkeypatch):
    monkeypatch.setattr(luasv, "MAX_INSTRUCTIONS", 100_000)
    with pytest.raises(SavedVariablesError, match="demasiado"):
        parse(b"while true do end")


def test_syntax_error():
    with pytest.raises(SavedVariablesError):
        parse(b"GuildRecipesDB = {")


def test_gzip_bomb(monkeypatch):
    monkeypatch.setattr(luasv, "MAX_FILE", 1000)
    with pytest.raises(SavedVariablesError):
        parse(gzip.compress(b"-" * 5000))
