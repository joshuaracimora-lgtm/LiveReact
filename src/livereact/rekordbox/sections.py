"""Per-section features for every planned section, from the USB's analysis files alone.

The Track Classifier agent (docs/AGENT_BRIEF.md) pools these over the whole library to find
classes of sections -- which kind of drop, which kind of break -- and section_classes.py
turns its rules into a class per planned section. Everything here is file-only (PSSI phrase
table, PQTZ grid, PWV5 colour waveform): no audio decode, no simulation, so all ~220 tracks
measure in seconds. Deterministic, like the planner it reads.

``track_profile`` is the track's overall frequency balance (decision D-23: high-frequency songs
get bright colours, low get dark, the middle blue).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .pssi import PhraseTable, beat_to_seconds
from .waveform import ColorWaveform

FEATURES_VERSION = 2   # bump when a feature's meaning changes: invalidates every cached file

# the feature names a class rule may test (section_classes.json "rule" keys)
FEATURE_NAMES = ("bars", "energy", "peak", "low_share", "mid_share", "high_share", "energy_delta",
                 "rel_position", "inner_beats", "phrases", "roll")

# track profile thresholds on the whole-track high minus low share (D-23); see track_profile
PROFILE_SPLIT = 0.06


@dataclass(frozen=True)
class SectionFeatures:
    index: int
    kind: str              # planner section kind (drop, build, verse, breakdown, intro, outro)
    bucket: str
    label: str             # rekordbox's phrase label at the section start
    beat: int
    end_beat: int
    t0: float | None       # seconds (None without a grid/bpm)
    t1: float | None
    bars: float
    energy: float          # mean waveform height 0..1
    peak: float            # 90th percentile height 0..1
    low_share: float       # red / (r+g+b)
    mid_share: float
    high_share: float
    energy_delta: float    # energy minus the previous section's (0 for the first)
    rel_position: float    # t0 / track length, 0..1 (0 when unknown)
    inner_beats: int       # rekordbox Up-phrase extra beats (beat2/3/4) inside the section
    phrases: int           # rekordbox phrases the section spans
    roll: int = 0          # 1 = the planner's snare-roll lead-in before a drop (planner PLAN_ROLL_BEATS)

    def get(self, name: str) -> float:
        return float(getattr(self, name))


def section_features(plan, table: PhraseTable, *, grid=None, waveform: ColorWaveform | None = None,
                     bpm: float = 0.0, beats_per_bar: int = 4) -> list[SectionFeatures]:
    """Features of each of ``plan.sections`` (a rekordbox/planner.py TrackPlan)."""
    def seconds(beat: int) -> float | None:
        try:
            return beat_to_seconds(beat, grid, bpm=bpm or None)
        except ValueError:
            return None

    length = waveform.duration_s if waveform is not None else None
    out: list[SectionFeatures] = []
    prev_energy = None
    for s in plan.sections:
        t0, t1 = seconds(s.beat), seconds(s.end_beat)
        energy = peak = 0.0
        low = mid = high = 0.0
        if waveform is not None and t0 is not None and t1 is not None and t1 > t0:
            energy, peak = waveform.energy(t0, t1), waveform.peak(t0, t1)
            low, mid, high = waveform.band_shares(t0, t1)
        else:
            energy = s.energy
        spanned = [p for p in table.phrases if s.beat <= p.beat < s.end_beat]
        inner = sum(1 for p in spanned for b in (p.beat2, p.beat3, p.beat4) if b)
        out.append(SectionFeatures(
            index=s.index, kind=s.kind, bucket=s.bucket, label=s.label, beat=s.beat, end_beat=s.end_beat,
            t0=None if t0 is None else round(t0, 3), t1=None if t1 is None else round(t1, 3),
            bars=round(s.length / beats_per_bar, 3), energy=round(energy, 4), peak=round(peak, 4),
            low_share=round(low, 4), mid_share=round(mid, 4), high_share=round(high, 4),
            energy_delta=round(energy - prev_energy, 4) if prev_energy is not None else 0.0,
            rel_position=round(t0 / length, 4) if (t0 is not None and length) else 0.0,
            inner_beats=inner, phrases=max(1, len(spanned)), roll=int(bool(getattr(s, "roll", False)))))
        prev_energy = energy
    return out


def track_profile(waveform: ColorWaveform | None) -> str:
    """'high' | 'mid' | 'low' | '' -- the whole track's frequency balance (D-23)."""
    if waveform is None or not len(waveform):
        return ""
    low, _mid, high = waveform.band_shares(0.0, waveform.duration_s)
    if low == 0.0 and high == 0.0:
        return ""
    if high - low > PROFILE_SPLIT:
        return "high"
    if low - high > PROFILE_SPLIT:
        return "low"
    return "mid"


