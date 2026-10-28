"""One look set per song, picked from how it sounds (Josh, 2026-10-28).

"If two songs play back to back and the strobe hits the same and same with the lasers (but just
different color), that is not what we want. We want each song (because each song is unique) to
trigger unique lighting scenes." Until now every planned track got the same looks (CORE_SCENES: one
per bucket, the 2026-09-15 cut after "a lot of different scenes, even when the buildups were pretty
similar"), the same post-drop movement order, the same phrase-tail strobe and the same beat-4 wash
accent; only the colour changed.

Both asks hold at once: a SongStyle is fixed for the WHOLE song (the Bible's TIME-20, repeat the look
when a section repeats; the 2026-09-15 consistency), and it differs from song to song:

    strobe      the DROP/PEAK strobe rhythm (engines/reactive.py STROBE_STYLES), or none at all
                (TIME-21 restraint: some songs get no strobe, so it lands harder where it is)
    accent      which beat of the bar the held drop wash flashes on (reactive.py's beat-4 accent)
    spots       the heads in the drop take the song colour, or its companion (palette-v2's second
                colour, so still two colours per song, D-27)
    move_start  which post-drop movement shape opens the drop (scenes/movement.py DROP_SEQUENCE)
    move_speed  full or half speed for those shapes
    build       the build look (palette looks only: the lasers follow the palette, D-23)
    brk         the break look

Each dimension ranks its options by one feature of the track's rekordbox colour waveform (below);
the style is the best-ranked combination that differs from the PREVIOUS song in at least
MIN_DIFFERENCES dimensions, colour not counted. The drop and peak looks themselves stay Drop Strobe
Stab / Peak Full Rig: the library's other drop looks hard-code their colour (blood red, sunset,
warm white) and park the heads, which would break the laser/palette match and bring back the
static drop Josh called stagnant. The laser fan keeps its one pattern until Saturday (2026-10-31).

First-guess targets, not tuned by eye. FEATURE_SPANS are the 5th..95th percentiles over the USB
library (tools/agent/song_styles.py prints them).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, fields

STROBE_STYLES = ("none", "sparse", "stab", "tail", "downbeat")   # quiet -> loud drops
ACCENTS = ("4", "24")    # darker -> brighter: beat 4, or the backbeat (the claps; never the kick, see reactive.py)
SPOT_MODES = ("match", "split")                               # narrow -> wide band spread
MOVE_SHAPES = (0, 2, 4, 3, 1)                                 # fan, cross, crowd_sweep, kick_lift, beat_chase: smooth -> punchy
MOVE_SPEEDS = (0.5, 1.0)                                      # still -> busy breaks (a groove that never stops)
BUILD_LOOKS = ("Build Riser Wash", "Rising Tide", "Chase Wash L/R")   # gentle -> steep rise
BREAK_LOOKS = ("Deep Wash Breathe", "Break Low Wash")         # quiet -> busier breaks

MIN_DIFFERENCES = 3

# (low, high) of each feature over the USB library; a value is placed on 0..1 between them
FEATURE_SPANS: dict[str, tuple[float, float]] = {   # USB library, 213 tracks, 2026-10-28
    "punch": (0.22, 0.51),
    "brightness": (-0.12, 0.22),
    "spread": (0.22, 0.355),
    "drop_energy": (0.28, 0.675),
    "rise": (0.0, 0.38),
    "break_energy": (0.03, 0.34),
}


@dataclass(frozen=True)
class SongFeatures:
    punch: float = 0.0          # drops: 90th-percentile height minus mean height (hits vs a wall of sound)
    brightness: float = 0.0     # whole track: high share minus low share (D-23's profile axis)
    spread: float = 0.0         # whole track: mid share (how much sits between the bass and the hats)
    drop_energy: float = 0.0    # drops: mean height
    rise: float = 0.0           # drop energy minus the build before it, averaged
    break_energy: float = 0.0   # breaks / verses: mean height

    def unit(self, name: str) -> float:
        lo, hi = FEATURE_SPANS[name]
        return max(0.0, min(1.0, (getattr(self, name) - lo) / (hi - lo))) if hi > lo else 0.5


@dataclass(frozen=True)
class SongStyle:
    strobe: str = "tail"
    accent: str = "4"
    spots: str = "match"
    move_start: int = 0
    move_speed: float = 1.0
    build: str = "Rising Tide"
    brk: str = "Deep Wash Breathe"

    def differences(self, other: "SongStyle | None") -> int:
        if other is None:
            return len(fields(self))
        return sum(1 for f in fields(self) if getattr(self, f.name) != getattr(other, f.name))

    def summary(self) -> str:
        return (f"strobe={self.strobe} accent={self.accent} spots={self.spots} move={self.move_start}"
                f"x{self.move_speed:g} build={self.build!r} break={self.brk!r}")


# the style every track had before 2026-10-28 (and still gets without a waveform)
DEFAULT_STYLE = SongStyle()


def _ranked(options: tuple, at: float) -> dict:
    """{option: penalty}: options spread evenly over 0..1, penalty = distance from ``at``."""
    n = len(options)
    return {o: abs(at - (i / (n - 1) if n > 1 else 0.5)) for i, o in enumerate(options)}


def choose(features: SongFeatures | None, previous: SongStyle | None = None) -> SongStyle:
    """The best-fitting style for ``features`` that differs from ``previous`` in at least
    MIN_DIFFERENCES dimensions (deterministic: same inputs, same style)."""
    if features is None and (previous is None or DEFAULT_STYLE.differences(previous) >= MIN_DIFFERENCES):
        return DEFAULT_STYLE
    f = features or SongFeatures()
    punch = f.unit("punch")
    dims = {
        "strobe": _ranked(STROBE_STYLES, f.unit("drop_energy")),
        "accent": _ranked(ACCENTS, f.unit("brightness")),
        "spots": _ranked(SPOT_MODES, f.unit("spread")),
        "move_start": _ranked(MOVE_SHAPES, punch),
        "move_speed": _ranked(MOVE_SPEEDS, f.unit("break_energy")),
        "build": _ranked(BUILD_LOOKS, f.unit("rise")),
        "brk": _ranked(BREAK_LOOKS, f.unit("break_energy")),
    }
    names = list(dims)
    best: tuple[float, int] | None = None
    chosen = DEFAULT_STYLE
    for order, combo in enumerate(itertools.product(*(list(dims[n]) for n in names))):
        style = SongStyle(**dict(zip(names, combo)))
        if previous is not None and style.differences(previous) < MIN_DIFFERENCES:
            continue
        key = (round(sum(dims[n][v] for n, v in zip(names, combo)), 9), order)   # ties: option order
        if best is None or key < best:
            best, chosen = key, style
    return chosen


def song_features(sections, *, seconds, waveform) -> SongFeatures | None:
    """Features from the planned sections (planner.PlannedSection) and the colour waveform; None
    without a waveform or a grid."""
    if waveform is None or not len(waveform):
        return None

    def span(s):
        t0, t1 = seconds(s.beat), seconds(s.end_beat)
        return (t0, t1) if t0 is not None and t1 is not None and t1 > t0 else None

    def mean(xs):
        xs = list(xs)
        return sum(xs) / len(xs) if xs else 0.0

    drops = [(s, span(s)) for s in sections if s.kind == "drop" and not s.roll]
    drops = [(s, w) for s, w in drops if w is not None]
    if not drops:
        return None
    punch = mean(waveform.peak(*w) - waveform.energy(*w) for _s, w in drops)
    drop_energy = mean(waveform.energy(*w) for _s, w in drops)
    low, mid, high = waveform.band_shares(0.0, waveform.duration_s)
    rises = []
    for i, s in enumerate(sections):
        if s.kind == "build" and i + 1 < len(sections) and sections[i + 1].kind == "drop":
            wb, wd = span(s), span(sections[i + 1])
            if wb is not None and wd is not None:
                rises.append(waveform.energy(*wd) - waveform.energy(*wb))
    quiet = [w for w in (span(s) for s in sections if s.bucket == "break") if w is not None]
    return SongFeatures(punch=round(punch, 4), brightness=round(high - low, 4), spread=round(mid, 4),
                        drop_energy=round(drop_energy, 4), rise=round(mean(rises), 4),
                        break_energy=round(mean(waveform.energy(*w) for w in quiet), 4))
