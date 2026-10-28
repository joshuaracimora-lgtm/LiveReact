"""Roadmap item 17: presets (colour scheme, genre, event type) resolved to ManualParams values,
show_catalog() kwargs and a palette. Pure: nothing here touches the running show."""
from __future__ import annotations

import json

import pytest

from livereact import presets, userdata
from livereact.merge.merge import ManualParams
from livereact.presets import PresetEffect, PresetEvent, resolve


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_choices_cover_the_roadmap_lists():
    labels = lambda kind: [lbl for _k, lbl in presets.choices(kind)]  # noqa: E731
    assert labels("schemes")[:6] == ["Auto", "Warm", "Ice", "Neon", "Sunset", "Mono"]
    assert {k for k, _ in presets.choices("schemes")} >= {
        f"rb_{b}" for b in ("cool", "natural", "hot", "subtle", "warm", "vivid", "club1", "club2")}
    assert labels("genres") == ["Auto", "Techno", "House", "UK Garage", "Drum & Bass", "Open format", "Latin"]
    assert labels("event_types") == ["Auto", "Club", "Bar", "House party", "Wedding", "Corporate", "Festival"]


def test_auto_is_a_no_op():
    eff = resolve(PresetEvent())
    assert eff.manual == {} and eff.catalog_kwargs == {} and eff.palette is None
    assert eff.lasers_allowed and eff.movement
    assert eff.apply_to(ManualParams()) == ManualParams()
    armed = ManualParams(laser_enable=True, master_brightness=0.7)
    assert eff.apply_to(armed) == armed


def test_every_manual_key_is_a_manual_params_field():
    from dataclasses import fields
    names = {f.name for f in fields(ManualParams)}
    for kind in ("schemes", "genres"):
        for entry in presets.catalog()[kind].values():
            assert set(entry["manual"]) <= names


def test_wedding_forbids_lasers_and_caps_strobe():
    eff = resolve(PresetEvent(event_type="wedding"))
    assert not eff.lasers_allowed
    assert eff.manual["laser_enable"] is False
    assert eff.manual["strobe_cap"] < 1.0 and eff.strobe_cap_max == eff.manual["strobe_cap"]
    p = eff.apply_to(ManualParams(laser_enable=True, strobe_cap=1.0, master_brightness=1.0))
    assert p.laser_enable is False
    assert p.strobe_cap == eff.strobe_cap_max
    assert p.master_brightness <= eff.master_brightness_max


def test_ceilings_only_lower():
    eff = resolve(PresetEvent(event_type="wedding"))
    p = eff.apply_to(ManualParams(master_brightness=0.2, strobe_cap=0.1))
    # the preset's own values are applied, then clamped: never above the ceiling
    assert p.master_brightness <= eff.master_brightness_max and p.strobe_cap <= eff.strobe_cap_max


def test_ceiling_beats_genre_beats_scheme(monkeypatch):
    data = presets.catalog()
    monkeypatch.setitem(data["schemes"], "loud", {"label": "x", "palette": "p",
                                                  "manual": {"strobe_cap": 0.9, "warm_cool_bias": 0.5}})
    monkeypatch.setitem(data["genres"], "chill", {"label": "y", "catalog": {"genre": "chill"},
                                                  "manual": {"strobe_cap": 0.5}})
    eff = resolve(PresetEvent(scheme="loud", genre="chill"))
    assert eff.manual["strobe_cap"] == 0.5 and eff.manual["warm_cool_bias"] == 0.5
    eff = resolve(PresetEvent(scheme="loud", genre="chill", event_type="corporate"))
    assert eff.manual["strobe_cap"] == presets.catalog()["event_types"]["corporate"]["strobe_cap_max"]
    assert eff.manual["warm_cool_bias"] == 0.5
    assert eff.palette == "p" and eff.catalog_kwargs == {"genre": "chill"}
    assert eff.movement is False


def test_scheme_sets_palette_and_bias_genre_sets_catalog():
    eff = resolve(PresetEvent(scheme="warm", genre="techno"))
    assert eff.palette == "warm" and eff.manual["warm_cool_bias"] < 0
    assert eff.catalog_kwargs == {"genre": "techno"}
    assert "laser_enable" not in eff.manual        # a scheme or genre never arms anything


def test_no_preset_ever_arms_lasers():
    for s in presets.catalog()["schemes"]:
        for g in presets.catalog()["genres"]:
            for e in presets.catalog()["event_types"]:
                eff = resolve(PresetEvent(scheme=s, genre=g, event_type=e))
                assert eff.manual.get("laser_enable", False) is False
                assert eff.apply_to(ManualParams()).laser_enable is False


def test_unknown_keys_raise():
    for ev in (PresetEvent(scheme="x"), PresetEvent(genre="x"), PresetEvent(event_type="x")):
        with pytest.raises(ValueError):
            resolve(ev)


def test_effect_is_pure_data():
    a = resolve(PresetEvent(event_type="bar"))
    b = resolve(PresetEvent(event_type="bar"))
    assert a == b and isinstance(a, PresetEffect)
    a.manual["strobe_cap"] = 0.0
    assert resolve(PresetEvent(event_type="bar")).manual["strobe_cap"] != 0.0


def test_named_events_save_load_list(home):
    ev = PresetEvent(name="Friday Club Night", scheme="ice", genre="house", event_type="club")
    p = presets.save_event(ev)
    assert p.parent == userdata.paths().events and p.name == "friday_club_night.json"
    assert json.loads(p.read_text(encoding="utf-8"))["genre"] == "house"
    assert presets.load_event("Friday Club Night") == ev
    (userdata.paths().events / "junk.json").write_text("{", encoding="utf-8")
    assert presets.list_events() == ["Friday Club Night"]


def test_save_refuses_bad_events(home):
    with pytest.raises(ValueError):
        presets.save_event(PresetEvent(name="x", genre="polka"))
    with pytest.raises(ValueError):
        presets.save_event(PresetEvent(name="  !! "))
