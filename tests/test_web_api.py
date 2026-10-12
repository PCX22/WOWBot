"""El bot y la API de grupos de la web, contra un servidor de pruebas que imita /api/bot/* (las mismas rutas,
cuerpos y errores que src/server/bot-api.ts de la web)."""
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web

from bot.cogs.grupos import due, render, sign_up
from bot.web import Directory
from bot.web_api import Group, WebApi, WebApiError

T0 = datetime(2026, 11, 6, 19, 0, tzinfo=timezone.utc)


def web_group(**over):
    group = {
        "id": "9251edc7-b425-4a99-b1bb-6e1c6660f2b7",
        "kind": "raid",
        "instanceKey": "nucleo_magma",
        "instanceName": "Núcleo de Magma",
        "startsAt": "2026-11-06T19:00:00.000Z",
        "expiresAt": None,
        "leader": {"userId": "u1", "discordId": "100000000000000001", "name": "Ironvow"},
        "tanks": 1,
        "healers": 1,
        "dps": 3,
        "notes": "Traed resistencia al fuego",
        "status": "open",
        "remindedAt": None,
        "discordChannelId": None,
        "discordMessageId": None,
        "signups": [],
    }
    group.update(over)
    return group


def signup(discord_id, name, wow_class, role, minute, bench=False):
    return {
        "userId": f"u{discord_id}",
        "discordId": str(discord_id),
        "characterId": f"c-{name}",
        "characterName": name,
        "wowClass": wow_class,
        "level": 60,
        "role": role,
        "bench": bench,
        "queuedAt": f"2026-11-01T10:{minute:02d}:00.000Z",
    }


class FakeWeb:
    """Lo mínimo de la web: guarda un grupo y aplica las reglas que importan al bot."""

    def __init__(self):
        self.group = web_group()
        self.requests = []

    def app(self):
        app = web.Application()
        app.router.add_route("*", "/api/bot/groups", self.groups)
        app.router.add_route("*", "/api/bot/groups/{id}", self.one)
        app.router.add_route("*", "/api/bot/groups/{id}/signups", self.signups)
        app.router.add_get("/api/bot/members", self.members)
        return app

    async def members(self, request):
        body = await self._body(request)
        member = {"discordId": "100000000000000001", "tier": "officer", "characters": [{
            "id": "c1", "name": "Tor", "surname": "Whitecross", "fullName": "Tor Whitecross", "class": "warrior",
            "spec": "Protection", "role": "tank", "level": 60, "isMain": True, "verified": True,
            "professions": [{"profession": "mining", "skill": 300}],
        }]}
        return web.json_response({"guild": {"slug": body["guild"], "name": "OSM", "timezone": "Europe/Madrid"}, "members": [member]})

    async def _body(self, request):
        if request.headers.get("Authorization") != "Bearer secreto":
            raise web.HTTPNotFound(text='{"error":"Not found"}', content_type="application/json")
        body = dict(request.query) if request.method == "GET" else await request.json()
        self.requests.append((request.method, request.path, body))
        return body

    async def groups(self, request):
        body = await self._body(request)
        if request.method == "GET":
            return web.json_response({"groups": [self.group]})
        self.group = web_group(instanceName=body["instanceName"], kind=body["kind"], notes=body["notes"])
        return web.json_response({"group": self.group}, status=201)

    async def one(self, request):
        body = await self._body(request)
        if request.method == "PATCH":
            if body.get("status") == "cancelled" and body.get("actorDiscordId") != self.group["leader"]["discordId"]:
                return web.json_response({"error": "Solo el líder del grupo o un oficial pueden hacer eso."}, status=403)
            for key in ("status", "discordChannelId", "discordMessageId"):
                if key in body:
                    self.group[key] = body[key]
            if body.get("reminded"):
                self.group["remindedAt"] = "2026-11-06T18:30:00.000Z"
        return web.json_response({"group": self.group})

    async def signups(self, request):
        body = await self._body(request)
        actor = body["actorDiscordId"]
        if request.method == "POST":
            if body["role"] == "tank" and body["characterId"] == "c-Agnes":
                return web.json_response({"error": "Ese personaje no puede ir de tanque."}, status=400)
            others = [s for s in self.group["signups"] if s["discordId"] != actor]
            name = body["characterId"].removeprefix("c-")
            self.group["signups"] = others + [signup(actor, name, "warrior", body["role"], len(others))]
        elif request.method == "DELETE":
            self.group["signups"] = [s for s in self.group["signups"] if s["discordId"] != (body.get("targetDiscordId") or actor)]
        return web.json_response({"group": self.group})


@pytest.fixture
async def fake(aiohttp_server):
    fake = FakeWeb()
    server = await aiohttp_server(fake.app())
    api = WebApi(str(server.make_url("")), "secreto", "osm")
    yield fake, api
    await api.close()


