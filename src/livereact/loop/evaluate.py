"""Evaluate one parameter set: run + score every labeled track, in memory.

Returns a compact ``Evaluation`` the gate and the search can compare. Tracks
are split into a tuning set and a hold-out set deterministically (by a hash
of the file name), so the split is stable as tracks are added and no track
silently moves between sets. Only tracks whose markers are ear-verified (no
``est:`` note, no 'estimated' source) are SCORED at all.

2026-10-25, the rekordbox track planner: with a USB export copy
(``rekordbox/usb_export.py``) each scored track also gets its phrase table, beat
grid, colour waveform, rekordbox id and look notes, and ``drive=True`` lets the
plan run the sections (``mode="drive"``). A second, marker-less set -- a
deterministic sample of the stick's other tracks (``sweep_sample``) -- is scored
on the marker-free rules only, so a change is judged on how it generalises, not
just on the ten tracks Josh timed. Marker rules are never scored against
unverified markers: a USB track's timeline carries none.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ..sim.harness import decode_audio, default_seed_range, run_sim
from ..sim.score import RULES, Run, SEVERITY_WEIGHT, score_run
from ..sim.timeline import Timeline, timeline_path_for
from .metrics import drop_offsets, offsets_summary

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg"}
HOLDOUT_FRACTION = 0.25
SWEEP_SAMPLE_N = 20


def is_ear_verified(tl) -> bool:
    """True when every marker is Josh's (no 'est:' machine guess, no estimated source)."""
    return bool(tl.markers) and not any(m.note.startswith("est:") for m in tl.markers) and "estimated" not in tl.source


def _audio_sibling(timeline_path: Path) -> Path | None:
    """The audio beside a timeline, matched in NFC: committed timelines are NFC, Mac audio names NFD
    (the 2026-10-23 desktop pairing bug) -- a plain stem comparison silently drops the accented tracks."""
    stem = unicodedata.normalize("NFC", timeline_path.name[: -len(".timeline.json")])
    for q in sorted(timeline_path.parent.iterdir()):
        if unicodedata.normalize("NFC", q.stem) == stem and q.suffix.lower() in AUDIO_EXT:
            return q
    return None


def labeled_tracks(tracks_dir: str | Path = "tracks", usb=None) -> list[Path]:
    """Audio paths of the SCORED set: every ear-verified timeline in ``tracks_dir`` whose audio is
    beside it, or -- when ``usb`` (a UsbExport) is given and the audio is not in ``tracks_dir`` --
    the same track's file on the USB export copy."""
    tdir = Path(tracks_dir)
    if not tdir.is_dir():
        return []
    out: list[Path] = []
    for tp in sorted(tdir.iterdir()):
        if not tp.name.endswith(".timeline.json"):
            continue
        if not is_ear_verified(Timeline.load(tp)):
            continue
        audio = _audio_sibling(tp)
        if audio is None and usb is not None:
            stem = tp.name[: -len(".timeline.json")]
            ut = usb.find(Path(stem + ".mp3"))
            if ut is not None and ut.audio is not None:
                audio = ut.audio
        if audio is not None:
            out.append(audio)
    return out


def ear_timeline_for(p: Path, usb=None, tracks_dir: str | Path = "tracks") -> Timeline:
    """The ear timeline of a scored track: beside the audio, else matched through the USB export."""
    tp = timeline_path_for(p)
    if tp.exists():
        return Timeline.load(tp)
    if usb is not None:
        ut = usb.find(p)
        if ut is not None:
            ep = usb.ear_timeline(ut, tracks_dir, check_duration=False)
            if ep is not None:
                return Timeline.load(ep)
    raise FileNotFoundError(f"no ear timeline for {p}")


