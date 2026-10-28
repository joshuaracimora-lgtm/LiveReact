"""A drop's class: what KIND of drop Josh marked (2026-10-26).

Josh: "we can have snare rolls a classification of a drop" -- a snare roll is a
short escalating drop (3-7 bars on his labels), a full drop is the groove/bass hit
and must hold at least 8 bars. Clap- and atmospheric drops are what his ear notes
describe as their own thing ("no snare roll (synths + claps)", "atmospheric drop:
... NO strobe").

An explicit ``"class"`` on the marker (sim/timeline.py ``Marker.cls``) always
wins. Otherwise the class is inferred from the ear note, in an order that matters:
a NEGATED roll ("no snare roll") must not read as a roll, "atmospheric ... a bit
of a snare roll" is atmospheric, and the scorer's own clap words (STRUCT-09,
plural / explicit on purpose) decide clap before the bare roll words do. With no
telling note, the length decides: under ``FULL_DROP_MIN_BARS`` is a roll.

This module is hashed into the evaluation's rules fingerprint (loop/evaluate.py):
changing how a drop is classed is changing what the scorer measures.
"""
from __future__ import annotations

from .timeline import DROP_CLASSES, Marker

FULL_DROP_MIN_BARS = 8.0        # Josh 2026-10-26: "at least 8 bars to be considered a drop"
NEGATED_ROLL_WORDS = ("no snare roll", "no roll", "not a snare roll", "not a roll")
ATMOSPHERIC_WORDS = ("atmospheric",)
ROLL_WORDS = ("snare roll", "roll", "escalat", "in-the-roll")


def drop_bars(m: Marker, bpm: float, beats_per_bar: int = 4) -> float | None:
    if m.end is None or bpm <= 0:
        return None
    return (m.end - m.t) * bpm / 60.0 / beats_per_bar


def infer_drop_class(m: Marker, bpm: float, beats_per_bar: int = 4) -> str:
    """The class of a drop marker (see the module docstring for the order)."""
    if m.cls in DROP_CLASSES:
        return m.cls
    from .score import CLAP_WORDS   # deferred: score imports the timeline module too
    note = m.note.lower()
    if any(w in note for w in NEGATED_ROLL_WORDS):
        return "clap"
    if any(w in note for w in ATMOSPHERIC_WORDS):
        return "atmospheric"
    if any(w in note for w in CLAP_WORDS):
        return "clap"
    if any(w in note for w in ROLL_WORDS):
        return "snare_roll"
    bars = drop_bars(m, bpm, beats_per_bar)
    if bars is not None and bars < FULL_DROP_MIN_BARS - 0.25:   # 7.75: a label a hair short of 8 is still 8
        return "snare_roll"
    return "full"


def is_inferred(m: Marker) -> bool:
    """True when the class came from the note/length, not from Josh."""
    return m.cls not in DROP_CLASSES
