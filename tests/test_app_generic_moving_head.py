"""Roadmap item 11: GenericMovingHead (pan/tilt, slew limit, home position) from a definition."""
from __future__ import annotations

import pytest

from livereact.fixtures.definition import SCHEMA, load_definition
from livereact.fixtures.generic import (
    DEFAULT_PAN_DEG, DEFAULT_PAN_MAX_DEG_S, DEFAULT_TILT_DEG, GenericFixture, GenericMovingHead, make_fixture,
)
from livereact.fixtures.profiles import _to16_int


class Uni:
    def __init__(self) -> None:
        self.data = bytearray(512)

    def set(self, channel_1based: int, value: int) -> None:
        self.data[channel_1based - 1] = value


def _defn(channels, physical=None, category="moving_head"):
    raw = {"schema": SCHEMA, "id": "h", "manufacturer": "m", "model": "m", "category": category,
           "source": {"kind": "user"},
           "modes": [{"name": "m", "channels": [{"offset": i, **c} for i, c in enumerate(channels)]}]}
    if physical is not None:
        raw["physical"] = physical
    return load_definition(raw)


HEAD16 = [{"function": "pan", "default": 128}, {"function": "pan_fine", "fine_of": 0},
          {"function": "tilt", "default": 100}, {"function": "tilt_fine", "fine_of": 2},
          {"function": "dimmer"}, {"function": "red"}, {"function": "green"}, {"function": "blue"}]
HEAD8 = [{"function": "dimmer"}, {"function": "pan"}, {"function": "tilt"}]


def head16(physical=None):
    return GenericMovingHead(_defn(HEAD16, physical or {"pan_deg": 540, "tilt_deg": 270, "pan_max_deg_s": 270}),
                             address=1, label="H")


def test_make_fixture_picks_the_class():
    assert type(make_fixture(_defn(HEAD16))) is GenericMovingHead
    assert type(make_fixture(_defn([{"function": "dimmer"}], category="dimmer"))) is GenericFixture
    # pan without tilt is not a moving head
    assert type(make_fixture(_defn([{"function": "pan"}], category="other"))) is GenericFixture


def test_requires_pan_and_tilt():
    with pytest.raises(ValueError):
        GenericMovingHead(_defn([{"function": "dimmer"}]))


def test_16bit_position():
    u = Uni()
    f = head16()
    f.set_position(u, 0.3, 0.7)
    p, t = _to16_int(0.3), _to16_int(0.7)
    assert u.data[0:4] == bytes([p >> 8, p & 0xFF, t >> 8, t & 0xFF])


def test_8bit_position():
    u = Uni()
    GenericMovingHead(_defn(HEAD8)).set_position(u, 0.5, 1.0)
    assert u.data[1:3] == bytes([128, 255])


def test_set_aim_marks_absolute_movement():
    class MarkUni(Uni):
        def __init__(self):
            super().__init__()
            self.absolute_movement = set()
    u = MarkUni()
    f = head16()
    f.set_aim(u, 0.5, 0.5)
    assert u.absolute_movement == {0, 1, 2, 3}
    u8 = MarkUni()
    GenericMovingHead(_defn(HEAD8), address=10).set_aim(u8, 0.5, 0.5)
    assert u8.absolute_movement == {10, 11}


def test_boot_uses_definition_defaults_and_dimmer_off():
    u = Uni()
    head16().boot_defaults(u)
    assert u.data[0:5] == bytes([128, 0, 100, 0, 0])


def test_slew_limit_from_physical_speed():
    f = head16({"pan_deg": 540, "tilt_deg": 270, "pan_max_deg_s": 270})
    uni = bytearray(512)
    f.apply_slew_limit(uni, 0.02)           # first frame passes through (from 0)
    f.set_position(_Wrap(uni), 1.0, 1.0)
    f.apply_slew_limit(uni, 0.1)
    # pan: 270 deg/s * 0.1 s = 27 deg of 540 -> 5% of travel
    pan = ((uni[0] << 8) | uni[1]) / 65535
    tilt = ((uni[2] << 8) | uni[3]) / 65535
    assert pan == pytest.approx(0.05, abs=1e-3)
    # tilt uses the same deg/s over its own 270 deg range -> 10%
    assert tilt == pytest.approx(0.10, abs=1e-3)


def test_slew_limit_default_when_speed_unknown():
    f = GenericMovingHead(_defn(HEAD16))       # no physical block at all
    assert f.pan_range_deg == DEFAULT_PAN_DEG and f.tilt_range_deg == DEFAULT_TILT_DEG
    assert f.pan_max_deg_s == DEFAULT_PAN_MAX_DEG_S
    assert DEFAULT_PAN_MAX_DEG_S <= 260      # never faster than the native SHEHDS placeholder
    uni = bytearray(512)
    f.apply_slew_limit(uni, 0.02)
    f.set_position(_Wrap(uni), 1.0, 0.0)
    f.apply_slew_limit(uni, 0.1)
    pan = ((uni[0] << 8) | uni[1]) / 65535
    assert pan == pytest.approx(DEFAULT_PAN_MAX_DEG_S * 0.1 / DEFAULT_PAN_DEG, abs=1e-3)


def test_slew_limit_8bit():
    f = GenericMovingHead(_defn(HEAD8, {"pan_deg": 540, "tilt_deg": 270, "pan_max_deg_s": 540}))
    uni = bytearray(512)
    f.apply_slew_limit(uni, 0.02)
    uni[1] = 255
    f.apply_slew_limit(uni, 0.1)             # 540 deg/s * 0.1 s = 10% of 540 deg
    assert uni[1] == round(0.1 * 65535) >> 8    # the coarse byte of 10% in 16-bit counts


def test_home_position_remap_and_absolute_passthrough():
    f = head16()
    uni = bytearray(512)
    f.set_position(_Wrap(uni), 0.5, 0.5)
    f.apply_home_position(uni)               # uncalibrated: no-op
    assert ((uni[0] << 8) | uni[1]) == _to16_int(0.5)
    f.home_pan, f.home_tilt = 0.3, 0.6
    f.apply_home_position(uni, bound=0.2)
    # within one 16-bit count: raw 0.5 is 32768/65535, a hair over centre (same as MovingHead)
    assert abs(((uni[0] << 8) | uni[1]) - _to16_int(0.3)) <= 1
    assert abs(((uni[2] << 8) | uni[3]) - _to16_int(0.6)) <= 1
    f.set_position(_Wrap(uni), 1.0, 0.5)
    f.apply_home_position(uni, bound=0.2)
    assert abs(((uni[0] << 8) | uni[1]) - _to16_int(0.4)) <= 1
    f.set_position(_Wrap(uni), 1.0, 0.5)
    f.apply_home_position(uni, absolute=True)
    assert ((uni[0] << 8) | uni[1]) == _to16_int(1.0)


def test_visual_state_reports_pan_tilt():
    f = head16()
    uni = bytearray(512)
    f.set_position(_Wrap(uni), 0.25, 0.75)
    uni[4] = 255
    uni[5] = 200
    vs = f.visual_state(bytes(uni))
    assert vs.pan == pytest.approx(0.25, abs=1e-4) and vs.tilt == pytest.approx(0.75, abs=1e-4)
    assert vs.active and vs.r == 200


class _Wrap:
    """Write a bytearray through the Buffer protocol."""

    def __init__(self, uni: bytearray) -> None:
        self.uni = uni

    def set(self, ch: int, v: int) -> None:
        self.uni[ch - 1] = v
