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
              "notes", "_todo"}
EDGE_KEYS = {"laser", "strobe"}
NO_DROP_WORDS = {"no-drop", "tool-track", "nodrop", "tool"}


@dataclass(frozen=True)
class EdgeFlags:
    laser: bool | None = None     # None = planner default (quiet edges)
    strobe: bool | None = None


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
    "mood": [],
    "notes": "",
}


def _palette_names() -> set[str]:
    try:
        from ..engines.palette import PALETTE_RGB
        return set(PALETTE_RGB)
    except Exception:  # noqa: BLE001 - never let a palette import break note loading
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


def from_dict(d: dict) -> LookNotes:
    validate(d)

    def edge(key: str) -> EdgeFlags:
        e = d.get(key) or {}
        return EdgeFlags(laser=e.get("laser"), strobe=e.get("strobe"))

    return LookNotes(track=str(d.get("track", "")), hook_scene=d.get("hook_scene"), hook_palette=d.get("hook_palette"),
                     big_moment=d.get("big_moment", "auto"), intro=edge("intro"), outro=edge("outro"),
                     strobe=d.get("strobe", "hard"), mood=tuple(d.get("mood", [])), notes=str(d.get("notes", "")))


def load_look_notes(path: str | Path) -> LookNotes | None:
    """None when the file is missing or still a ``_todo`` scaffold; ValueError when it is wrong."""
    p = Path(path)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, dict) and d.get("_todo"):
        return None
    return from_dict(d)


def look_notes_path_for(audio_or_stem: str | Path, tracks_dir: str | Path = "tracks") -> Path:
    """``tracks/<stem>.look.json`` (NFC/NFD tolerant like timeline_path_for)."""
    p = Path(audio_or_stem)
    stem = p.stem if p.suffix else p.name
    tdir = Path(tracks_dir) if not p.parent or str(p.parent) == "." else p.parent
    direct = tdir / f"{stem}.look.json"
    if direct.exists() or not tdir.is_dir():
        return direct
    want = unicodedata.normalize("NFC", direct.name)
    hits = [q for q in tdir.iterdir() if q.name.endswith(".look.json") and unicodedata.normalize("NFC", q.name) == want]
    return hits[0] if len(hits) == 1 else direct


def write_template(path: str | Path, track: str, todo: bool = True) -> Path:
    p = Path(path)
    d = dict(TEMPLATE, track=track)
    if todo:
        d["_todo"] = True
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p
