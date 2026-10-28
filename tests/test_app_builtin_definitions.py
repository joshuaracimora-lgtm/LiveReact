"""Roadmap item 7: the four native fixture models as `livereact-fixture/1` definitions.

The native classes in profiles.py stay what the show runs; the definitions document them and feed
the patch UI, so they must agree channel for channel with the class.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from livereact.fixtures import definition
from livereact.fixtures.definition import kind_of, load_definition, validate
from livereact.fixtures.profiles import FIXTURE_TYPES

BUILTIN = Path(definition.__file__).parent / "builtin"
NATIVE = sorted(FIXTURE_TYPES)


class _Universe:
    """A bare 512-channel write target (profiles.Buffer protocol)."""

    def __init__(self) -> None:
        self.data = bytearray(512)

    def set(self, channel_1based: int, value: int) -> None:
        self.data[channel_1based - 1] = value


def _native(type_name: str):
    return FIXTURE_TYPES[type_name](1, "t", "wash", "left")


def test_one_definition_per_native_model():
    assert NATIVE == ["ok103_wash_bar", "rgb_par", "shehds_180w_mh", "shehds_3w_laser"]
    assert sorted(p.stem for p in BUILTIN.glob("*.json")) == NATIVE


@pytest.mark.parametrize("type_name", NATIVE)
def test_definition_is_valid_and_builtin(type_name):
    d = load_definition(BUILTIN / f"{type_name}.json")
    assert validate(d) == []
    assert d.id == type_name
    assert d.source.kind == "builtin"
    assert len(d.modes) == 1


@pytest.mark.parametrize("type_name", NATIVE)
def test_channel_count_and_kinds_match_native(type_name):
    d = load_definition(BUILTIN / f"{type_name}.json")
    fx = _native(type_name)
    chans = d.mode().channels
    assert len(chans) == fx.channel_count
    assert [kind_of(c.function, d.category) for c in sorted(chans, key=lambda c: c.offset)] \
        == fx.channel_kinds()
    assert d.channel_kinds() == fx.channel_kinds()


@pytest.mark.parametrize("type_name", NATIVE)
def test_boot_defaults_match_native(type_name):
    d = load_definition(BUILTIN / f"{type_name}.json")
    fx = _native(type_name)
    uni = _Universe()
    fx.boot_defaults(uni)
    native = list(uni.data[:fx.channel_count])
    assert [c.default for c in sorted(d.mode().channels, key=lambda c: c.offset)] == native


def test_moving_head_physical_and_fine_pairs():
    d = load_definition(BUILTIN / "shehds_180w_mh.json")
    mh = FIXTURE_TYPES["shehds_180w_mh"]
    assert d.category == "moving_head"
    assert d.physical.pan_deg == mh.PAN_RANGE_DEG
    assert d.physical.tilt_deg == mh.TILT_RANGE_DEG
    assert d.physical.pan_max_deg_s == mh.PAN_MAX_DEG_PER_S
    ch = {c.offset: c for c in d.mode().channels}
    assert (ch[1].function, ch[1].fine_of) == ("pan_fine", 0)
    assert (ch[3].function, ch[3].fine_of) == ("tilt_fine", 2)


def test_moving_head_wheel_slots_match_native_table():
    """The wheel capabilities are the same slots profiles.rgb_to_color_wheel picks from."""
    from livereact.fixtures.profiles import color_wheel_to_rgb
    d = load_definition(BUILTIN / "shehds_180w_mh.json")
    wheel = next(c for c in d.mode().channels if c.function == "color_wheel")
    assert wheel.capabilities
    for cap in wheel.capabilities:
        lo, _hi = cap.range
        r, g, b = color_wheel_to_rgb(lo)
        assert cap.color == f"#{r:02x}{g:02x}{b:02x}"


def test_laser_colour_bands_hold_the_native_constants():
    laser = FIXTURE_TYPES["shehds_3w_laser"]
    d = load_definition(BUILTIN / "shehds_3w_laser.json")
    colour = next(c for c in d.mode().channels if c.function == "laser_color")
    by_label = {cap.label: cap.range for cap in colour.capabilities}
    for name in ("WHITE", "RED", "YELLOW", "GREEN", "CYAN", "BLUE", "PURPLE"):
        lo, hi = by_label[name.lower()]
        assert lo <= getattr(laser, f"COLOR_{name}") <= hi


def test_builtin_laser_is_not_confirmed_and_boots_off():
    """A definition-driven laser only outputs once confirmed with all intents (item 12); the
    native class drives the real unit, so the builtin definition stays unconfirmed."""
    d = load_definition(BUILTIN / "shehds_3w_laser.json")
    assert d.category == "laser"
    assert d.source.confirmed is False
    assert all(c.default == 0 for c in d.mode().channels)
    mode = d.mode().channels[0]
    assert mode.function == "mode"
    off = next(cap for cap in mode.capabilities if cap.range[0] == 0)
    assert off.range[1] >= 41   # CH1 0-41 is LASER OFF
