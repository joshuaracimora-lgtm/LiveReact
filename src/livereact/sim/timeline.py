"""Per-track beat timeline: what the real Pro DJ Link feed would have said.

A ``Timeline`` is cached next to the audio as ``<track>.timeline.json`` so every
simulation of that track sees the identical beat grid (the agent's before/after
comparisons depend on that). ``first_downbeat_s`` is the one number the fixed
``--bpm`` replay in tools/simulations/replay_song.py never had: without it bar 1
lands wherever the file happens to start and nothing phrase-based can be
scored. ``markers`` are optional human/agent annotations (drops, breakdowns)
used by the scorer, never by the engine.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# The marker vocabulary (Marker's docstring). Loading is lenient -- an unknown kind,
# class or key is logged, never raised: one bad file must not take down a night's
# evaluation. tests/test_timeline_vocab.py holds every tracks/ file to it instead.
MARKER_KINDS = ("drop", "fill", "breakdown", "build", "groove", "whiteout", "outro")
# Drop classes (Josh 2026-10-26: "snare rolls a classification of a drop"). Only drops
# carry one; empty = inferred by sim/drop_class.py.
DROP_CLASSES = ("snare_roll", "full", "clap", "atmospheric")
_MARKER_KEYS = ("t", "kind", "note", "end", "class")


@dataclass
class Marker:
    """A structural moment, by ear. ``kind``:
    drop       -- the real thing: full-energy section; ``end`` is where it cuts
    fill       -- an energy entry that is NOT a drop (Namerakana-style tool tracks,
                  One More Time's first sections); no strobe/drop looks warranted
    breakdown  -- energy strips out; runs until the next marker
    build      -- riser / snare-roll section leading into a drop
    groove     -- the main full-energy section after a drop's roll resolves: synth-synced
                  wash bar, heads at full speed, laser bursts 2-4 beats every other bar
                  (STRUCT-07); ``end`` is where it cuts
    whiteout   -- drums cut while the track stays intense (stabs/synths carry it):
                  the rig goes white, moving heads to the DJ (STRUCT-04)
    outro      -- the track winding down (a kind of break, D-22)
    ``note`` starting with "est:" means unverified machine guess.
    ``cls`` (JSON ``"class"``) is a drop's class from DROP_CLASSES, set by Josh;
    empty means "infer it" (sim/drop_class.py)."""
    t: float
    kind: str
    note: str = ""
    end: float | None = None
    cls: str = ""

    def to_dict(self) -> dict:
        d = {"t": self.t, "kind": self.kind, "note": self.note, "end": self.end}
        if self.cls:
            d["class"] = self.cls
        return d

    @classmethod
    def from_dict(cls, m: dict, where: str = "") -> "Marker":
        extra = sorted(set(m) - set(_MARKER_KEYS))
        if extra:
            log.warning("timeline%s: marker at %s has unknown keys %s (ignored)", where, m.get("t"), extra)
        mk = cls(t=m["t"], kind=m["kind"], note=m.get("note", ""), end=m.get("end"), cls=m.get("class", "") or "")
        if mk.kind not in MARKER_KINDS:
            log.warning("timeline%s: marker at %s has unknown kind %r", where, mk.t, mk.kind)
        if mk.cls and (mk.kind != "drop" or mk.cls not in DROP_CLASSES):
            log.warning("timeline%s: marker at %s has class %r (classes are for drops: %s)",
                        where, mk.t, mk.cls, ", ".join(DROP_CLASSES))
        return mk


@dataclass
class Timeline:
    bpm: float
    first_downbeat_s: float = 0.0
    beats_per_bar: int = 4
    markers: list[Marker] = field(default_factory=list)
    source: str = "manual"   # "manual" | "analyze_track" | ...
    notes: str = ""          # track-level design notes from Josh (palette, feel, what the lights should follow)

    @property
    def beat_dur(self) -> float:
        return 60.0 / self.bpm

    @property
    def bar_dur(self) -> float:
        return self.beat_dur * self.beats_per_bar

    def grid_start(self) -> tuple[float, int]:
        """(time of the first beat at or after t=0, its beat_in_bar).

        The grid extends backwards from ``first_downbeat_s`` so the beat feed is
        continuous from the top of the file, and ``beat_in_bar`` is phased so a
        downbeat (beat 1) lands exactly on ``first_downbeat_s``.
        """
        bd = self.beat_dur
        n = int(self.first_downbeat_s // bd)   # whole beats before the downbeat
        t0 = self.first_downbeat_s - n * bd
        beat_in_bar = ((-n) % self.beats_per_bar) + 1
        return t0, beat_in_bar

    def to_json(self) -> str:
        d = asdict(self)
        d["markers"] = [m.to_dict() for m in self.markers]
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, text: str, where: str = "") -> "Timeline":
        d = json.loads(text)
        d["markers"] = [Marker.from_dict(m, where) for m in d.get("markers", [])]
        return cls(**d)

    @classmethod
    def load(cls, path: str | Path) -> "Timeline":
        return cls.from_json(Path(path).read_text(encoding="utf-8"), where=f" {Path(path).name}")

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


def match_timeline_name(expected: str, candidates: "list[str]") -> "str | None":
    """Find ``expected`` among ``candidates`` ignoring Unicode normalization form.

    The committed timelines are NFC (git stores paths in NFC on this project);
    audio copied from the Mac arrives NFD (``é`` as ``e`` + U+0301). macOS treats
    the two spellings as one file name, Windows as two, so a plain
    ``with_name()`` lookup silently loses the accented tracks there (found on the
    desktop 2026-10-23: 8 of 10 labeled tracks). Exact match wins; else the unique
    candidate with the same NFC form.
    """
    if expected in candidates:
        return expected
    want = unicodedata.normalize("NFC", expected)
    hits = [c for c in candidates if unicodedata.normalize("NFC", c) == want]
    return hits[0] if len(hits) == 1 else None


def timeline_path_for(audio_path: str | Path) -> Path:
    """``tracks/song.mp3`` -> ``tracks/song.timeline.json``.

    If that exact path does not exist but a sibling differs only in Unicode
    normalization form, return the sibling (see ``match_timeline_name``).
    Otherwise return the plain derived path, so callers that *create* timelines
    (analyze_track.py) keep working.
    """
    p = Path(audio_path)
    direct = p.with_name(p.stem + ".timeline.json")
    if direct.exists() or not p.parent.is_dir():
        return direct
    names = [q.name for q in p.parent.iterdir() if q.name.endswith(".timeline.json")]
    hit = match_timeline_name(direct.name, names)
    return p.with_name(hit) if hit else direct
