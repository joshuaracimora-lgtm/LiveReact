"""Roadmap item 6: the `livereact-fixture/1` definition format (fixtures/definition.py)."""
from __future__ import annotations

import copy
import json

import pytest

from livereact.fixtures.definition import (
    CAPABILITY_TYPES,
    CATEGORIES,
    FUNCTIONS,
    SCHEMA,
    DefinitionError,
    FixtureDefinition,
    kind_of,
    load_definition,
    validate,
)
from livereact.fixtures.profiles import ChannelKind


def _par() -> dict:
    return {
        "schema": SCHEMA,
        "id": "acme_par_rgb",
        "manufacturer": "Acme",
        "model": "Par RGB",
        "category": "par",
        "source": {"kind": "user", "confirmed": False},
        "modes": [{
            "name": "4ch",
            "channels": [
                {"offset": 0, "function": "dimmer"},
                {"offset": 1, "function": "red"},
                {"offset": 2, "function": "green"},
                {"offset": 3, "function": "blue"},
            ],
        }],
    }


def _head() -> dict:
    return {
        "schema": SCHEMA,
        "id": "acme_spot",
        "manufacturer": "Acme",
        "model": "Spot 60",
        "category": "moving_head",
        "source": {"kind": "ofl", "ofl_key": "acme/spot-60", "license": "CC-BY-SA-4.0",
                   "attribution": "Open Fixture Library contributors", "confirmed": True,
                   "confirmed_at": "2026-10-28T03:00:00"},
        "physical": {"pan_deg": 540, "tilt_deg": 270, "pan_max_deg_s": 300},
        "modes": [{
            "name": "8ch",
            "channels": [
                {"offset": 0, "function": "pan", "default": 128},
                {"offset": 1, "function": "pan_fine", "fine_of": 0},
                {"offset": 2, "function": "tilt", "default": 128},
                {"offset": 3, "function": "tilt_fine", "fine_of": 2},
                {"offset": 4, "function": "dimmer"},
                {"offset": 5, "function": "shutter", "default": 8, "capabilities": [
                    {"range": [0, 7], "type": "shutter", "label": "closed"},
                    {"range": [8, 15], "type": "shutter", "label": "open"},
                    {"range": [16, 255], "type": "strobe", "speed_hz": [1, 20], "label": "strobe slow-fast"},
                ]},
                {"offset": 6, "function": "color_wheel", "capabilities": [
                    {"range": [0, 9], "type": "color_wheel", "color": "#ffffff", "label": "open"},
                    {"range": [10, 19], "type": "color_wheel", "color": "#ff0000", "label": "red",
                     "confidence": 0.9, "source_page": 7},
                ]},
                {"offset": 7, "function": "reset", "capabilities": [
                    {"range": [0, 199], "type": "no_function"},
                    {"range": [200, 255], "type": "reset", "label": "reset after 3 s"},
                ]},
            ],
        }],
    }


def _laser() -> dict:
    return {
        "schema": SCHEMA,
        "id": "acme_laser",
        "manufacturer": "Acme",
        "model": "Beam 1W",
        "category": "laser",
        "source": {"kind": "manual_ai", "pdf_sha256": "ab" * 32, "confirmed": False},
        "physical": {"laser": {"class": "4", "power_mw": 1000}},
        "modes": [{
            "name": "3ch",
            "channels": [
                {"offset": 0, "function": "mode"},
                {"offset": 1, "function": "laser_pattern"},
                {"offset": 2, "function": "laser_color"},
            ],
        }],
        "laser_intents": {"blank": {"0": 0}, "line_static": {"0": 100, "1": 12},
                          "fan": {"0": 100, "1": 40}, "chop_channel": None},
    }


# --- loading ---

def test_load_par_fields_and_defaults():
    d = load_definition(_par())
    assert isinstance(d, FixtureDefinition)
    assert (d.id, d.manufacturer, d.model, d.category) == ("acme_par_rgb", "Acme", "Par RGB", "par")
    assert d.source.kind == "user" and d.source.confirmed is False
    mode = d.mode("4ch")
    assert [c.function for c in mode.channels] == ["dimmer", "red", "green", "blue"]
    assert all(c.default == 0 and c.fine_of is None and c.capabilities == () for c in mode.channels)
    assert d.physical.pan_deg is None and d.physical.laser is None
    assert d.laser_intents is None
    assert validate(d) == []