def test_group_from_web_and_render():
    group = Group.from_web(web_group(signups=[
        signup(1, "Tor Whitecross", "warrior", "tank", 1),
        signup(2, "Godfrey Shieldmere", "warrior", "tank", 2),
        signup(3, "Agnes Lambsbury", "priest", "healer", 3, bench=True),
    ]))
    assert (group.kind, group.status, group.leader_id, group.starts_at) == ("raid", "abierto", 100000000000000001, T0)
    assert [s.role for s in group.signups] == ["tanque", "tanque", "sanador"]
    fields = {f.name: f.value for f in render(group).fields}
    assert fields["🛡️ Tanque (1/1)"] == "1. Tor Whitecross (Guerrero)"
    assert fields["🪑 Reserva (2)"] == "Godfrey Shieldmere (Guerrero) · Tanque\nAgnes Lambsbury (Sacerdote) · Sanador"


def test_due_reminders_and_endings():
    upcoming = Group.from_web(web_group(id="a", startsAt="2026-11-06T19:00:00.000Z"))
    reminded = Group.from_web(web_group(id="b", startsAt="2026-11-06T19:10:00.000Z", remindedAt="2026-11-06T18:40:00.000Z"))
    old = Group.from_web(web_group(id="c", startsAt="2026-11-06T12:00:00.000Z"))
    lfg = Group.from_web(web_group(id="d", kind="lfg", startsAt="2026-11-06T16:00:00.000Z", expiresAt="2026-11-06T18:00:00.000Z"))
    cancelled = Group.from_web(web_group(id="e", status="cancelled", startsAt="2026-11-06T12:00:00.000Z"))
    to_remind, to_finish = due([upcoming, reminded, old, lfg, cancelled], T0 - timedelta(minutes=20), 30)
    assert [g.id for g in to_remind] == ["a"]
    assert sorted(g.id for g in to_finish) == ["c", "d"]


async def test_api_round_trip(fake):
    web_fake, api = fake
    created = await api.create_group(100000000000000001, kind="mazmorra", instance_key="stratholme", instance_name="Stratholme",
                                     starts_at=T0, expires_at=None, tanks=1, healers=1, dps=3, notes="")
    method, path, body = web_fake.requests[-1]
    assert (method, path, body["guild"], body["actorDiscordId"], body["kind"]) == ("POST", "/api/bot/groups", "osm", "100000000000000001", "dungeon")
    assert created.kind == "mazmorra"

    joined = await api.sign_up(created.id, 200, "c-Tor", "tanque")
    assert web_fake.requests[-1][2]["role"] == "tank"
    assert [(s.character_name, s.role) for s in joined.signups] == [("Tor", "tanque")]

    # El bot por su cuenta (sin usuario): guarda el mensaje de Discord y marca el recordatorio.
    attached = await api.update_group(created.id, channel_id=111, message_id=222, reminded=True)
    assert "actorDiscordId" not in web_fake.requests[-1][2]
    assert (attached.channel_id, attached.message_id, attached.reminded) == (111, 222, True)

    assert [g.id for g in await api.list_groups()] == [created.id]
    left = await api.remove(created.id, 200)
    assert left.signups == []


async def test_members_through_the_api(fake):
    web_fake, api = fake
    snapshot = await Directory(api.list_members).snapshot()
    method, path, body = web_fake.requests[-1]
    assert (method, path, body) == ("GET", "/api/bot/members", {"guild": "osm"})  # el slug va en la dirección
    tor = snapshot.find("Tor Whitecross")
    assert (tor.class_key, tor.role, tor.discord_id, [p.name for p in tor.professions]) == ("guerrero", "tanque", 100000000000000001, ["Minería"])
    assert snapshot.is_officer(100000000000000001)


async def test_api_errors_are_shown_as_is(fake):
    _, api = fake
    with pytest.raises(WebApiError, match="no puede ir de tanque") as bad_role:
        await api.sign_up("g", 300, "c-Agnes", "tanque")
    assert bad_role.value.status == 400
    with pytest.raises(WebApiError, match="Solo el líder"):
        await api.update_group("g", 999, status="cancelado")
    wrong_key = WebApi(api.base_url, "otra", "osm")
    with pytest.raises(WebApiError, match="BOT_API_TOKEN"):
        await wrong_key.list_groups()
    await wrong_key.close()


async def test_web_down():
    api = WebApi("http://127.0.0.1:9", "secreto", "osm")
    import bot.web_api as module

    module.RETRY_DELAYS, saved = (0, 0), module.RETRY_DELAYS
    try:
        with pytest.raises(WebApiError, match="no responde"):
            await api.list_groups()
    finally:
        module.RETRY_DELAYS = saved
        await api.close()


class FakeBot:
    def __init__(self, api):
        self.web_api = api

    def get_channel(self, _):
        return None


async def test_sign_up_reply(fake):
    _, api = fake
    bot = FakeBot(api)
    assert await sign_up(bot, "g", 200, "c-Tor", "tanque") == "Apuntado con Tor como Tanque con plaza."
    assert await sign_up(bot, "g", 300, "c-Agnes", "tanque") == "Ese personaje no puede ir de tanque."
