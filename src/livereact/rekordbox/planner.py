"""Plan a whole track's lighting at load time from its rekordbox phrase table.

Josh, 2026-10-25: "when the track is loaded, it must plan out its scenes
accordingly ... we know that throughout this whole track we can use a certain
lighting scheme". The rules he chose (all four), each a Bible rule too:

* REPEAT THE HOOK (TIME-20): one palette for the track; every chorus/drop uses
  the identical look so the audience learns it.
* RESTRAINT, ONE BIG MOMENT (TIME-21 / LASER-14): only the track's biggest drop
  (by the rekordbox waveform's loudness) gets the full hit -- the laser fan,
  PEAK -- the others get the regular drop look.
* BUILDS COUNT IN (TIME-01 / TIME-04): in the bars before a planned drop the
  strobe and laser tension follow the countdown, black on the last beat, cut
  on the downbeat.
* QUIET EDGES (§9.1 / §9.5): intro and outro stay dim and slow, no strobe, no
  laser; the outro ends in the clean fade for the mix-out.

Pure function of (phrase table, beat grid, waveform, track id, scene pool):
deterministic, so the same track always gets the same plan. No I/O here; the
metronome executes the plan on the live beat counter (core/metronome.py
``_drive_from_plan``) and falls back to the audio classifier the moment the
plan loses authority.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import mapping
from .pssi import PhraseTable, beat_to_seconds
from .waveform import ColorWaveform

log = logging.getLogger(__name__)

# One look per bucket: the core catalog (scenes/library.py CORE_SCENES). The app
# passes the real catalog's names; this is the fallback for tests / offline use.
DEFAULT_SCENE_POOL: dict[str, list[str]] = {
    "break": ["Deep Wash Breathe"],
    "build": ["Rising Tide"],
    "drop": ["Drop Strobe Stab"],
    "peak": ["Peak Full Rig"],
    "outro": ["Outro Fade Wash"],
}

# drop_hit_intensity targets (core/metronome.py DROP_INTENSITY_FLOOR..CEIL = 0.85..1.0;
# the laser fan needs >= FAN_MIN_HIT 0.95): the big moment gets the fan, the rest don't.
BIG_MOMENT_HIT = 1.0
REGULAR_DROP_HIT = 0.92

# default section energies when no waveform is available (ranking then falls back to length)
_KIND_ENERGY = {"drop": 0.8, "build": 0.5, "verse": 0.4, "breakdown": 0.2, "intro": 0.15, "outro": 0.2}


@dataclass(frozen=True)
class PlanRules:
    repeat_hook: bool = True
    one_big_moment: bool = True
    builds_count_in: bool = True
    quiet_edges: bool = True
    prearm_beats: int = 16      # drop_armed this many beats before a planned drop
    tension_beats: int = 32     # laser tension ramps over this countdown
    min_section_beats: int = 32 # interior break/drop/outro sections shorter than this (8 bars = TIME-11's
                                # BREAK/DROP dwell) are absorbed into their predecessor; builds may be 4 bars
    min_build_beats: int = 16


DEFAULT_RULES = PlanRules()


@dataclass(frozen=True)
class PlannedSection:
    index: int
    beat: int
    end_beat: int
    label: str            # rekordbox's label (chorus, up, verse2 ...)
    kind: str             # mapping section kind (drop, build, verse, breakdown, intro, outro)
    bucket: str           # EnergyBucket value the metronome should show
    scene: str            # scene name for the phrase engine
    energy: float         # 0..1 measured from the waveform (or the kind default)
    hit: float            # drop_hit_intensity target when this section is a drop
    is_hook: bool = False
    big_moment: bool = False
    strobe_allowed: bool = True
    laser_allowed: bool = True
    drop_at_beat: int | None = None   # the next planned drop boundary ahead of this section
    fill_beat: int | None = None

    @property
    def length(self) -> int:
        return max(0, self.end_beat - self.beat)


@dataclass(frozen=True)
class TrackPlan:
    track_id: int
    title: str
    bpm: float
    mood: int
    palette_seed: int
    hook_scene: str
    sections: tuple[PlannedSection, ...]
    big_moment_index: int | None = None
    rules: PlanRules = field(default=DEFAULT_RULES)
    source: str = "rekordbox"

    def at_beat(self, beat: int) -> tuple[PlannedSection | None, PlannedSection | None]:
        if not self.sections:
            return None, None
        if beat < self.sections[0].beat:
            return None, self.sections[0]
        for i, s in enumerate(self.sections):
            if s.beat <= beat < s.end_beat:
                return s, (self.sections[i + 1] if i + 1 < len(self.sections) else None)
        return None, None

    def beats_to_drop(self, beat: int) -> int:
        """Beats until the next planned drop boundary strictly ahead of ``beat``; -1 if none."""
        for s in self.sections:
            if s.kind == "drop" and s.beat > beat and (s.index == 0 or self.sections[s.index - 1].kind != "drop"):
                return s.beat - beat
        return -1

    @property
    def drops(self) -> list[PlannedSection]:
        return [s for s in self.sections if s.kind == "drop"]

    def summary(self) -> str:
        parts = []
        for s in self.sections:
            tag = "**" if s.big_moment else ("*" if s.is_hook else "")
            parts.append(f"{s.label}={s.kind}{tag}@{s.beat}")
        return (f"plan track {self.track_id} {self.title!r} palette_seed={self.palette_seed} hook={self.hook_scene!r}: "
                + " > ".join(parts))


def _pick(pool: dict[str, list[str]], bucket: str, seed: int) -> str:
    names = pool.get(bucket) or DEFAULT_SCENE_POOL.get(bucket) or []
    if not names:
        return ""
    return names[seed % len(names)]


def plan_track(table: PhraseTable, *, track_id: int, title: str = "", bpm: float = 0.0,
               grid: list[tuple[int, float, int]] | None = None, waveform: ColorWaveform | None = None,
               scene_pool: dict[str, list[str]] | None = None, rules: PlanRules = DEFAULT_RULES) -> TrackPlan:
    pool = scene_pool or DEFAULT_SCENE_POOL
    seed = int(track_id) if track_id else 0
    hook_scene = _pick(pool, "drop", seed)
    per_bucket_scene = {
        "break": _pick(pool, "break", seed),
        "build": _pick(pool, "build", seed),
        "drop": hook_scene,
        "peak": _pick(pool, "peak", seed),
        "outro": _pick(pool, "outro", seed),
    }

    def seconds(beat: int) -> float | None:
        try:
            return beat_to_seconds(beat, grid, bpm=bpm or None)
        except ValueError:
            return None

    # --- measure every phrase ---
    raw: list[dict] = []
    for i, p in enumerate(table.phrases):
        kind = mapping.section_for(table.mood, p.label)
        bucket = mapping.bucket_for_section(kind)
        energy = _KIND_ENERGY.get(kind, 0.3)
        if waveform is not None:
            t0, t1 = seconds(p.beat), seconds(p.end_beat)
            if t0 is not None and t1 is not None and t1 > t0:
                energy = 0.5 * waveform.energy(t0, t1) + 0.5 * waveform.peak(t0, t1)
        raw.append(dict(index=i, beat=p.beat, end_beat=p.end_beat, label=p.label, kind=kind, bucket=bucket,
                        energy=energy, fill_beat=p.beat_fill if p.fill else None))

    # --- merge consecutive phrases of the same kind into one section ---
    # rekordbox splits a 32-bar chorus into 8-bar "Chorus 1/2/3" phrases; the lights
    # must treat that as ONE section (one commit, one look held, TIME-02/TIME-11),
    # so a run of equal kinds collapses into a single section spanning all of them.
    merged: list[dict] = []
    for r in raw:
        if merged and merged[-1]["kind"] == r["kind"]:
            m = merged[-1]
            m["energy"] = (m["energy"] * (m["end_beat"] - m["beat"]) + r["energy"] * (r["end_beat"] - r["beat"])) \
                / max(1, r["end_beat"] - m["beat"])
            m["end_beat"] = r["end_beat"]
            m["fill_beat"] = r["fill_beat"] if r["fill_beat"] is not None else m["fill_beat"]
            m["phrases"] += 1
        else:
            merged.append(dict(r, phrases=1))
    # rekordbox also emits 2-4 bar micro-phrases around a hook (SWEETER: chorus 2 bars, up 2
    # bars, chorus); a look cannot hold on those (TIME-11 dwell 8 bars) so anything shorter
    # than MIN_SECTION_BEATS is absorbed into its predecessor (the first one into its successor).
    # The first and last sections are left alone (a 4-bar intro is real music, and the
    # scorer's dwell rules exempt the first/last run too).
    def too_short(m: dict) -> bool:
        need = rules.min_build_beats if m["kind"] == "build" else rules.min_section_beats
        return (m["end_beat"] - m["beat"]) < need

    absorbed: list[dict] = []
    for i, m in enumerate(merged):
        if 0 < i < len(merged) - 1 and absorbed and too_short(m):
            prev = absorbed[-1]
            prev["end_beat"] = m["end_beat"]
            prev["phrases"] += m["phrases"]
            continue
        absorbed.append(m)
    # absorbing can put two same-kind sections side by side: merge again
    merged = []
    for m in absorbed:
        if merged and merged[-1]["kind"] == m["kind"]:
            merged[-1]["end_beat"] = m["end_beat"]
            merged[-1]["phrases"] += m["phrases"]
        else:
            merged.append(m)
    for i, m in enumerate(merged):
        m["index"] = i
    raw = merged

    # --- the loudest drop section is the big moment ---
    runs: list[list[int]] = [[r["index"]] for r in raw if r["kind"] == "drop"]
    big_run: list[int] = []
    if runs:
        if rules.one_big_moment:
            def run_score(run: list[int]) -> tuple[float, int, int]:
                beats = sum(raw[i]["end_beat"] - raw[i]["beat"] for i in run) or 1
                e = sum(raw[i]["energy"] * (raw[i]["end_beat"] - raw[i]["beat"]) for i in run) / beats
                return (round(e, 3), beats, run[-1])   # loudest, then longest, then latest
            big_run = max(runs, key=run_score)
        else:
            big_run = [i for run in runs for i in run]
    big_set = set(big_run)

    # --- assemble ---
    sections: list[PlannedSection] = []
    for r in raw:
        kind, bucket = r["kind"], r["bucket"]
        is_drop = kind == "drop"
        big = r["index"] in big_set
        quiet = rules.quiet_edges and kind in ("intro", "outro")
        next_drop = None
        for run in runs:
            if raw[run[0]]["beat"] > r["beat"]:
                next_drop = raw[run[0]]["beat"]
                break
        sections.append(PlannedSection(
            index=r["index"], beat=r["beat"], end_beat=r["end_beat"], label=r["label"], kind=kind, bucket=bucket,
            scene=per_bucket_scene.get(bucket, ""), energy=round(r["energy"], 4),
            hit=(BIG_MOMENT_HIT if big else REGULAR_DROP_HIT) if is_drop else 0.0,
            is_hook=is_drop and rules.repeat_hook, big_moment=big,
            strobe_allowed=(is_drop or kind == "build") and not quiet,
            laser_allowed=not quiet,
            drop_at_beat=next_drop if rules.builds_count_in else None,
            fill_beat=r["fill_beat"],
        ))
    plan = TrackPlan(track_id=int(track_id), title=title, bpm=float(bpm), mood=table.mood, palette_seed=seed,
                     hook_scene=hook_scene, sections=tuple(sections),
                     big_moment_index=big_run[0] if big_run else None, rules=rules)
    log.info("%s", plan.summary())
    return plan
