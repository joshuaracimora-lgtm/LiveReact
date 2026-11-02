"""The laser scene library and the channel dictionary behind it (2026-10-29).

Josh: "The goal is for LiveReact to understand the depths of the laser channels,
so if I describe a scene or pattern, it's able to understand and know how to
create that pattern."  Two things make that possible:

  THE DICTIONARY  fixtures/builtin/shehds_3w_laser.json carries every DMX range of
                  the unit's 12-channel mode with a plain-English label and a
                  confidence (1.0 = watched on Josh's unit, 0.9 = printed in the
                  manufacturer's manual, docs/laser_findings.md "The manufacturer's
                  manual").  ``describe(values)`` reads a 12-byte look back in those
                  words; ``check(values)`` says what is wrong or unseen about it.

  THE LIBRARY     scenes/laser_scenes.json: named looks, each with its 12 bytes, a
                  description, whether CH12 follows the show palette, and whether its
                  bytes were seen or only composed from the manual.  ``compose()``
                  turns named choices (shape, motion, size, outline, colour) into bytes
                  through the dictionary, so a scene Josh describes becomes a library
                  entry without anyone guessing at numbers.

Who reads the library: engines/laser_show.py (LASER_SCENES, the Show tab's buttons),
engines/laser_manual.py (the cue buttons), engines/laser_fan.py (a track's look notes
pick a scene per trigger -- rekordbox/look_notes.py ``laser_scene`` / ``laser_cues``).
Safety is unchanged: every scene is written by Laser.set_scene behind apply_safety
(the arm switch) and apply_laser_gate; CH1 of a lit scene must sit in the manual band.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..fixtures.definition import FixtureDefinition, load_definition
from ..fixtures.profiles import Laser

SCHEMA = "livereact-laser-scenes/1"
LIBRARY_PATH = Path(__file__).resolve().parent.parent / "scenes" / "laser_scenes.json"
DEFINITION_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "builtin" / "shehds_3w_laser.json"
COLOR_MODES = ("palette", "fixed")
STATUSES = ("seen", "manual")
CHANNEL_NAMES = ("mode", "pattern group", "pattern", "rotation", "horizontal flip", "vertical flip",
                 "horizontal move", "vertical move", "size", "painting", "outline", "colour")

COLOR_BANDS: dict[str, int] = {
    "white": Laser.COLOR_WHITE, "red": Laser.COLOR_RED, "yellow": Laser.COLOR_YELLOW, "green": Laser.COLOR_GREEN,
    "cyan": Laser.COLOR_CYAN, "blue": Laser.COLOR_BLUE, "purple": Laser.COLOR_PURPLE,
}
LINE_GROUP_BYTE = Laser.PATTERN_A1_DOTTED_LINE   # CH2 118: the one shape value watched on the unit
DOTS_BYTE = Laser.SCAN_DOTS                      # CH11 190: the verified dot lattice


class LaserSceneError(ValueError):
    """A scene the library cannot accept (bad bytes, unknown name, bad file)."""


@dataclass(frozen=True)
class LaserScene:
    name: str
    values: tuple[int, ...]
    color: str = "palette"        # "palette": the fan engine puts the show palette's band in CH12
    status: str = "manual"        # "seen" on the unit, or "manual" (composed from the table)
    description: str = ""
    source: str = ""

    @property
    def follows_palette(self) -> bool:
        return self.color == "palette"


# ---------------------------------------------------------------- the dictionary
@lru_cache(maxsize=1)
def definition() -> FixtureDefinition:
    return load_definition(DEFINITION_PATH)


def _capability(offset: int, value: int):
    for cap in definition().mode().channels[offset].capabilities:
        lo, hi = cap.range
        if lo <= value <= hi:
            return cap
    return None


def describe(values: "list[int] | tuple[int, ...]") -> list[str]:
    """One line per channel: ``CH9 = 220: zoom in and out, slow to fast [manual]``."""
    out = []
    for i, v in enumerate(values[:12]):
        cap = _capability(i, int(v))
        if cap is None:
            out.append(f"CH{i + 1} = {int(v)}: {CHANNEL_NAMES[i]}: NOT IN ANY KNOWN RANGE")
            continue
        tag = "seen" if (cap.confidence or 0) >= 1.0 else "manual"
        out.append(f"CH{i + 1} = {int(v)}: {CHANNEL_NAMES[i]}: {cap.label} [{tag}]")
    return out


def check(values: "list[int] | tuple[int, ...]") -> tuple[list[str], list[str]]:
    """``(errors, notes)``: errors make a scene unusable; notes are what has not been seen yet."""
    errors: list[str] = []
    notes: list[str] = []
    if len(values) != 12:
        return [f"a scene needs 12 bytes, got {len(values)}"], notes
    for i, v in enumerate(values):
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 255:
            errors.append(f"CH{i + 1} = {v!r} is not a byte 0-255")
    if errors:
        return errors, notes
    if not 42 <= values[0] <= 83 and not values[0] >= 252:
        errors.append(f"CH1 = {values[0]} is not the manual band (42-83): a lit scene must be DMX-driven, never sound-active")
    for i, v in enumerate(values):
        cap = _capability(i, v)
        if cap is None:
            errors.append(f"CH{i + 1} = {v} is in no known range")
        elif (cap.confidence or 0) < 1.0:
            notes.append(f"CH{i + 1} = {v}: {cap.label} (from the manual, not yet seen)")
    if 64 <= values[8] <= 255:
        notes.append(f"CH9 = {values[8]}: the size animates on its own (speed band), it is not a fixed size")
    return errors, notes


# ---------------------------------------------------------------- composing
def _speed(s: float, lo: int, hi: int) -> int:
    s = min(1.0, max(0.0, float(s)))
    return lo + int(round(s * (hi - lo)))


def compose(*, shape: "str | tuple[int, int]" = "line", rotation=None, flip_x=None, flip_y=None,
            x=("sweep", 0.5), y=("pos", 71 / 127), size=("static", 0.0), painting=None,
            outline: "str | tuple[str, float]" = "dots", color: "str | tuple[str, float]" = "palette") -> tuple[int, ...]:
    """Named choices -> the 12 bytes, through the dictionary.  Every knob is a word (or a
    word + a 0..1 amount), never a raw number:

      shape     "line" (the one shape seen: group 4 at 118, pattern 1) or (group 1-8, pattern 1-16)
      rotation  None | ("angle", a) | ("cw", speed) | ("ccw", speed)          CH4
      flip_x    None | ("angle", a) | ("spin", speed)                           CH5 (X-axis tilt)
      flip_y    None | ("angle", a) | ("spin", speed)                           CH6 (Y-axis tilt)
      x         ("pos", 0..1) | ("sweep", speed)                                CH7
      y         ("pos", 0..1) | ("sweep", speed)   default: Rainbow's 71        CH8 (beam height!)
      size      ("static", 0 = full .. 1 = smallest) | ("shrink"|"grow"|"pulse", speed)   CH9
      painting  None | ("style1", speed) | ("style2", speed)                    CH10
      outline   "dots" | ("solid", scan_speed)                                  CH11
      color     "palette" (white here; the fan engine swaps the palette in) | a band name
                | ("auto"|"rainbow"|"paint", speed)                              CH12
    """
    def angle_or_speed(v, angle_lo, angle_hi, speed_word, speed_lo, speed_hi):
        if v is None:
            return 0
        word, amt = v
        if word == "angle":
            return _speed(amt, angle_lo, angle_hi)
        if word == speed_word:
            return _speed(amt, speed_lo, speed_hi)
        raise LaserSceneError(f"unknown choice {v!r}")

    if shape == "line":
        ch2, ch3 = LINE_GROUP_BYTE, 0
    else:
        g, p = shape
        if not (1 <= g <= 8 and 1 <= p <= 16):
            raise LaserSceneError("shape must be 'line' or (group 1-8, pattern 1-16)")
        ch2 = LINE_GROUP_BYTE if g == 4 else (g - 1) * 32 + 16
        ch3 = 0 if p == 1 else (p - 1) * 16 + 8
    if rotation is None:
        ch4 = 0
    else:
        word, amt = rotation
        ch4 = {"angle": (1, 127), "cw": (128, 191), "ccw": (192, 255)}.get(word)
        if ch4 is None:
            raise LaserSceneError(f"rotation must be ('angle'|'cw'|'ccw', amount), got {rotation!r}")
        ch4 = _speed(amt, *ch4)
    ch5 = angle_or_speed(flip_x, 0, 127, "spin", 128, 255)
    ch6 = angle_or_speed(flip_y, 0, 127, "spin", 128, 255)
    ch7 = angle_or_speed(x, 0, 127, "sweep", 128, 255) if x[0] != "pos" else _speed(x[1], 0, 127)
    ch8 = angle_or_speed(y, 0, 127, "sweep", 128, 255) if y[0] != "pos" else _speed(y[1], 0, 127)
    word, amt = size
    band = {"static": (0, 63), "shrink": (64, 127), "grow": (128, 191), "pulse": (192, 255)}.get(word)
    if band is None:
        raise LaserSceneError(f"size must be ('static'|'shrink'|'grow'|'pulse', amount), got {size!r}")
    ch9 = _speed(amt, *band)
    if painting is None:
        ch10 = 0
    else:
        word, amt = painting
        band = {"style1": (1, 127), "style2": (128, 255)}.get(word)
        if band is None:
            raise LaserSceneError(f"painting must be None or ('style1'|'style2', speed), got {painting!r}")
        ch10 = _speed(amt, *band)
    if outline == "dots":
        ch11 = DOTS_BYTE
    elif isinstance(outline, tuple) and outline[0] == "solid":
        ch11 = _speed(outline[1], 0, 127)
    else:
        raise LaserSceneError(f"outline must be 'dots' or ('solid', scan_speed), got {outline!r}")
    if color == "palette":
        ch12 = Laser.COLOR_WHITE
    elif isinstance(color, str):
        if color not in COLOR_BANDS:
            raise LaserSceneError(f"colour must be 'palette', one of {sorted(COLOR_BANDS)} or ('auto'|'rainbow'|'paint', speed)")
        ch12 = COLOR_BANDS[color]
    else:
        word, amt = color
        band = {"auto": (64, 127), "rainbow": (128, 191), "paint": (192, 255)}.get(word)
        if band is None:
            raise LaserSceneError(f"unknown colour mode {color!r}")
        ch12 = _speed(amt, *band)
    values = (Laser.MANUAL_PATTERN_MODE, ch2, ch3, ch4, ch5, ch6, ch7, ch8, ch9, ch10, ch11, ch12)
    errors, _ = check(values)
    if errors:
        raise LaserSceneError("; ".join(errors))
    return values


# ---------------------------------------------------------------- the library
def _parse(raw: dict, where: str) -> list[LaserScene]:
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise LaserSceneError(f"{where}: not a {SCHEMA} file")
    out: list[LaserScene] = []
    seen: set[str] = set()
    for i, s in enumerate(raw.get("scenes", [])):
        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            raise LaserSceneError(f"{where}: scene {i} has no name")
        if name in seen:
            raise LaserSceneError(f"{where}: scene {name!r} appears twice")
        seen.add(name)
        values = s.get("values")
        if not isinstance(values, list):
            raise LaserSceneError(f"{where}: {name!r}: values must be a list of 12 bytes")
        errors, _ = check(values)
        if errors:
            raise LaserSceneError(f"{where}: {name!r}: " + "; ".join(errors))
        color = s.get("color", "palette")
        status = s.get("status", "manual")
        if color not in COLOR_MODES or status not in STATUSES:
            raise LaserSceneError(f"{where}: {name!r}: color must be {COLOR_MODES}, status {STATUSES}")
        out.append(LaserScene(name=name, values=tuple(int(v) for v in values), color=color, status=status,
                              description=str(s.get("description", "")), source=str(s.get("source", ""))))
    if not out:
        raise LaserSceneError(f"{where}: no scenes")
    return out


def load_library(path: "str | Path" = LIBRARY_PATH) -> list[LaserScene]:
    p = Path(path)
    return _parse(json.loads(p.read_text(encoding="utf-8")), str(p))


@lru_cache(maxsize=1)
def library() -> tuple[LaserScene, ...]:
    return tuple(load_library())


def scene_names() -> list[str]:
    return [s.name for s in library()]


def get_scene(name: str) -> LaserScene:
    for s in library():
        if s.name == name:
            return s
    raise LaserSceneError(f"no laser scene named {name!r} (have: {', '.join(scene_names())})")


def scene_values() -> dict[str, tuple[int, ...]]:
    """name -> bytes, in file order (what engines/laser_show.py exposes as LASER_SCENES)."""
    return {s.name: s.values for s in library()}


def catalogue() -> str:
    """The library in words, for a human or for Claude writing a new scene."""
    lines = []
    for s in library():
        lines.append(f"{s.name} [{s.status}, colour {s.color}]: {s.description}")
        lines.extend("    " + d for d in describe(s.values))
    return "\n".join(lines)


if __name__ == "__main__":   # python -m livereact.engines.laser_scenes: the catalogue in words
    print(catalogue())