def is_holdout(track: Path, fraction: float = HOLDOUT_FRACTION) -> bool:
    h = int(hashlib.sha256(track.name.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < fraction


def sweep_sample(usb, n: int = SWEEP_SAMPLE_N, exclude: set[str] | None = None) -> list:
    """A deterministic sample of the USB's non-scored, complete tracks (sha256 of the file name order)."""
    if usb is None or n <= 0:
        return []
    ex = {unicodedata.normalize("NFC", e) for e in (exclude or set())}
    cands = [t for t in usb.tracks() if t.complete and t.name not in ex]
    cands.sort(key=lambda t: hashlib.sha256(t.name.encode("utf-8")).hexdigest())
    return cands[:n]


def sweep_set_id(tracks: list) -> str | None:
    if not tracks:
        return None
    names = sorted(unicodedata.normalize("NFC", t.name) for t in tracks)
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:12]


@dataclass
class TrackEval:
    track: str
    holdout: bool
    status: str
    compliance: float | None
    verdicts: dict[str, str]          # rule -> status
    quality: dict
    frames_digest: str
    driven: bool = False              # the plan ran the sections (drive mode + a phrase table)
    track_id: int | None = None       # rekordbox id when known
    drop_offsets: list = field(default_factory=list)   # loop/metrics.drop_offsets per ear drop

    @classmethod
    def from_dict(cls, d: dict) -> "TrackEval":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def rules_fingerprint() -> str:
    """Identifies the scorer, so a baseline from an older scorer is never compared against.

    Hashes the rule ids + severities AND the scorer's source bytes: a change to a rule's logic or a
    threshold (2026-10-25, D4/D5) must force a baseline regeneration just like a new rule does, or the
    gate would report the scorer's own change as track flips."""
    ids = "|".join(f"{r}:{s}" for r, s, _ in RULES)
    src = (Path(__file__).resolve().parents[1] / "sim" / "score.py").read_bytes()
    return hashlib.sha256(ids.encode() + b"\n" + src).hexdigest()[:12]


@dataclass
class Evaluation:
    params: dict
    tracks: list[TrackEval] = field(default_factory=list)
    rules: str = field(default_factory=rules_fingerprint)
    seed_range: list | None = None   # the normalizer seed the runs started from; None = older baseline (pre 2026-10-24)
    mode: str = "classifier"         # "classifier" | "drive" (the rekordbox plan ran the sections)
    analysis_fingerprint: str | None = None   # rekordbox analysis of the SCORED tracks (usb_export.analysis_fingerprint)
    sweep: list[TrackEval] = field(default_factory=list)   # the generalisation sample (marker-free rules)
    sweep_set: str | None = None

    def subset(self, holdout: bool) -> list[TrackEval]:
        return [t for t in self.tracks if t.holdout == holdout]

    @staticmethod
    def _score(tracks: list[TrackEval], with_offsets: bool = True) -> dict:
        """Aggregate: severity-weighted compliance, critical fails, quality means, drop placement."""
        if not tracks:
            d = {"n": 0, "compliance": None, "critical_fails": 0, "blocked": 0, "quality": {}}
            if with_offsets:
                d.update(offsets_summary([]))
                d["driven"] = 0
            return d
        comp = [t.compliance for t in tracks if t.compliance is not None]
        crit = sum(1 for t in tracks for r, s in t.verdicts.items()
                   if s == "FAIL" and next(sev for rid, sev, _ in RULES if rid == r) == "critical")
        qkeys = ("distinct_looks", "brightness_dynamic_range", "fixture_utilization", "drop_response_mean")
        quality = {}
        for k in qkeys:
            vals = [t.quality.get(k) for t in tracks if isinstance(t.quality.get(k), (int, float))]
            quality[k] = round(sum(vals) / len(vals), 3) if vals else None
        d = {"n": len(tracks), "compliance": round(sum(comp) / len(comp), 4) if comp else None,
             "critical_fails": crit, "blocked": sum(1 for t in tracks if t.status == "BLOCKED"), "quality": quality}
        if with_offsets:
            d.update(offsets_summary([t.drop_offsets for t in tracks]))
            d["driven"] = sum(1 for t in tracks if t.driven)
        return d

    def summary(self) -> dict:
        return {"tuning": self._score(self.subset(False)), "holdout": self._score(self.subset(True)),
                "sweep": self._score(self.sweep, with_offsets=False), "mode": self.mode}

    def to_json(self) -> dict:
        return {"params": self.params, "rules": self.rules, "seed_range": self.seed_range, "mode": self.mode,
                "analysis_fingerprint": self.analysis_fingerprint, "sweep_set": self.sweep_set,
                "summary": self.summary(),
                "tracks": [t.__dict__ for t in self.tracks], "sweep": [t.__dict__ for t in self.sweep]}

    @classmethod
    def from_json(cls, d: dict) -> "Evaluation":
        ev = cls(params=d["params"], rules=d.get("rules", "unknown"), seed_range=d.get("seed_range"),
                 mode=d.get("mode", "classifier"), analysis_fingerprint=d.get("analysis_fingerprint"),
                 sweep_set=d.get("sweep_set"))
        ev.tracks = [TrackEval.from_dict(t) for t in d["tracks"]]
        ev.sweep = [TrackEval.from_dict(t) for t in d.get("sweep", [])]
        return ev


_AUDIO: dict[str, object] = {}


def _audio(p: Path):
    """Decoded audio, cached for the process: the search evaluates the same tracks thousands of times."""
    key = str(p)
    if key not in _AUDIO:
        _AUDIO[key] = decode_audio(p)
    return _AUDIO[key]


def resolve_usb(arg: str | None):
    """CLI helper: ``None`` -> the default export copy (or None), ``'none'`` -> None, a path -> UsbExport."""
    from ..rekordbox.usb_export import UsbExport, default_usb_root
    if arg is not None and str(arg).lower() == "none":
        return None
    root = Path(arg) if arg else default_usb_root()
    return UsbExport(root) if root is not None else None


def _track_eval(name: str, holdout: bool, r, tl: Timeline, rig_obj, driven: bool, track_id: int | None,
                offsets: bool) -> TrackEval:
    sc = score_run(Run(r.frames, r.t, r.signals, tl, rig_obj))
    return TrackEval(track=name, holdout=holdout, status=sc["status"], compliance=sc["compliance"],
                     verdicts={v["rule"]: v["status"] for v in sc["verdicts"]}, quality=sc["quality"],
                     frames_digest=r.frames_digest(), driven=driven, track_id=track_id,
                     drop_offsets=drop_offsets(r.signals, tl) if offsets else [])


def evaluate(params: dict | None, tracks: list[Path] | None = None, rig: str = "rigs/josh_rig.json",
             gestures: str | None = None, seed_range=None, on_track=None, *, usb=None, drive: bool = False,
             sweep_tracks: list | None = None, tracks_dir: str | Path = "tracks") -> Evaluation:
    """Score the labeled set (and, with ``usb``, the sweep sample) under ``params``.

    ``usb``: a ``rekordbox.usb_export.UsbExport``; each scored track then simulates with its own phrase
    table / grid / waveform / id / look notes. ``drive``: the plan runs the sections (``mode="drive"``).
    ``sweep_tracks``: UsbTracks (from ``sweep_sample``) scored on marker-free rules with a marker-less
    timeline; their audio is decoded uncached.
    """
    from ..fixtures.rig import load_rig
    from ..rekordbox.look_notes import load_look_notes, look_notes_path_for
    tracks = tracks if tracks is not None else labeled_tracks(tracks_dir, usb)
    seed_range = tuple(seed_range) if seed_range is not None else default_seed_range()
    ev = Evaluation(params=dict(params or {}), seed_range=[float(seed_range[0]), float(seed_range[1])],
                    mode="drive" if drive else "classifier")
    rig_obj = load_rig(rig)
    scored_uts = []
    for p in tracks:
        tl = ear_timeline_for(p, usb, tracks_dir)
        phrases = grid = wave = None
        track_id = None
        if usb is not None:
            ut = usb.find(p)
            if ut is not None:
                scored_uts.append(ut)
                phrases, grid, wave = usb.load_analysis(ut)
                track_id = ut.info.id
        look = load_look_notes(look_notes_path_for(p.stem, tracks_dir))   # ValueError on a bad file: loud on purpose
        driven = bool(drive and phrases is not None)
        r = run_sim(p, tl, rig, gestures=gestures, seed_range=seed_range, params=params, audio=_audio(p),
                    phrases=phrases, drive=driven, grid=grid, waveform=wave, track_id=track_id or 1, look=look)
        te = _track_eval(p.name, is_holdout(p), r, tl, rig_obj, driven, track_id, offsets=True)
        ev.tracks.append(te)
        if on_track:
            on_track(te)
    if usb is not None and scored_uts:
        ev.analysis_fingerprint = usb.analysis_fingerprint(scored_uts)
    for ut in sweep_tracks or []:
        phrases, grid, wave = usb.load_analysis(ut)
        tl = usb.timeline(ut, grid)
        look = load_look_notes(look_notes_path_for(ut.stem, tracks_dir))
        audio = decode_audio(ut.audio)
        driven = bool(drive and phrases is not None)
        r = run_sim(ut.audio, tl, rig, gestures=gestures, seed_range=seed_range, params=params, audio=audio,
                    phrases=phrases, drive=driven, grid=grid, waveform=wave, track_id=ut.info.id, look=look)
        te = _track_eval(ut.name, False, r, tl, rig_obj, driven, ut.info.id, offsets=False)
        ev.sweep.append(te)
        if on_track:
            on_track(te)
    ev.sweep_set = sweep_set_id(sweep_tracks or [])
    return ev