def test_load_head_capabilities_and_physical():
    d = load_definition(_head())
    assert validate(d) == []
    assert d.physical.pan_deg == 540 and d.physical.pan_max_deg_s == 300
    assert d.source.ofl_key == "acme/spot-60" and d.source.confirmed_at == "2026-10-28T03:00:00"
    wheel = d.mode("8ch").channels[6]
    red = wheel.capabilities[1]
    assert red.range == (10, 19) and red.color == "#ff0000" and red.label == "red"
    assert red.confidence == 0.9 and red.source_page == 7
    strobe = d.mode("8ch").channels[5].capabilities[2]
    assert strobe.speed_hz == (1.0, 20.0)
    assert d.mode("8ch").channels[1].fine_of == 0


def test_load_laser_intents_and_laser_physical():
    d = load_definition(_laser())
    assert validate(d) == []
    assert d.physical.laser.laser_class == "4" and d.physical.laser.power_mw == 1000
    assert d.laser_intents.blank == {0: 0}
    assert d.laser_intents.line_static == {0: 100, 1: 12}
    assert d.laser_intents.fan == {0: 100, 1: 40}
    assert d.laser_intents.chop_channel is None


def test_load_from_json_file(tmp_path):
    p = tmp_path / "par.json"
    p.write_text(json.dumps(_par()), encoding="utf-8")
    assert load_definition(p).id == "acme_par_rgb"
    assert load_definition(str(p)).id == "acme_par_rgb"


def test_round_trip_to_dict():
    for raw in (_par(), _head(), _laser()):
        d = load_definition(raw)
        again = load_definition(d.to_dict())
        assert again == d
        assert json.loads(json.dumps(d.to_dict())) == d.to_dict()


def test_mode_lookup_default_and_unknown():
    d = load_definition(_head())
    assert d.mode().name == "8ch"
    with pytest.raises(KeyError):
        d.mode("nope")


def test_wrong_schema_or_missing_field_raises():
    raw = _par()
    raw["schema"] = "livereact-fixture/99"
    with pytest.raises(DefinitionError):
        load_definition(raw)
    raw = _par()
    del raw["id"]
    with pytest.raises(DefinitionError):
        load_definition(raw)
    raw = _par()
    raw["modes"][0]["channels"][0]["offset"] = "zero"
    with pytest.raises(DefinitionError):
        load_definition(raw)


# --- validate ---

def _errors(raw: dict) -> list[str]:
    return validate(load_definition(raw))


def test_validate_offset_gap():
    raw = _par()
    raw["modes"][0]["channels"][3]["offset"] = 4
    errs = _errors(raw)
    assert errs and any("offset" in e for e in errs)


def test_validate_duplicate_offset():
    raw = _par()
    raw["modes"][0]["channels"][2]["offset"] = 1
    assert any("offset" in e for e in _errors(raw))


def test_validate_overlapping_ranges():
    raw = _head()
    raw["modes"][0]["channels"][5]["capabilities"][1]["range"] = [5, 15]
    assert any("overlap" in e for e in _errors(raw))


@pytest.mark.parametrize("rng", [[-1, 7], [250, 256], [20, 10]])
def test_validate_range_outside_0_255_or_reversed(rng):
    raw = _head()
    raw["modes"][0]["channels"][6]["capabilities"][0]["range"] = rng
    assert any("range" in e for e in _errors(raw))


def test_validate_default_outside_0_255():
    raw = _par()
    raw["modes"][0]["channels"][0]["default"] = 300
    assert any("default" in e for e in _errors(raw))


def test_validate_unpaired_fine_channel():
    raw = _head()
    del raw["modes"][0]["channels"][1]["fine_of"]
    assert any("fine" in e for e in _errors(raw))
    raw = _head()
    raw["modes"][0]["channels"][1]["fine_of"] = 9          # no such channel
    assert any("fine" in e for e in _errors(raw))
    raw = _head()
    raw["modes"][0]["channels"][3]["fine_of"] = 0          # tilt_fine paired with pan
    assert any("fine" in e for e in _errors(raw))
    raw = _head()
    raw["modes"][0]["channels"][3]["function"] = "pan_fine"  # two fines on one coarse
    raw["modes"][0]["channels"][3]["fine_of"] = 0
    assert any("fine" in e for e in _errors(raw))


