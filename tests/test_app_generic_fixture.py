"""Roadmap item 10: GenericFixture, a profiles.Fixture driven by a fixture definition."""
from __future__ import annotations

import pytest

from livereact.fixtures.definition import SCHEMA, load_definition
from livereact.fixtures.generic import GenericFixture
from livereact.fixtures.profiles import DIMMER_FLOOR_DMX, ChannelKind, Fixture, _intensity_to8


class Uni:
    def __init__(self) -> None:
        self.data = bytearray(512)

    def set(self, channel_1based: int, value: int) -> None:
        self.data[channel_1based - 1] = value


def _defn(category, channels, mode="m"):
    return load_definition({"schema": SCHEMA, "id": "t", "manufacturer": "m", "model": "m",
                            "category": category, "source": {"kind": "user"},
                            "modes": [{"name": mode, "channels": [
                                {"offset": i, **c} for i, c in enumerate(channels)]}]})


def par():
    """Dimmer + RGB + a shutter channel with an explicit open range and a strobe range."""
    return _defn("par", [
        {"function": "dimmer"}, {"function": "red"}, {"function": "green"}, {"function": "blue"},
        {"function": "shutter", "capabilities": [
            {"range": [0, 9], "type": "shutter", "label": "Closed"},
            {"range": [10, 19], "type": "shutter", "label": "Open"},
            {"range": [20, 250], "type": "strobe", "label": "strobe slow-fast"},
            {"range": [251, 255], "type": "shutter", "label": "open"}]},
    ])


def rgbw_bar():
    """No dimmer: RGBW + amber + UV, and a plain 0-255 strobe channel."""
    return _defn("wash_bar", [
        {"function": "red"}, {"function": "green"}, {"function": "blue"}, {"function": "white"},
        {"function": "amber"}, {"function": "uv"}, {"function": "strobe"},
    ])


def wheel_only():
    return _defn("other", [
        {"function": "dimmer", "default": 40},
        {"function": "color_wheel", "capabilities": [
            {"range": [0, 9], "type": "color_wheel", "color": "#ffffff", "label": "open"},
            {"range": [10, 19], "type": "color_wheel", "color": "#ff0000", "label": "red"},
            {"range": [20, 29], "type": "color_wheel", "color": "#00ff00", "label": "green"},
            {"range": [30, 39], "type": "color_wheel", "color": "#0000ff", "label": "blue"},
            {"range": [40, 255], "type": "color_wheel", "label": "rotation"}]},
    ])


def fx(defn, address=11):
    return GenericFixture(defn, None, address=address, label="L1", role="wash", side="left")


# --- identity / shape ---

def test_is_a_fixture_with_definition_shape():
    f = fx(par())
    assert isinstance(f, Fixture)
    assert f.channel_count == 5
    assert f.type_name == "t@m"
    assert f.mode_name == "m" and f.definition.id == "t"
    assert list(f.channels()) == [11, 12, 13, 14, 15]
    assert f.channel_kinds() == [ChannelKind.DIMMER, ChannelKind.COLOR, ChannelKind.COLOR,
                                 ChannelKind.COLOR, ChannelKind.STROBE]
    assert (f.label, f.role, f.side) == ("L1", "wash", "left")


def test_unknown_mode_raises():
    with pytest.raises(KeyError):
        GenericFixture(par(), "nope")


def test_invalid_definition_is_refused():
    bad = _defn("par", [{"function": "dimmer", "default": 999}])
    with pytest.raises(ValueError):
        GenericFixture(bad)


# --- boot ---

def test_boot_defaults_shutter_open_dimmer_zero():
    u = Uni()
    fx(par()).boot_defaults(u)
    assert u.data[10:15] == bytes([0, 0, 0, 0, 10])   # shutter at the bottom of "Open"


def test_boot_defaults_force_dimmer_zero_over_definition_default():
    u = Uni()
    fx(wheel_only()).boot_defaults(u)
    assert u.data[10] == 0


# --- intensity ---

def test_intensity_drives_the_dimmer_with_the_native_curve():
    u = Uni()
    f = fx(par())
    f.set_intensity(u, 0.5)
    assert u.data[10] == _intensity_to8(0.5)
    f.set_intensity(u, 0.01)
    assert u.data[10] == DIMMER_FLOOR_DMX
    f.set_intensity(u, 0.0)
    assert u.data[10] == 0


