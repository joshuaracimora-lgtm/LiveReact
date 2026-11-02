"""Per-track look notes: how Josh wants ONE track to look, in a few structured fields.

``tracks/<stem>.look.json`` (committed, like the timelines):

    {
      "schema": 1,
      "track": "SWEETER_KLICKAUD",
      "hook_scene": null,                 // a scene name from scenes/library.py, or null = planner's pick
      "hook_palette": null,               // a palette name from engines/palette.py (blood_red, cold_steel, ...)
      "big_moment": "auto",               // "auto" | "none" | {"drop": 2} | {"beat": 417} | {"t": 172.8}
      "intro":  {"laser": false, "strobe": false},
      "outro":  {"laser": false, "strobe": false},
      "strobe": "hard",                   // hard = count-in strobe in builds too | soft = only inside drops | none
      "laser_scene": null,                // a scenes/laser_scenes.json name the fan uses on this track, or null = Rainbow
      "laser_cues": [],                   // [{"on": "big_moment"|"drop"|"peak"|"wub"|"hit"|"cue", "scene": name}]
      "mood":   [],                       // free words; tools understand no-drop, tool-track, clap-driven
      "notes":  ""                        // anything else, for humans and the agent's report
    }

The planner honours a track's notes exactly (``plan_track(..., look=)``); the
nightly agent studies the notes Josh wrote for his labelled tracks to shape
the DEFAULTS for every other track. A file with ``"_todo": true`` is a scaffold
Josh has not filled in yet and is ignored. Unknown keys and bad values raise:
a typo must never pass silently as "no preference".
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = 1
STROBE_MODES = ("hard", "soft", "none")
BIG_MOMENT_WORDS = ("auto", "none")
KNOWN_KEYS = {"schema", "track", "hook_scene", "hook_palette", "big_moment", "intro", "outro", "strobe", "mood",
              "notes", "laser_scene", "laser_cues", "cues", "_todo"}
# Timed look cues (2026-10-29, Josh's Thriller ear notes: "drums cut right before the drop: straight to
# white out"): a cue names a look, WHEN (seconds into the file as rekordbox's grid counts them, or a beat)
# and how many beats it lasts. The planner turns it into beats, the show engine overrides the frame.
CUE_LOOKS = ("whiteout", "tease")   # tease: P-24, a Tease from the cue for its beats (rekordbox/planner.py)
CUE_DEFAULT_BEATS = 4
# what a laser cue can fire on: the fan engine's moments (engines/laser_trigger.py) plus the plan's big moment
LASER_CUE_TRIGGERS = ("big_moment", "drop", "peak", "wub", "hit", "cue")
EDGE_KEYS = {"laser", "strobe"}
NO_DROP_WORDS = {"no-drop", "tool-track", "nodrop", "tool"}


@dataclass(frozen=True)
class EdgeFlags:
    laser: bool | None = None     # None = planner default (quiet edges)
    strobe: bool | None = None


@dataclass(frozen=True)
class Cue:
    look: str                 # one of CUE_LOOKS
    t: float | None = None    # seconds into the file (the grid's clock), or
    beat: int | None = None   # a rekordbox beat number (1-based)
    beats: int = CUE_DEFAULT_BEATS


@dataclass(frozen=True)
class LookNotes:
    track: str = ""
    hook_scene: str | None = None
    hook_palette: str | None = None
    big_moment: object = "auto"           # "auto" | "none" | {"drop": k} | {"beat": n} | {"t": seconds}
    intro: EdgeFlags = field(default_factory=EdgeFlags)
    outro: EdgeFlags = field(default_factory=EdgeFlags)
    strobe: str = "hard"
    mood: tuple[str, ...] = ()
    notes: str = ""
    laser_scene: str | None = None        # the fan's look for every moment of this track (None = the default Rainbow)
    laser_cues: tuple[tuple[str, str], ...] = ()   # (trigger, scene): a look for one kind of moment
    cues: tuple[Cue, ...] = ()            # timed look overrides (whiteout at a time), see Cue

    @property
    def no_drop(self) -> bool:
        return any(w.lower() in NO_DROP_WORDS for w in self.mood)


TEMPLATE: dict = {
    "schema": SCHEMA,
    "track": "",
    "hook_scene": None,
    "hook_palette": None,
    "big_moment": "auto",
    "intro": {"laser": False, "strobe": False},
    "outro": {"laser": False, "strobe": False},
    "strobe": "hard",
    "laser_scene": None,
    "laser_cues": [],
    "cues": [],
    "mood": [],
    "notes": "",
}


def _palette_names() -> set[str]:
    try:
        from ..engines.palette import PALETTE_RGB
        return set(PALETTE_RGB)
    except Exception:  # noqa: BLE001 - never let a palette import break note loading
        return set()


def _laser_scene_names() -> set[str]:
    try:
        from ..engines.laser_scenes import scene_names
        return set(scene_names())
    except Exception:  # noqa: BLE001 - never let the scene library break note loading
        return set()


def validate(d: dict) -> None:
    if not isinstance(d, dict):
        raise ValueError("look notes must be a JSON object")
    unknown = set(d) - KNOWN_KEYS
    if unknown:
        raise ValueError(f"unknown look-notes keys: {sorted(unknown)} (allowed: {sorted(KNOWN_KEYS - {'_todo'})})")
    if d.get("schema", SCHEMA) != SCHEMA:
        raise ValueError(f"look notes schema {d.get('schema')} not supported (want {SCHEMA})")
    strobe = d.get("strobe", "hard")
    if strobe not in STROBE_MODES:
        raise ValueError(f"strobe must be one of {STROBE_MODES}, got {strobe!r}")
    bm = d.get("big_moment", "auto")
    if isinstance(bm, str):
        if bm not in BIG_MOMENT_WORDS:
            raise ValueError(f"big_moment must be {BIG_MOMENT_WORDS} or an object, got {bm!r}")
    elif isinstance(bm, dict):
        if len(bm) != 1 or next(iter(bm)) not in ("drop", "beat", "t"):
            raise ValueError('big_moment object must be exactly one of {"drop": k}, {"beat": n}, {"t": seconds}')
        v = next(iter(bm.values()))
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ValueError("big_moment value must be a positive number")
    else:
        raise ValueError("big_moment must be a string or an object")
    for edge in ("intro", "outro"):
        e = d.get(edge)
        if e is None:
            continue
        if not isinstance(e, dict) or set(e) - EDGE_KEYS:
            raise ValueError(f"{edge} must be an object with only {sorted(EDGE_KEYS)}")
        for k, v in e.items():
            if v is not None and not isinstance(v, bool):
                raise ValueError(f"{edge}.{k} must be true/false/null")
    pal = d.get("hook_palette")
    if pal is not None:
        names = _palette_names()
        if not isinstance(pal, str) or (names and pal not in names):
            raise ValueError(f"hook_palette {pal!r} is not a palette name ({sorted(names)})")
    if d.get("hook_scene") is not None and not isinstance(d["hook_scene"], str):
        raise ValueError("hook_scene must be a scene name or null")
    mood = d.get("mood", [])
    if not isinstance(mood, list) or not all(isinstance(w, str) for w in mood):
        raise ValueError("mood must be a list of words")
    if not isinstance(d.get("notes", ""), str):
        raise ValueError("notes must be a string")
    scenes = _laser_scene_names()
    ls = d.get("laser_scene")
    if ls is not None and (not isinstance(ls, str) or (scenes and ls not in scenes)):
        raise ValueError(f"laser_scene {ls!r} is not a laser scene name ({sorted(scenes)})")
    cues = d.get("laser_cues", [])
    if not isinstance(cues, list):
        raise ValueError("laser_cues must be a list of {\"on\": trigger, \"scene\": name}")
    seen_on: set[str] = set()
    for c in cues:
        if not isinstance(c, dict) or set(c) != {"on", "scene"}:
            raise ValueError('each laser cue must be exactly {"on": trigger, "scene": name}')
        if c["on"] not in LASER_CUE_TRIGGERS:
            raise ValueError(f"laser cue 'on' must be one of {LASER_CUE_TRIGGERS}, got {c['on']!r}")
        if not isinstance(c["scene"], str) or (scenes and c["scene"] not in scenes):
            raise ValueError(f"laser cue scene {c['scene']!r} is not a laser scene name ({sorted(scenes)})")
        if c["on"] in seen_on:
            raise ValueError(f"laser cue {c['on']!r} given twice")
        seen_on.add(c["on"])
    timed = d.get("cues", [])
    if not isinstance(timed, list):
        raise ValueError('cues must be a list of {"look": "whiteout", "t": seconds | "beat": n, "beats": n}')
    for c in timed:
        if not isinstance(c, dict) or "look" not in c or not (set(c) <= {"look", "t", "beat", "beats"}):
            raise ValueError('each cue is {"look": name, "t": seconds} or {"look": name, "beat": n}, optional "beats"')
        if c["look"] not in CUE_LOOKS:
            raise ValueError(f"cue look must be one of {CUE_LOOKS}, got {c['look']!r}")
        if ("t" in c) == ("beat" in c):
            raise ValueError("a cue needs exactly one of \"t\" (seconds) or \"beat\"")
        when = c.get("t", c.get("beat"))
        if not isinstance(when, (int, float)) or isinstance(when, bool) or when < 0:
            raise ValueError("cue t/beat must be a non-negative number")
        if "beats" in c and (not isinstance(c["beats"], int) or isinstance(c["beats"], bool) or c["beats"] <= 0):
            raise ValueError("cue beats must be a positive whole number")


def from_dict(d: dict) -> LookNotes:
    validate(d)

    def edge(key: str) -> EdgeFlags:
        e = d.get(key) or {}
        return EdgeFlags(laser=e.get("laser"), strobe=e.get("strobe"))

    return LookNotes(track=str(d.get("track", "")), hook_scene=d.get("hook_scene"), hook_palette=d.get("hook_palette"),
                     big_moment=d.get("big_moment", "auto"), intro=edge("intro"), outro=edge("outro"),
                     strobe=d.get("strobe", "hard"), mood=tuple(d.get("mood", [])), notes=str(d.get("notes", "")),
                     laser_scene=d.get("laser_scene"),
                     laser_cues=tuple((c["on"], c["scene"]) for c in d.get("laser_cues", [])),
                     cues=tuple(Cue(look=c["look"], t=float(c["t"]) if "t" in c else None,
                                    beat=int(c["beat"]) if "beat" in c else None,
                                    beats=int(c.get("beats", CUE_DEFAULT_BEATS))) for c in d.get("cues", [])))


def load_look_notes(path: str | Path) -> LookNotes | None:
    """None when the file is missing or still a ``_todo`` scaffold; ValueError when it is wrong."""
    p = Path(path)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, dict) and d.get("_todo"):
        return None
    return from_dict(d)


LOOK_PREFIX_MIN_CHARS = 20   # a shortened name must keep at least this much of the track name


def look_notes_path_for(audio_or_stem: str | Path, tracks_dir: str | Path = "tracks") -> Path:
    """``tracks/<stem>.look.json`` (NFC/NFD tolerant like timeline_path_for)."""
    p = Path(audio_or_stem)
    stem = p.stem if p.suffix else p.name
    tdir = Path(tracks_dir) if not p.parent or str(p.parent) == "." else p.parent
    direct = tdir / f"{stem}.look.json"
    if direct.exists() or not tdir.is_dir():
        return direct
    want = unicodedata.normalize("NFC", direct.name)
    notes = [q for q in tdir.iterdir() if q.name.endswith(".look.json")]
    hits = [q for q in notes if unicodedata.normalize("NFC", q.name) == want]
    if len(hits) == 1:
        return hits[0]
    # a truncated name (2026-10-30 run 6): the notes were named after a shortened file
    # (Thriller "..._Haunted_R.look.json") while the USB track is "..._Haunted_Remix_KLICKAUD", so the
    # live show never found them. One note whose stem starts the track's stem (or the reverse) counts.
    want_stem = unicodedata.normalize("NFC", stem)
    prefix = []
    for q in notes:
        s = unicodedata.normalize("NFC", q.name[: -len(".look.json")])
        if min(len(s), len(want_stem)) >= LOOK_PREFIX_MIN_CHARS and s != want_stem and (want_stem.startswith(s) or s.startswith(want_stem)):
            prefix.append(q)
    return prefix[0] if len(prefix) == 1 else direct


def write_template(path: str | Path, track: str, todo: bool = True) -> Path:
    p = Path(path)
    d = dict(TEMPLATE, track=track)
    if todo:
        d["_todo"] = True
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p
