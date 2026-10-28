"""Roadmap item 12: GenericLaser. Driven only through a definition's laser_intents; dark unless the
definition is confirmed, and behind the same arm switch (apply_safety) and gate (apply_laser_gate)
as the native laser."""
from __future__ import annotations

import copy

import pytest

from livereact.fixtures.definition import SCHEMA, load_definition
from livereact.fixtures.generic import GenericFixture, GenericLaser, make_fixture
from livereact.fixtures.profiles import ChannelKind


class Uni:
    def __init__(self) -> None:
        self.data = bytearray(512)

    def set(self, channel_1based: int, value: int) -> None:
        self.data[channel_1based - 1] = value


RAW = {
    "schema": SCHEMA, "id": "las", "manufacturer": "m", "model": "m", "category": "laser",
    "source": {"kind": "user", "confirmed": True, "confirmed_at": "2026-10-28T03:00:00"},
    "modes": [{"name": "4ch", "channels": [
        {"offset": 0, "function": "mode"}, {"offset": 1, "function": "laser_pattern"},
        {"offset": 2, "function": "laser_x"}, {"offset": 3, "function": "dimmer"}]}],
    "laser_intents": {"blank": {"0": 5}, "line_static": {"0": 100, "1": 12, "3": 255},
                      "fan": {"0": 100, "1": 40, "2": 200, "3": 255}, "chop_channel": None},
}


def laser(confirmed=True, intents="all", chop=None, address=21):
    raw = copy.deepcopy(RAW)
    raw["source"]["confirmed"] = confirmed
    if intents is None:
        del raw["laser_intents"]
    elif intents != "all":
        raw["laser_intents"][intents] = {}
    if chop is not None:
        raw["laser_intents"]["chop_channel"] = chop
    return GenericLaser(load_definition(raw), address=address, label="LZ")


def test_every_channel_is_laser_kind():
    f = laser()
    assert f.channel_count == 4 and f.role == "laser"
    assert f.channel_kinds() == [ChannelKind.LASER] * 4


def test_make_fixture_builds_a_laser_for_the_laser_category():
    assert type(make_fixture(load_definition(RAW))) is GenericLaser


def test_no_semantic_setter_can_light_it():
    """It is not a GenericFixture: dimmer/colour/strobe intents from wash scenes do nothing."""
    f = laser()
    assert not isinstance(f, GenericFixture)
    u = Uni()
    f.set_intensity(u, 1.0)
    f.set_color(u, 1, 1, 1, 1)
    f.set_strobe(u, 1.0)
    assert u.data == bytearray(512)


def test_ready_needs_confirmation_and_all_three_intents():
    assert laser().ready
    assert not laser(confirmed=False).ready
    assert not laser(intents=None).ready
    for k in ("blank", "line_static", "fan"):
        assert not laser(intents=k).ready


def test_intents_write_their_values_and_zero_the_rest():
    f = laser()
    u = Uni()
    u.data[20:24] = bytes([9, 9, 9, 9])
    f.set_intent(u, "line_static")
    assert u.data[20:24] == bytes([100, 12, 0, 255])
    f.set_intent(u, "fan")
    assert u.data[20:24] == bytes([100, 40, 200, 255])
    f.set_intent(u, "blank")
    assert u.data[20:24] == bytes([5, 0, 0, 0])
    with pytest.raises(KeyError):
        f.set_intent(u, "circle")


def test_boot_is_blank_when_ready_else_zero():
    u = Uni()
    laser().boot_defaults(u)
    assert u.data[20:24] == bytes([5, 0, 0, 0])
    u = Uni()
    u.data[20:24] = bytes([9, 9, 9, 9])
    laser(confirmed=False).boot_defaults(u)
    assert u.data[20:24] == bytes(4)


def test_unconfirmed_laser_stays_dark_even_when_enabled():
    f = laser(confirmed=False)
    u = Uni()
    f.set_intent(u, "fan")
    assert u.data[20:24] == bytes(4)
    uni = bytearray(512)
    uni[20:24] = bytes([100, 40, 200, 255])          # a stray write from any other layer
    f.apply_safety(uni, laser_enabled=True)
    assert uni[20:24] == bytes(4)


def test_laser_without_intents_stays_dark_even_when_enabled():
    f = laser(intents=None)
    uni = bytearray(512)
    uni[20:24] = bytes([100, 40, 200, 255])
    f.apply_safety(uni, laser_enabled=True)
    assert uni[20:24] == bytes(4)


def test_disarmed_zeroes_everything():
    f = laser()
    uni = bytearray(512)
    uni[20:24] = bytes([100, 40, 200, 255])
    f.apply_safety(uni, laser_enabled=False)
    assert uni[20:24] == bytes(4)


def test_armed_and_confirmed_passes_through():
    f = laser()
    uni = bytearray(512)
    uni[20:24] = bytes([100, 40, 200, 255])
    f.apply_safety(uni, laser_enabled=True)
    f.apply_laser_gate(uni, laser_on=True)
    assert uni[20:24] == bytes([100, 40, 200, 255])


def test_gate_off_writes_blank_over_the_whole_head():
    f = laser()
    uni = bytearray(512)
    uni[20:24] = bytes([100, 40, 200, 255])
    f.apply_laser_gate(uni, laser_on=False)
    assert uni[20:24] == bytes([5, 0, 0, 0])


def test_gate_off_with_chop_channel_only_blanks_that_channel():
    """Like the native gate (CH1 only), so a pattern can stay primed between moments."""
    f = laser(chop=0)
    uni = bytearray(512)
    uni[20:24] = bytes([100, 40, 200, 255])
    f.apply_laser_gate(uni, laser_on=False)
    assert uni[20:24] == bytes([5, 40, 200, 255])


def test_gate_off_on_unready_laser_is_zero():
    f = laser(confirmed=False)
    uni = bytearray(512)
    uni[20:24] = bytes([1, 2, 3, 4])
    f.apply_laser_gate(uni, laser_on=False)
    assert uni[20:24] == bytes(4)


def test_finalize_order_blackout_style_all_zero_wins():
    """Disarm after a lit intent: apply_safety runs last in finalize and nothing survives it."""
    f = laser()
    u = Uni()
    f.set_intent(u, "fan")
    uni = bytearray(u.data)
    f.apply_laser_gate(uni, laser_on=True)
    f.apply_safety(uni, laser_enabled=False)
    assert uni[20:24] == bytes(4)


def test_visual_state():
    f = laser()
    assert not f.visual_state(bytes(512)).active
    uni = bytearray(512)
    uni[20:24] = bytes([100, 40, 200, 255])
    assert f.visual_state(bytes(uni)).active
    uni[20:24] = bytes([5, 0, 0, 0])                 # blank is off
    assert not f.visual_state(bytes(uni)).active
