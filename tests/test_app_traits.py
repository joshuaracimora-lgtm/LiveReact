"""Roadmap item 8: fixture traits (fixtures/traits.py), the capability tags scenes will ask for
instead of isinstance checks (item 15 does that swap)."""
from __future__ import annotations

from pathlib import Path

import pytest

from livereact.fixtures import definition
from livereact.fixtures.definition import SCHEMA, load_definition
from livereact.fixtures.profiles import FIXTURE_TYPES, Fixture, Laser, MovingHead, Par, WashBar
from livereact.fixtures.traits import TRAITS, traits_of

BUILTIN = Path(definition.__file__).parent / "builtin"


def test_trait_vocabulary():
    assert set(TRAITS) == {"rgb_wash", "pan_tilt", "color_wheel", "strobe_channel", "dimmer", "laser"}


@pytest.mark.parametrize("cls,expected", [
    (Par, {"rgb_wash", "dimmer"}),
    (WashBar, {"rgb_wash", "dimmer", "strobe_channel"}),
    (MovingHead, {"pan_tilt", "color_wheel", "dimmer", "strobe_channel"}),
    (Laser, {"laser"}),
])
def test_native_classes(cls, expected):
    t = traits_of(cls(1, "x", "wash", "left"))
    assert isinstance(t, frozenset)
    assert t == expected


def test_native_subclass_keeps_its_traits():
    class MyBar(WashBar):
        pass
    assert "rgb_wash" in traits_of(MyBar(1, "x", "wash", "left"))


def test_unknown_fixture_has_no_traits():
    assert traits_of(Fixture(1, "x", "wash", "left")) == frozenset()


@pytest.mark.parametrize("type_name", sorted(FIXTURE_TYPES))
def test_native_traits_equal_builtin_definition_traits(type_name):
    native = FIXTURE_TYPES[type_name](1, "x", "wash", "left")
    assert traits_of(native) == traits_of(load_definition(BUILTIN / f"{type_name}.json"))


def _defn(category: str, functions: list[str]) -> dict:
    return {"schema": SCHEMA, "id": "t", "manufacturer": "m", "model": "m", "category": category,
            "source": {"kind": "user"},
            "modes": [{"name": "a", "channels": [{"offset": i, "function": f} for i, f in enumerate(functions)]},
                      {"name": "dimmer_only", "channels": [{"offset": 0, "function": "dimmer"}]}]}


def test_definition_traits_from_functions():
    d = load_definition(_defn("par", ["red", "green", "blue", "white"]))
    assert traits_of(d) == {"rgb_wash"}
    d = load_definition(_defn("wash_bar", ["dimmer", "shutter", "red", "green", "blue"]))
    assert traits_of(d) == {"rgb_wash", "dimmer", "strobe_channel"}
    d = load_definition(_defn("moving_head", ["pan", "tilt", "dimmer", "color_wheel"]))
    assert traits_of(d) == {"pan_tilt", "dimmer", "color_wheel"}


def test_partial_rgb_or_pan_only_is_not_the_trait():
    assert "rgb_wash" not in traits_of(load_definition(_defn("par", ["red", "green"])))
    assert "pan_tilt" not in traits_of(load_definition(_defn("moving_head", ["pan", "dimmer"])))


def test_laser_category_is_only_laser():
    """A laser is never driven as a wash or a dimmer, whatever its channels are called."""
    d = load_definition(_defn("laser", ["dimmer", "red", "green", "blue", "laser_pattern"]))
    assert traits_of(d) == {"laser"}
    d = load_definition(_defn("other", ["mode", "laser_pattern"]))
    assert traits_of(d) == {"laser"}


def test_mode_selects_channels():
    d = load_definition(_defn("par", ["red", "green", "blue"]))
    assert traits_of(d, "dimmer_only") == {"dimmer"}


def test_fixture_carrying_a_definition_maps_from_it():
    """A definition-driven fixture (item 10's GenericFixture) exposes .definition and .mode_name."""
    class Driven(Fixture):
        pass
    fx = Driven(1, "x", "wash", "left")
    fx.definition = load_definition(_defn("moving_head", ["pan", "tilt", "dimmer"]))
    fx.mode_name = "dimmer_only"
    assert traits_of(fx) == {"dimmer"}
    fx.mode_name = "a"
    assert traits_of(fx) == {"pan_tilt", "dimmer"}
