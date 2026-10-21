"""Evaluate one parameter set: run + score every labeled track, in memory.

Returns a compact ``Evaluation`` the gate and the search can compare. Tracks
are split into a tuning set and a hold-out set deterministically (by a hash
of the file name), so the split is stable as tracks are added and no track
silently moves between sets. Only tracks whose markers are ear-verified (no
``est:`` note, no 'estimated' source) are used at all.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..sim.harness import default_seed_range, run_sim
from ..sim.score import RULES, Run, SEVERITY_WEIGHT, score_run
from ..sim.timeline import Timeline, timeline_path_for

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg"}
HOLDOUT_FRACTION = 0.25


def labeled_tracks(tracks_dir: str | Path = "tracks") -> list[Path]:
    out = []
    for p in sorted(Path(tracks_dir).iterdir()):
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        tp = timeline_path_for(p)
        if not tp.exists():
            continue
        tl = Timeline.load(tp)
        if not tl.markers or any(m.note.startswith("est:") for m in tl.markers) or "estimated" in tl.source:
            continue
        out.append(p)
    return out


def is_holdout(track: Path, fraction: float = HOLDOUT_FRACTION) -> bool:
    h = int(hashlib.sha256(track.name.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < fraction


@dataclass
class TrackEval:
    track: str
    holdout: bool
    status: str
    compliance: float | None
    verdicts: dict[str, str]          # rule -> status
    quality: dict
    frames_digest: str


@dataclass
class Evaluation:
    params: dict
    tracks: list[TrackEval] = field(default_factory=list)

    def subset(self, holdout: bool) -> list[TrackEval]:
        return [t for t in self.tracks if t.holdout == holdout]

    @staticmethod
    def _score(tracks: list[TrackEval]) -> dict:
        """Aggregate: severity-weighted compliance, critical fails, quality means."""
        if not tracks:
            return {"n": 0, "compliance": None, "critical_fails": 0, "blocked": 0, "quality": {}}
        comp = [t.compliance for t in tracks if t.compliance is not None]
        crit = sum(1 for t in tracks for r, s in t.verdicts.items()
                   if s == "FAIL" and next(sev for rid, sev, _ in RULES if rid == r) == "critical")
        qkeys = ("distinct_looks", "brightness_dynamic_range", "fixture_utilization", "drop_response_mean")
        quality = {}
        for k in qkeys:
            vals = [t.quality.get(k) for t in tracks if isinstance(t.quality.get(k), (int, float))]
            quality[k] = round(sum(vals) / len(vals), 3) if vals else None
        return {"n": len(tracks), "compliance": round(sum(comp) / len(comp), 4) if comp else None,
                "critical_fails": crit, "blocked": sum(1 for t in tracks if t.status == "BLOCKED"), "quality": quality}

    def summary(self) -> dict:
        return {"tuning": self._score(self.subset(False)), "holdout": self._score(self.subset(True))}

    def to_json(self) -> dict:
        return {"params": self.params, "summary": self.summary(),
                "tracks": [t.__dict__ for t in self.tracks]}

    @classmethod
    def from_json(cls, d: dict) -> "Evaluation":
        ev = cls(params=d["params"])
        ev.tracks = [TrackEval(**t) for t in d["tracks"]]
        return ev


_AUDIO: dict[str, object] = {}


def _audio(p: Path):
    """Decoded audio, cached for the process: the search evaluates the same tracks thousands of times."""
    key = str(p)
    if key not in _AUDIO:
        from ..sim.harness import decode_audio
        _AUDIO[key] = decode_audio(p)
    return _AUDIO[key]


def evaluate(params: dict | None, tracks: list[Path] | None = None, rig: str = "rigs/josh_rig.json",
             gestures: str | None = None, seed_range=None, on_track=None) -> Evaluation:
    tracks = tracks if tracks is not None else labeled_tracks()
    seed_range = seed_range if seed_range is not None else default_seed_range()
    ev = Evaluation(params=dict(params or {}))
    for p in tracks:
        tl = Timeline.load(timeline_path_for(p))
        r = run_sim(p, tl, rig, gestures=gestures, seed_range=seed_range, params=params, audio=_audio(p))
        from ..fixtures.rig import load_rig
        sc = score_run(Run(r.frames, r.t, r.signals, tl, load_rig(rig)))
        te = TrackEval(track=p.name, holdout=is_holdout(p), status=sc["status"], compliance=sc["compliance"],
                       verdicts={v["rule"]: v["status"] for v in sc["verdicts"]}, quality=sc["quality"],
                       frames_digest=r.frames_digest())
        ev.tracks.append(te)
        if on_track:
            on_track(te)
    return ev
