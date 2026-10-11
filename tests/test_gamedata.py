from bot.gamedata import GAME


def test_race_class_combos():
    assert GAME.is_valid_combo("tauren", "druida")
    assert GAME.is_valid_combo("enano", "paladin")
    assert not GAME.is_valid_combo("gnomo", "paladin")
    assert not GAME.is_valid_combo("humano", "chaman")
    assert not GAME.is_valid_combo("orco", "paladin")


def test_every_class_has_a_race_on_some_faction():
    for key in GAME.classes:
        assert GAME.races_for_class(key), key
    factions = {r.faction for r in GAME.races_for_class("paladin")}
    assert factions == {"alianza"}  # en vanilla los paladines son solo de la Alianza


def test_roles():
    assert GAME.classes["picaro"].roles == ("dps",)
    assert set(GAME.classes["druida"].roles) == {"tanque", "sanador", "dps"}


def test_instance_lookup():
    assert GAME.find_instance("mc").key == "nucleo_magma"
    assert GAME.find_instance("BWL").key == "guarida_alanegra"
    assert GAME.find_instance("nucleo de magma").key == "nucleo_magma"  # sin tilde
    assert GAME.find_instance("strat").key == "stratholme"
    assert GAME.find_instance("zz-no-existe") is None


def test_instance_slots_match_size():
    for inst in GAME.instances.values():
        assert sum(inst.slots.values()) == inst.size
    assert GAME.instances["zulgurub"].size == 20
    assert GAME.instances["cumbre_superior"].slots == {"tanque": 2, "sanador": 3, "dps": 5}


def test_profession_skill_lines():
    assert GAME.profession_by_skill_line(171).name == "Alquimia"
    assert GAME.profession_by_skill_line(129).primary is False