def test_validate_default_inside_reset_range():
    raw = _head()
    raw["modes"][0]["channels"][7]["default"] = 220
    assert any("default" in e and "reset" in e for e in _errors(raw))
    raw = _head()
    raw["modes"][0]["channels"][7]["capabilities"][1]["type"] = "maintenance"
    raw["modes"][0]["channels"][7]["default"] = 255
    assert any("default" in e for e in _errors(raw))


def test_validate_unknown_vocabulary():
    raw = _par()
    raw["category"] = "fog"
    assert any("category" in e for e in _errors(raw))
    raw = _par()
    raw["modes"][0]["channels"][0]["function"] = "brightness"
    assert any("function" in e for e in _errors(raw))
    raw = _head()
    raw["modes"][0]["channels"][6]["capabilities"][0]["type"] = "colour"
    assert any("type" in e for e in _errors(raw))
    raw = _par()
    raw["source"]["kind"] = "web"
    assert any("source" in e for e in _errors(raw))


def test_validate_laser_intents_reference_real_offsets():
    raw = _laser()
    raw["laser_intents"]["fan"] = {"7": 40}
    assert any("laser_intents" in e for e in _errors(raw))
    raw = _laser()
    raw["laser_intents"]["blank"] = {"0": 300}
    assert any("laser_intents" in e for e in _errors(raw))


def test_validate_empty_and_duplicate_modes():
    raw = _par()
    raw["modes"] = []
    assert any("mode" in e for e in _errors(raw))
    raw = _par()
    raw["modes"].append(copy.deepcopy(raw["modes"][0]))
    assert any("mode" in e for e in _errors(raw))


def test_validate_does_not_mutate():
    raw = _head()
    d = load_definition(raw)
    before = d.to_dict()
    validate(d)
    assert d.to_dict() == before


# --- kind_of ---

@pytest.mark.parametrize("function,kind", [
    ("dimmer", ChannelKind.DIMMER),
    ("red", ChannelKind.COLOR), ("green", ChannelKind.COLOR), ("blue", ChannelKind.COLOR),
    ("white", ChannelKind.COLOR), ("amber", ChannelKind.COLOR), ("uv", ChannelKind.COLOR),
    ("color_wheel", ChannelKind.COLOR),
    ("pan", ChannelKind.MOVEMENT), ("pan_fine", ChannelKind.MOVEMENT),
    ("tilt", ChannelKind.MOVEMENT), ("tilt_fine", ChannelKind.MOVEMENT),
    ("pan_tilt_speed", ChannelKind.OTHER),   # the native SHEHDS speed channel is OTHER
    ("shutter", ChannelKind.STROBE), ("strobe", ChannelKind.STROBE),
    ("gobo", ChannelKind.OTHER), ("prism", ChannelKind.OTHER), ("focus", ChannelKind.OTHER),
    ("zoom", ChannelKind.OTHER), ("macro", ChannelKind.OTHER), ("mode", ChannelKind.OTHER),
    ("reset", ChannelKind.OTHER), ("generic", ChannelKind.OTHER),
    ("laser_pattern", ChannelKind.LASER), ("laser_x", ChannelKind.LASER), ("laser_y", ChannelKind.LASER),
    ("laser_rotate", ChannelKind.LASER), ("laser_color", ChannelKind.LASER),
])
def test_kind_of_function(function, kind):
    assert kind_of(function) == kind


def test_kind_of_every_function_is_mapped():
    for f in FUNCTIONS:
        assert isinstance(kind_of(f), ChannelKind)
    with pytest.raises(KeyError):
        kind_of("brightness")


def test_kind_of_laser_category_is_always_laser():
    """Every channel of a laser fixture is LASER, so the finalize gate holds the whole head."""
    for f in ("mode", "dimmer", "red", "generic", "pan"):
        assert kind_of(f, category="laser") == ChannelKind.LASER


def test_channel_kinds_for_a_mode():
    d = load_definition(_laser())
    assert d.channel_kinds() == [ChannelKind.LASER] * 3
    assert load_definition(_par()).channel_kinds("4ch") == [
        ChannelKind.DIMMER, ChannelKind.COLOR, ChannelKind.COLOR, ChannelKind.COLOR]


def test_vocabularies():
    assert set(CATEGORIES) == {"par", "wash_bar", "moving_head", "strobe", "dimmer", "laser", "other"}
    assert set(FUNCTIONS) <= set(CAPABILITY_TYPES)
    assert {"maintenance", "no_function"} <= set(CAPABILITY_TYPES)
