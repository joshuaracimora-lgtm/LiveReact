"""Fixture traits: what a fixture can do, as tags scenes can ask for.

Today scenes pick fixtures with isinstance checks on the four native classes, which a
user-added fixture can never pass. A trait set describes both: native classes map by class,
definition-driven fixtures by their channel functions (fixtures/definition.py). Nothing on the
show path reads this yet (roadmap item 15 does the swap).
"""

from __future__ import annotations

from .definition import FixtureDefinition
from .profiles import Fixture, Laser, MovingHead, Par, WashBar

TRAITS = ("rgb_wash", "pan_tilt", "color_wheel", "strobe_channel", "dimmer", "laser")

# Same answer the native class's builtin definition gives (fixtures/builtin/).
_NATIVE: tuple[tuple[type[Fixture], frozenset[str]], ...] = (
    (Par, frozenset({"rgb_wash", "dimmer"})),
    (WashBar, frozenset({"rgb_wash", "dimmer", "strobe_channel"})),
    (MovingHead, frozenset({"pan_tilt", "color_wheel", "dimmer", "strobe_channel"})),
    (Laser, frozenset({"laser"})),
)


def _from_definition(d: FixtureDefinition, mode: str | None) -> frozenset[str]:
    functions = {c.function for c in d.mode(mode).channels}
    # A laser is only ever a laser: never driven as a wash or a dimmer.
    if d.category == "laser" or any(f.startswith("laser_") for f in functions):
        return frozenset({"laser"})
    t: set[str] = set()
    if {"red", "green", "blue"} <= functions:
        t.add("rgb_wash")
    if {"pan", "tilt"} <= functions:
        t.add("pan_tilt")
    if "color_wheel" in functions:
        t.add("color_wheel")
    if functions & {"strobe", "shutter"}:
        t.add("strobe_channel")
    if "dimmer" in functions:
        t.add("dimmer")
    return frozenset(t)


def traits_of(fx: Fixture | FixtureDefinition, mode: str | None = None) -> frozenset[str]:
    """The traits of a native fixture, a definition-driven one (``.definition`` +
    ``.mode_name``), or a bare definition in ``mode`` (default: its first)."""
    if isinstance(fx, FixtureDefinition):
        return _from_definition(fx, mode)
    d = getattr(fx, "definition", None)
    if isinstance(d, FixtureDefinition):
        return _from_definition(d, getattr(fx, "mode_name", None))
    for cls, traits in _NATIVE:
        if isinstance(fx, cls):
            return traits
    return frozenset()