def test_no_dimmer_scales_rgb():
    u = Uni()
    f = fx(rgbw_bar())
    f.set_color(u, 1.0, 0.5, 0.0, 0.0)
    assert u.data[10:13] == bytes([255, 128, 0])     # level defaults to full
    f.set_intensity(u, 0.5)
    assert u.data[10:13] == bytes([128, 64, 0])
    f.set_color(u, 0.0, 0.0, 1.0)
    assert u.data[10:13] == bytes([0, 0, 128])       # the level persists across a colour change
    f.set_intensity(u, 0.0)
    assert u.data[10:14] == bytes(4)


# --- colour ---

def test_par_rgb_folds_white_in():
    u = Uni()
    fx(par()).set_color(u, 0.2, 0.0, 0.0, w=0.5)
    assert u.data[11:14] == bytes([round(0.7 * 255), round(0.5 * 255), round(0.5 * 255)])


def test_rgbw_writes_white_and_zeros_amber_uv():
    u = Uni()
    u.data[14] = u.data[15] = 99
    fx(rgbw_bar()).set_color(u, 0.0, 1.0, 0.0, w=0.4)
    assert u.data[10:16] == bytes([0, 255, 0, round(0.4 * 255), 0, 0])


@pytest.mark.parametrize("rgb,slot", [((1, 0, 0), 14), ((0, 0.9, 0.1), 24), ((0, 0, 1), 34), ((1, 1, 1), 4)])
def test_wheel_only_picks_nearest_slot_middle(rgb, slot):
    u = Uni()
    fx(wheel_only()).set_color(u, *rgb)
    assert u.data[11] == slot


# --- strobe ---

def test_strobe_maps_into_the_strobe_range_and_zero_opens():
    u = Uni()
    f = fx(par())
    f.set_strobe(u, 1.0)
    assert u.data[14] == 250
    f.set_strobe(u, 0.5)
    assert u.data[14] == 135
    f.set_strobe(u, 0.0)
    assert u.data[14] == 10


def test_plain_strobe_channel_is_linear():
    u = Uni()
    f = fx(rgbw_bar())
    f.set_strobe(u, 0.5)
    assert u.data[16] == 128
    f.set_strobe(u, 0.0)
    assert u.data[16] == 0


def test_no_strobe_channel_is_a_noop():
    u = Uni()
    fx(wheel_only()).set_strobe(u, 1.0)
    assert u.data == bytearray(512)


def test_cap_strobe_only_in_the_strobe_range():
    f = fx(par())
    uni = bytearray(512)
    uni[14] = 250
    f.cap_strobe(uni, 0.5)
    assert uni[14] == 135
    uni[14] = 10                     # shutter open, not strobing: left alone
    f.cap_strobe(uni, 0.0)
    assert uni[14] == 10


# --- finalize clamps ---

def test_master_brightness_scales_dimmer_or_rgb():
    uni = bytearray(512)
    uni[10] = 200
    fx(par()).apply_master_brightness(uni, 0.5)
    assert uni[10] == 100
    uni = bytearray(512)
    uni[10:14] = bytes([200, 100, 50, 20])
    fx(rgbw_bar()).apply_master_brightness(uni, 0.5)
    assert uni[10:14] == bytes([100, 50, 25, 10])


def test_color_bias_warm_boosts_red_cuts_blue():
    uni = bytearray(512)
    uni[11:14] = bytes([100, 100, 100])
    fx(par()).apply_color_bias(uni, -1.0)
    assert uni[11:14] == bytes([150, 100, 50])


# --- visual state ---

def test_visual_state_par():
    f = fx(par())
    uni = bytearray(512)
    uni[10:15] = bytes([255, 10, 20, 30, 10])
    vs = f.visual_state(bytes(uni))
    assert (vs.r, vs.g, vs.b) == (10, 20, 30)
    assert vs.brightness == 1.0 and vs.active and vs.label == "L1"
    assert vs.strobe == 0.0


def test_visual_state_no_dimmer_and_wheel():
    uni = bytearray(512)
    uni[10:14] = bytes([0, 0, 100, 50])
    vs = fx(rgbw_bar()).visual_state(bytes(uni))
    assert (vs.r, vs.g, vs.b) == (50, 50, 150) and vs.active and vs.brightness == 1.0
    uni = bytearray(512)
    uni[10:12] = bytes([128, 15])
    vs = fx(wheel_only()).visual_state(bytes(uni))
    assert (vs.r, vs.g, vs.b) == (255, 0, 0)
    assert vs.brightness == pytest.approx(128 / 255) and vs.active
    assert not fx(rgbw_bar()).visual_state(bytes(512)).active
