"""Modo local (sin web): lector de los mensajes del canal, personajes y grupos guardados en el bot."""
from datetime import timedelta

import pytest

from bot.cogs.registro import save_character
from bot.intencion import parse
from bot.local import LocalDirectory, LocalGroupsApi
from bot.models import utcnow
from bot.web_api import WebApiError


@pytest.mark.parametrize(
    ("text", "class_key", "race_key", "role", "professions"),
    [
        ("Guerrero tanque, humano. Minero y herrero", "guerrero", "humano", "tanque", ["mineria", "herreria"]),
        ("pala heal enano", "paladin", "enano", "sanador", []),
        ("Me haré una Pícara gnoma, desuello + peletería", "picaro", "gnomo", "dps", ["desuello", "peleteria"]),
        ("Druida tauren feral tank / alquimia", "druida", "tauren", "tanque", ["alquimia"]),
        ("Elfa de la noche cazadora", "cazador", "elfo_noche", "dps", []),
        ("Sacerdote no muerto, healer", "sacerdote", "no_muerto", "sanador", []),
        ("ROGUE DPS", "picaro", None, "dps", []),
        ("mago trol, sastre + encantador", "mago", "trol", "dps", ["encantamiento", "sastreria"]),
    ],
)
def test_parse(text, class_key, race_key, role, professions):
    result = parse(text)
    assert (result.class_key, result.race_key, result.role) == (class_key, race_key, role)
    assert sorted(result.professions) == sorted(professions)


def test_parse_warnings_and_name():
    assert parse("Gnomo paladín").race_key is None  # un gnomo no puede ser paladín
    assert any("no puede ser" in w for w in parse("Gnomo paladín").warnings)
    assert parse("druida enano").race_key is None
    assert parse("guerrero").role is None and "no dice el rol" in parse("guerrero").warnings
    assert parse("Nombre: Tio Paco, guerrero tanque").name == "Tio Paco"
    assert not parse("¡Hola a todos! Qué ganas de que salga").ok
    # Palabras enteras: «palabra» no es «pala», «elfos» no basta para «elfo»… pero «paladín» sí es paladín.
    assert parse("una palabra cualquiera").class_key is None
    assert parse("paladín").class_key == "paladin"


async def test_local_directory_and_characters(sessions):
    async with sessions() as session:
        paco, _ = await save_character(session, 100, "Tio Paco", "guerrero", "enano", "tanque", professions=["mineria"])
        alt, _ = await save_character(session, 100, "Paquita", "sacerdote", "humano", "sanador")
        other, problem = await save_character(session, 200, "tio  paco", "mago", "gnomo", "dps")
        await session.commit()
    assert other is None and "ya es de otro miembro" in problem
    snapshot = await LocalDirectory(sessions).snapshot()
    chars = snapshot.characters_of(100)
    assert [c.full_name for c in chars] == ["Tio Paco", "Paquita"]  # el primero es el main
    assert (chars[0].class_key, chars[0].race_key, chars[0].role, chars[0].is_main) == ("guerrero", "enano", "tanque", True)
    assert [p.name for p in chars[0].professions] == ["Minería"]


async def test_local_groups(sessions):
    officers = {300}
    api = LocalGroupsApi(sessions, lambda discord_id: discord_id in officers)
    async with sessions() as session:
        tank, _ = await save_character(session, 100, "Tio Paco", "guerrero", "enano", "tanque")
        healer, _ = await save_character(session, 200, "Curandera", "sacerdote", "humano", "sanador")
        await session.commit()
        tank_id, healer_id = str(tank.id), str(healer.id)

    with pytest.raises(WebApiError, match="registra tu personaje"):
        await api.create_group(999, kind="raid", instance_key="nucleo_magma", instance_name="Núcleo de Magma",
                               starts_at=utcnow() + timedelta(hours=2), expires_at=None, tanks=1, healers=1, dps=3, notes="")
    group = await api.create_group(100, kind="raid", instance_key="nucleo_magma", instance_name="Núcleo de Magma",
                                   starts_at=utcnow() + timedelta(hours=2), expires_at=None, tanks=1, healers=1, dps=3, notes="")
    group = await api.sign_up(group.id, 100, tank_id, "tanque")
    with pytest.raises(WebApiError, match="no puede ir de tanque"):
        await api.sign_up(group.id, 200, healer_id, "tanque")
    with pytest.raises(WebApiError, match="no es tuyo"):
        await api.sign_up(group.id, 200, tank_id, "tanque")
    group = await api.sign_up(group.id, 200, healer_id, "sanador")
    assert [(s.character_name, s.class_key, s.role) for s in group.signups] == [("Tio Paco", "guerrero", "tanque"), ("Curandera", "sacerdote", "sanador")]

    # Gestionar: solo el líder o un oficial; el bot por su cuenta solo da por terminado.
    with pytest.raises(WebApiError, match="Solo el líder"):
        await api.update_group(group.id, 200, status="cerrado")
    with pytest.raises(WebApiError, match="Solo el líder"):
        await api.remove(group.id, 200, target=100)
    assert (await api.set_bench(group.id, 300, target=200)).signups[1].bench is True
    assert (await api.update_group(group.id, channel_id=11, message_id=22, reminded=True)).message_id == 22
    with pytest.raises(WebApiError, match="Solo el líder"):
        await api.update_group(group.id, None, status="cancelado")
    assert [g.id for g in await api.list_groups()] == [group.id]
    group = await api.remove(group.id, 200)
    assert [s.character_name for s in group.signups] == ["Tio Paco"]
    assert (await api.update_group(group.id, None, status="terminado")).status == "terminado"
    assert await api.list_groups() == []
