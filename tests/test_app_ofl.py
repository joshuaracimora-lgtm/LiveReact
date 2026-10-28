"""Roadmap item 24: the Open Fixture Library converter (fixtures/ofl.py). Test data under
tests/data/ofl/ was written for these tests in OFL's format; nothing is fetched."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from livereact.fixtures.definition import validate
from livereact.fixtures.generic import GenericFixture, GenericLaser, GenericMovingHead, make_fixture
from livereact.fixtures.ofl import OFL_ATTRIBUTION, ofl_to_definition
from livereact.fixtures.profiles import ChannelKind

DATA = Path(__file__).resolve().parent / "data" / "ofl"


def _load(name: str, key: str, **kw):
    return ofl_to_definition(json.loads((DATA / f"{name}.json").read_text(encoding="utf-8")), key, **kw)


def test_par_modes_functions_and_validity():
    d = _load("test-par-rgbw", "test-maker/test-par-rgbw")
    assert validate(d) == []
    assert d.id == "ofl_test_maker_test_par_rgbw" and d.model == "Test Par RGBW"
    assert d.manufacturer == "Test Maker" and d.category == "par"
    assert [m.name for m in d.modes] == ["4ch", "8ch"]
    assert [c.function for c in d.mode("4ch").channels] == ["red", "green", "blue", "white"]
    eight = d.mode("8ch").channels
    assert [c.function for c in eight] == ["dimmer", "red", "green", "blue", "white", "generic", "strobe", "generic"]
    assert eight[7].capabilities[0].label == "unused"          # an OFL null channel


def test_source_is_ofl_unconfirmed_with_attribution():
    d = _load("test-par-rgbw", "test-maker/test-par-rgbw", manufacturer="Test Maker Ltd", license="CC-BY-SA-4.0")
    assert d.source.kind == "ofl" and d.source.ofl_key == "test-maker/test-par-rgbw"
    assert d.source.confirmed is False and d.source.license == "CC-BY-SA-4.0"
    assert OFL_ATTRIBUTION in d.source.attribution and "LiveReact tests" in d.source.attribution
    assert d.manufacturer == "Test Maker Ltd"


def test_strobe_capability_range_and_speed():
    d = _load("test-par-rgbw", "m/p")
    strobe = d.mode("8ch").channels[6]
    caps = strobe.capabilities
    assert caps[0].range == (0, 9) and caps[0].type == "no_function"
    assert caps[1].range == (10, 255) and caps[1].type == "strobe" and caps[1].speed_hz == (1.0, 25.0)


def test_spot_fine_aliases_wheels_shutter_physical():
    d = _load("test-spot", "test-maker/test-spot-60")
    assert validate(d) == []
    assert d.category == "moving_head"
    assert d.physical.pan_deg == 540 and d.physical.tilt_deg == 270
    ext = d.mode("13ch").channels
    assert [c.function for c in ext[:5]] == ["pan", "pan_fine", "tilt", "tilt_fine", "generic"]
    assert ext[1].fine_of == 0 and ext[3].fine_of == 2
    assert ext[0].default == 128 and ext[2].default == 128          # "50%" and 128
    shutter = ext[7]
    assert shutter.function == "shutter" and shutter.default == 8
    assert [c.type for c in shutter.capabilities] == ["shutter", "shutter", "strobe"]
    assert shutter.capabilities[2].speed_hz == (1.0, 20.0)
    wheel = ext[8]
    assert wheel.function == "color_wheel"
    assert [c.color for c in wheel.capabilities[:3]] == ["#ffffff", "#ff0000", "#0000ff"]
    assert wheel.capabilities[3].speed_hz is None                  # "slow CW" is not a frequency
    assert ext[9].function == "gobo"
    assert ext[11].function == "generic"                           # Iris: no LiveReact function
    reset = ext[12]
    assert reset.function == "reset" and reset.capabilities[1].type == "maintenance"
    assert reset.capabilities[1].label == "Reset all"


def test_converted_spot_drives_as_a_generic_moving_head():
    d = _load("test-spot", "m/s")
    fx = make_fixture(d, "13ch")
    assert isinstance(fx, GenericMovingHead) and fx.channel_count == 13

    class U:
        data = bytearray(512)

        def set(self, ch, v):
            self.data[ch - 1] = v
    u = U()
    fx.boot_defaults(u)
    assert u.data[7] == 8                                          # shutter open at boot
    fx.set_color(u, 1, 0, 0)
    assert u.data[8] == 14                                         # the red slot


def test_laser_category_makes_every_channel_laser_and_unconfirmed():
    d = _load("test-laser", "m/l")
    assert validate(d) == [] and d.category == "laser"
    assert d.channel_kinds() == [ChannelKind.LASER] * 3
    fx = make_fixture(d)
    assert isinstance(fx, GenericLaser) and not fx.ready
    assert d.mode().name == "3-channel"


def test_unknown_capability_types_become_generic():
    ofl = {"name": "X", "categories": ["Other"],
           "availableChannels": {"Fog": {"capability": {"type": "Fog", "fogType": "Haze"}},
                                 "Weird": {"capabilities": [{"dmxRange": [0, 255], "type": "BeamAngle"}]}},
           "modes": [{"name": "2ch", "channels": ["Fog", "Weird"]}]}
    d = ofl_to_definition(ofl, "m/x")
    assert validate(d) == [] and d.category == "other"
    assert [c.function for c in d.mode().channels] == ["generic", "generic"]
    assert [c.capabilities[0].type for c in d.mode().channels] == ["generic", "generic"]


def test_matrix_and_unknown_entries_do_not_crash():
    ofl = {"name": "X", "categories": ["Pixel Bar"],
           "availableChannels": {"Red": {"capability": {"type": "ColorIntensity", "color": "Red"}}},
           "modes": [{"name": "m", "channels": ["Red", {"insert": "matrixChannels"}, "Missing"]}]}
    d = ofl_to_definition(ofl, "m/x")
    assert d.category == "wash_bar"
    assert [c.function for c in d.mode().channels] == ["red", "generic", "generic"]
    assert isinstance(make_fixture(d), GenericFixture)


@pytest.mark.parametrize("cats,expected", [
    (["Laser", "Moving Head"], "laser"), (["Scanner"], "moving_head"), (["Strobe", "Color Changer"], "strobe"),
    (["Dimmer"], "dimmer"), ([], "other"),
])
def test_category_mapping(cats, expected):
    d = ofl_to_definition({"name": "x", "categories": cats, "availableChannels": {}, "modes": []}, "m/x")
    assert d.category == expected
