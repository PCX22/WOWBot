"""Permisos por roles de Discord: PERMISO_CREAR_RAID y ROLES_OFICIAL."""
from dataclasses import dataclass, field
from types import SimpleNamespace

from bot.permisos import can_create_raid, has_any_role, is_officer, parse_roles, role_key


@dataclass
class FakeRole:
    id: int
    name: str


@dataclass
class FakeMember:
    roles: list = field(default_factory=list)
    manage_guild: bool = False

    @property
    def guild_permissions(self):
        return SimpleNamespace(manage_guild=self.manage_guild)


def config(officers="", raids="", officer_role_id=None):
    return SimpleNamespace(officer_roles=parse_roles(officers), raid_creator_roles=parse_roles(raids), officer_role_id=officer_role_id)


def test_role_names_ignore_case_accents_and_emoji():
    assert role_key("⚔️ Líder de Raid") == "lider de raid"
    assert parse_roles(" Oficial , Líder de raid,,") == ("Oficial", "Líder de raid")
    member = FakeMember([FakeRole(1, "🛡️ OFICIAL"), FakeRole(42, "Raider")])
    assert has_any_role(member, ("oficial",))
    assert has_any_role(member, ("42",))  # por ID
    assert not has_any_role(member, ("Líder de raid",))
    assert not has_any_role(None, ("oficial",))


def test_officers():
    cfg = config(officers="Oficial, Consejo")
    assert is_officer(FakeMember([FakeRole(1, "Consejo")]), cfg)
    assert is_officer(FakeMember(manage_guild=True), cfg)  # quien gestiona el servidor, siempre
    assert not is_officer(FakeMember([FakeRole(2, "Raider")]), cfg)
    assert is_officer(FakeMember([FakeRole(77, "Lo que sea")]), config(officer_role_id=77))
    assert not is_officer(SimpleNamespace(roles=[]), cfg)  # un usuario fuera del servidor


def test_raid_creation():
    leader = FakeMember([FakeRole(1, "Líder de raid")])
    raider = FakeMember([FakeRole(2, "Raider")])
    officer = FakeMember([FakeRole(3, "Oficial")])
    cfg = config(officers="Oficial", raids="Líder de raid")
    assert can_create_raid(leader, cfg) and can_create_raid(officer, cfg)
    assert not can_create_raid(raider, cfg)
    assert can_create_raid(raider, config())  # sin PERMISO_CREAR_RAID, cualquiera
