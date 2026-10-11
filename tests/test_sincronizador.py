"""El sincronizador: lectura del archivo del addon (con su Pack.lua real) y envío a la web."""
from pathlib import Path

import pytest
from lupa import LuaRuntime

from sync_client.lector import pack_list, parse, professions_payload, read, unpack_list
from sync_client.lector.addon import NotAddonFile
from sync_client.sincronizador import Web, WebError, send_with_retries, summary_text

PACK_LUA = (Path(__file__).parent.parent / "addon_ref" / "Pack.lua").read_text(encoding="utf-8")
GUID = "Player-1234-0ABCDEF0"


@pytest.fixture(scope="module")
def real_pack():
    """El Pack.lua del addon, ejecutado tal cual en Lua."""
    lua = LuaRuntime()
    ns = lua.table()
    lua.execute("return function(src, ns) return load(src, 'Pack.lua')('Addon', ns) end")(PACK_LUA, ns)
    return lua, ns.Pack


def lua_pack(real_pack, ids):
    """Lo que guarda el addon en disco para esa lista (Data.PackForSave): WriteList y ToBase64, en Lua."""
    lua, pack = real_pack
    encode = lua.eval("function(pack, list) local w = pack.Writer(); pack.WriteList(w, list); return pack.ToBase64(w:Result()) end")
    return encode(pack, lua.table(*ids))


@pytest.mark.parametrize("ids", [[], [2330], [2330, 2331, 3171], [1, 127, 128, 16383, 16384, 1_500_000, 2097151, 2097152]])
def test_unpack_matches_real_pack_lua(real_pack, ids):
    encoded = lua_pack(real_pack, ids)
    assert unpack_list(encoded) == ids
    assert pack_list(ids) == encoded  # y nuestro empaquetado es idéntico al del addon


def test_unpack_rejects_garbage():
    assert unpack_list("no es base64!") is None
    assert unpack_list("QUJD") is None  # base64 válido, pero no es una lista
    assert unpack_list(pack_list([1, 2, 3]), max_count=2) is None
    assert unpack_list(123) is None


def sample_lua(real_pack) -> bytes:
    packed = lua_pack(real_pack, [2330, 2331])
    return f"""
GuildProfessionsRecipesDB = {{
    ["schema"] = 2,
    ["guilds"] = {{
        ["Vieja"] = {{ ["seen"] = 1, ["members"] = {{}} }},
        ["Los de Siempre"] = {{
            ["seen"] = 1790000000,
            ["members"] = {{
                ["{GUID}"] = {{ ["t"] = 1790000000, ["n"] = "Tio  Paco", ["av"] = 500,
                    ["p"] = {{ [171] = {{ ["r"] = 250, ["m"] = 300, ["o"] = 1, ["k"] = "{packed}" }},
                              [186] = {{ ["r"] = 300, ["m"] = 300, ["o"] = 2, ["k"] = false }} }} }},
                ["Player-1-AB"] = {{ ["t"] = 5, ["n"] = "Sin Fecha", ["p"] = {{}} }},
                ["basura"] = {{ ["t"] = 1790000000 }},
            }},
        }},
    }},
}}
""".encode()


def test_read_and_payload(real_pack):
    data = read(parse(sample_lua(real_pack)))
    assert data.guild == "Los de Siempre"  # la última visitada
    [paco] = data.characters
    assert (paco.guid, paco.name, paco.t) == (GUID, "Tio Paco", 1790000000)
    assert paco.professions[171].recipes == [2330, 2331]
    assert paco.professions[186].recipes == []
    assert data.unreadable == 0
    assert professions_payload(data, "0.5.0") == {
        "addonVersion": "0.5.0",
        "guild": "Los de Siempre",
        "characters": [
            {
                "guid": GUID,
                "name": "Tio Paco",
                "t": 1790000000,
                "professions": [{"skillLine": 171, "rank": 250, "maxRank": 300}, {"skillLine": 186, "rank": 300, "maxRank": 300}],
            }
        ],
    }


def test_read_old_variable_name_and_errors(real_pack):
    old = sample_lua(real_pack).replace(b"GuildProfessionsRecipesDB", b"GuildRecipesDB")
    assert read(parse(old)).characters[0].name == "Tio Paco"
    assert read(parse(old), "vieja").characters == []
    with pytest.raises(NotAddonFile):
        read(parse(b"OtroAddonDB = {}"))
    with pytest.raises(NotAddonFile):
        read(parse(old), "No Existe")


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_web_sends_key_and_maps_errors():
    session = FakeSession(FakeResponse(200, {"received": 1, "updated": 1}), FakeResponse(401, {"error": "Esa clave no existe"}), FakeResponse(503, {}))
    web = Web("https://lds.example/", " gpra_x ", session)
    assert web.send_professions({"characters": []})["updated"] == 1
    method, url, kwargs = session.calls[0]
    assert (method, url, kwargs["headers"]["Authorization"]) == ("POST", "https://lds.example/api/addon/professions", "Bearer gpra_x")
    with pytest.raises(WebError) as bad_key:
        web.send_professions({})
    assert not bad_key.value.retry and "no existe" in str(bad_key.value)
    with pytest.raises(WebError) as down:
        web.send_professions({})
    assert down.value.retry


def test_retries_only_what_can_get_better():
    session = FakeSession(FakeResponse(503, {}), FakeResponse(429, {}), FakeResponse(200, {"received": 2}))
    waits = []
    assert send_with_retries(Web("https://w", "k", session), {}, sleep=waits.append) == {"received": 2}
    assert waits == [5, 15]
    session = FakeSession(FakeResponse(403, {"error": "ya no es oficial"}))
    assert send_with_retries(Web("https://w", "k", session), {}, sleep=waits.append) is None


def test_summary_text():
    text = summary_text({"received": 3, "updated": 1, "unchanged": 1, "skippedOld": 1, "unmatched": ["Nadie Raro"]})
    assert text == "3 personajes: 1 actualizados, 1 sin cambios, 1 ignorados por ser datos más viejos. No están en la web: Nadie Raro"