# (darkest, brightest) high-minus-low share inside each profile; the USB library (215 tracks,
# 2026-10-28) spans -0.34..+0.38, so the outer edges clip the few extremes rather than stretch for them
PROFILE_SPANS = {"low": (-0.30, -PROFILE_SPLIT), "mid": (-PROFILE_SPLIT, PROFILE_SPLIT), "high": (PROFILE_SPLIT, 0.30)}


def profile_position(waveform: ColorWaveform | None) -> float | None:
    """0..1: where the track sits inside its own profile, bassiest to brightest (palette-v2, D-23).
    None without a usable waveform."""
    prof = track_profile(waveform)
    if not prof:
        return None
    low, _mid, high = waveform.band_shares(0.0, waveform.duration_s)
    lo, hi = PROFILE_SPANS[prof]
    return max(0.0, min(1.0, (high - low - lo) / (hi - lo)))


def library_features(usb, cache_dir: str | Path | None = None, *, limit: int | None = None,
                     on_track=None) -> dict[str, dict]:
    """Every complete USB track -> {"id", "title", "bpm", "profile", "sections": [SectionFeatures dicts]}.

    Keyed by the audio file stem. Cached per track under ``cache_dir`` (one JSON per track, named by a hash
    of its analysis + FEATURES_VERSION + the planner source), so a night re-measures only what changed.
    Plans use the default scene pool and no look notes: the classes describe the music, not Josh's
    per-track overrides."""
    from .planner import plan_track
    cdir = Path(cache_dir) if cache_dir else None
    if cdir:
        cdir.mkdir(parents=True, exist_ok=True)
    planner_src = (Path(__file__).with_name("planner.py")).read_bytes()
    out: dict[str, dict] = {}
    n = 0
    for ut in usb.tracks():
        if not ut.complete:
            continue
        if limit is not None and n >= limit:
            break
        n += 1
        key = hashlib.sha256(usb.analysis_fingerprint([ut]).encode() + f"|{FEATURES_VERSION}|".encode()
                             + hashlib.sha256(planner_src).digest()).hexdigest()[:16]
        cached = cdir / f"{ut.info.id}-{key}.json" if cdir else None
        if cached is not None and cached.exists():
            rec = json.loads(cached.read_text(encoding="utf-8"))
        else:
            table, grid, wave = usb.load_analysis(ut)
            if table is None:
                continue
            plan = plan_track(table, track_id=ut.info.id, title=ut.info.title, bpm=ut.info.bpm, grid=grid,
                              waveform=wave)
            feats = section_features(plan, table, grid=grid, waveform=wave, bpm=ut.info.bpm)
            rec = {"id": ut.info.id, "title": ut.info.title, "bpm": ut.info.bpm, "profile": track_profile(wave),
                   "sections": [asdict(f) for f in feats]}
            if cached is not None:
                cached.write_text(json.dumps(rec), encoding="utf-8")
        out[ut.stem] = rec
        if on_track:
            on_track(ut.stem, rec)
    return out
