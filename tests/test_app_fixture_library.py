"""Roadmap item 13: the fixture definition library (fixtures/library.py)."""
from __future__ import annotations

import json

import pytest

from livereact import userdata
from livereact.fixtures import library
from livereact.fixtures.definition import SCHEMA, DefinitionError, load_definition


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _raw(fid="acme_par", model="Par", modes=("4ch",)):
    return {"schema": SCHEMA, "id": fid, "manufacturer": "Acme", "model": model, "category": "par",
            "source": {"kind": "user"},
            "modes": [{"name": m, "channels": [{"offset": 0, "function": "dimmer"},
                                               {"offset": 1, "function": "red"}]} for m in modes]}


def test_parse_ref():
    assert library.parse_ref("acme_par@4ch") == ("acme_par", "4ch")
    assert library.parse_ref("acme_par") == ("acme_par", None)
    with pytest.raises(ValueError):
        library.parse_ref("@4ch")


def test_builtin_resolves(home):
    d, mode = library.resolve("rgb_par@4ch")
    assert d.id == "rgb_par" and mode == "4ch"
    d, mode = library.resolve("shehds_180w_mh")         # no mode -> the first
    assert mode == "15ch"


def test_unknown_id_or_mode(home):
    with pytest.raises(KeyError):
        library.resolve("nope@1ch")
    with pytest.raises(KeyError):
        library.resolve("rgb_par@99ch")


def test_save_then_resolve_from_user_folder(home):
    p = library.save_user_definition(load_definition(_raw()))
    assert p == userdata.paths().fixtures / "acme_par.json"
    assert json.loads(p.read_text(encoding="utf-8"))["id"] == "acme_par"
    d, mode = library.resolve("acme_par@4ch")
    assert d.model == "Par" and mode == "4ch"


def test_embedded_rig_definitions_win(home):
    library.save_user_definition(load_definition(_raw(model="User copy")))
    embedded = {"acme_par": _raw(model="Rig snapshot")}
    d, _ = library.resolve("acme_par@4ch", embedded)
    assert d.model == "Rig snapshot"
    d, _ = library.resolve("acme_par@4ch")
    assert d.model == "User copy"


def test_embedded_id_mismatch_is_refused(home):
    with pytest.raises(DefinitionError):
        library.resolve("acme_par", {"acme_par": _raw(fid="other")})


def test_invalid_definitions_are_refused(home):
    bad = _raw()
    bad["modes"][0]["channels"][1]["offset"] = 5
    with pytest.raises(DefinitionError):
        library.save_user_definition(load_definition(bad))
    with pytest.raises(DefinitionError):
        library.resolve("acme_par", {"acme_par": bad})
    (userdata.paths().fixtures / "broken.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(DefinitionError):
        library.resolve("broken")


def test_user_cannot_shadow_a_builtin(home):
    """A user copy of a native model could, say, mark the builtin laser confirmed."""
    raw = _raw(fid="shehds_3w_laser")
    with pytest.raises(ValueError):
        library.save_user_definition(load_definition(raw))
    (userdata.paths().fixtures / "rgb_par.json").write_text(json.dumps(_raw(fid="rgb_par", model="X")),
                                                            encoding="utf-8")
    d, _ = library.resolve("rgb_par")
    assert d.source.kind == "builtin"


def test_rig_snapshot_cannot_redefine_a_builtin(home):
    las = json.loads((library.BUILTIN_DIR / "shehds_3w_laser.json").read_text(encoding="utf-8"))
    las["source"]["confirmed"] = True
    las["laser_intents"] = {"blank": {"0": 0}, "line_static": {"0": 60}, "fan": {"0": 60}}
    d, _ = library.resolve("shehds_3w_laser", {"shehds_3w_laser": las})
    assert d.source.confirmed is False and d.laser_intents is None


@pytest.mark.parametrize("fid", ["../evil", "a/b", "CON", "", "x" * 81, "Acme Par"])
def test_unsafe_ids_are_refused(home, fid):
    with pytest.raises(ValueError):
        library.save_user_definition(load_definition(_raw(fid=fid)))


def test_list_all(home):
    library.save_user_definition(load_definition(_raw()))
    (userdata.paths().fixtures / "broken.json").write_text("{nope", encoding="utf-8")
    entries = library.list_definitions()
    by_id = {e.definition.id: e for e in entries}
    assert {"rgb_par", "ok103_wash_bar", "shehds_180w_mh", "shehds_3w_laser", "acme_par"} <= set(by_id)
    assert by_id["acme_par"].origin == "user" and by_id["rgb_par"].origin == "builtin"
    assert by_id["acme_par"].path == userdata.paths().fixtures / "acme_par.json"
    assert "broken" not in by_id                       # skipped, not fatal
    assert [e.definition.id for e in entries] == sorted(by_id, key=lambda i: (by_id[i].origin != "builtin", i))


def test_save_overwrites_and_leaves_no_temp(home):
    library.save_user_definition(load_definition(_raw(model="v1")))
    library.save_user_definition(load_definition(_raw(model="v2")))
    assert library.resolve("acme_par")[0].model == "v2"
    assert sorted(p.name for p in userdata.paths().fixtures.iterdir()) == ["acme_par.json"]


def test_refs_for_a_definition(home):
    d = load_definition(_raw(modes=("4ch", "8ch")))
    assert library.refs(d) == ["acme_par@4ch", "acme_par@8ch"]
