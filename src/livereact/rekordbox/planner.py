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
from dataclasses import dataclass, field, replace

from . import mapping
from .look_notes import LookNotes
from .pssi import PhraseTable, beat_to_seconds
from .section_classes import ClassBook
from .sections import profile_position, section_features, track_profile
from .song_style import DEFAULT_STYLE, SongStyle, choose as choose_style, song_features
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
REGULAR_DROP_HIT = 0.92      # params.json knob (tunable): must stay under the laser fan threshold 0.95
# params.json knobs (tunable); PlanRules reads them at construction so params.apply reaches a plan
PREARM_BEATS = 16            # drop_armed this many beats before a planned drop (core/metronome.py)
MIN_SECTION_BEATS = 32       # interior break/drop/outro sections shorter than this (8 bars = TIME-11's
                             # BREAK/DROP dwell) are absorbed into their predecessor
MIN_BUILD_BEATS = 16         # builds may be 4 bars
# The snare roll that leads into a drop (Josh's drop, D-21): rekordbox's chorus starts where the bass
# returns, Josh's drop at the roll 4-8 bars earlier. Measured 2026-10-28 on the 7 tuning tracks: of the
# 13 ear drops right before a planned build -> drop boundary, 8 start exactly 8 bars before it, 5 exactly
# 4 bars. Rekordbox's inner Up beats, phrase starts and the colour waveform's onsets did not tell the two
# apart; the waveform's loudness did: a 4-bar roll is a crescendo over those 4 bars (PLAN_ROLL_RISE, below).
# The planner splits the last PLAN_ROLL_BEATS of the build off as its own drop section (a roll lead-in),
# half of it on a crescendo, unless that would leave the build under PLAN_ROLL_MIN_BUILD_BEATS: TIME-02's 8-bar build look hold plus the up-to-2-bar lag of
# the phrase engine's look change after the bucket enters BUILD (seen on the sweep: an 8-bar build shows
# 'Rising Tide' for 6 bars).
PLAN_ROLL_BEATS = 32              # params.json knob (tunable)
# Was 40 (TIME-02's 8-bar build look + lag) until 2026-10-29: on the bench, Thriller (BVRNOUT remix) has
# 36-beat builds, so the 40-beat guard refused every roll and the strobe only counted in over the last
# 2 bars -- Josh: "the strobing didn't match the snare roll ... that's not a true build-up". With the
# guard at one bar the planner's 8-bar roll lands on 76.74-91.98 s and 145.31-160.55 s against Josh's
# ear notes 76.73-90.06 (groove 91.96) and 145.30-158.63 (groove 160.54). The roll never eats a build
# that is not longer than itself: an 8-bar build stays a build (the roll IS Josh's drop, D-21, but a
# whole-build roll would turn every short build into a 7-bar strobe -- not tried on hardware).
PLAN_ROLL_MIN_BUILD_BEATS = 4     # params.json knob (tunable)
# Waveform energy over bars 4..2 before the drop over bars 8..6 (the last bar of each half skipped: it holds
# the pre-drop gap). Tuning tracks: all 5 four-bar ear rolls rise 1.23-1.96x, 7 of the 8 eight-bar ones
# 0.21-1.14x (the eighth, a clap drop, 1.44x).
PLAN_ROLL_RISE = 1.2              # params.json knob (tunable)
# D-36 (2026-10-30): a roll lead-in -- and any strobe before the drop -- only for drops within this share
# of the song's loudest drop. Josh on Thriller's first chorus (88 % of the big moment): "I wouldn't really
# classify that as a buildup ... LiveReact ... would be able to tell that that buildup isn't building up to
# a high-intensity part." Lesser drops still land as drops, with no strobe lead-in.
ROLL_STROBE_MIN_SHARE = 0.9
# The Tease (P-24, 2026-10-30, Josh: "when the kicks and claps leave, yet the intensity is still high ...
# usually before a drop to tease the incoming drop"): the kick-free bars right before a drop at least
# TEASE_MIN_SHARE of the song's loudest drop (looser than the roll strobe's share -- Josh: "Tease loose,
# strobe strict"). A bar is kick-free when rekordbox's colour waveform keeps its low band (red, 0..7) at or
# under TEASE_KICK_RED at the beat onsets: kick bars read 6-7 there on every track checked (Tongue Tied,
# Thriller), the kick-free stretches 0-4.5. A drum bar alone between kick-free bars is a hit, not the
# groove (Tongue Tied 1:22.8): the Tease runs through it (the show punches it live).
TEASE_MIN_SHARE = 0.7
# ... and only on an intense song (Josh, 2026-10-30: "the justification for a tease [is] the intensity of the
# song ... It should only trigger when it is an intense song"): the mean waveform height of its drops at least
# TEASE_SONG_MIN_DROP_ENERGY -- the top ~40 % of the 351-track Mac library (Thriller 0.574, 79th percentile;
# Tongue Tied 0.674, 96th; ANOTR "Vertigo" 0.19 and FISHER "Ocean" 0.43 stay out). A song under it keeps the
# P-20 riser in its kick-free breaks. Ear-note tease cues are orders and ignore it.
TEASE_SONG_MIN_DROP_ENERGY = 0.53
TEASE_KICK_RED = 4.5
TEASE_MAX_BEATS = 64       # a Tease is the last 16 bars at most before its drop; earlier kick-free bars keep the break
                           # look (library check 2026-10-30: uncapped, 25 % of the 633 Teases ran past 24 s, the longest 88 s)
# A drop rekordbox leaves inside an "up" phrase (Tongue Tied: the kick returns at 1:28.7, bar 46, mid
# phrase, so the planner held one 50 s build): a run of at least TEASE_SPLIT_MIN_BEATS kick bars after at
# least TEASE_SPLIT_MIN_FREE_BEATS kick-free ones inside a build, loud enough for a Tease, is a drop.
TEASE_SPLIT_MIN_BEATS = 16
TEASE_SPLIT_MIN_FREE_BEATS = 8

# default section energies when no waveform is available (ranking then falls back to length)
_KIND_ENERGY = {"drop": 0.8, "build": 0.5, "verse": 0.4, "breakdown": 0.2, "intro": 0.15, "outro": 0.2}


@dataclass(frozen=True)
class PlanRules:
    repeat_hook: bool = True
    one_big_moment: bool = True
    builds_count_in: bool = True
    quiet_edges: bool = True
    # the strobe count-in length is engines/reactive.py BUILD_COUNTIN_BEATS and the laser tension
    # lead is engines/laser_feature.py TENSION_LEAD_BEATS; these three read the module knobs above
    prearm_beats: int = field(default_factory=lambda: PREARM_BEATS)
    min_section_beats: int = field(default_factory=lambda: MIN_SECTION_BEATS)
    min_build_beats: int = field(default_factory=lambda: MIN_BUILD_BEATS)
    roll_beats: int = field(default_factory=lambda: PLAN_ROLL_BEATS)               # 0 = no roll lead-ins
    roll_min_build_beats: int = field(default_factory=lambda: PLAN_ROLL_MIN_BUILD_BEATS)
    roll_rise: float = field(default_factory=lambda: PLAN_ROLL_RISE)
    roll_min_share: float = field(default_factory=lambda: ROLL_STROBE_MIN_SHARE)


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
    fill_beat: int | None = None      # rekordbox's fill-in flag (unused yet; the scorer's STRUCT-01 vocabulary)
    cls: str = ""                     # section class (rekordbox/section_classes.py); "" = none matched
    roll: bool = False                # a roll lead-in (PLAN_ROLL_BEATS): a drop section the next drop continues
    laser_scene: str = ""             # look notes: the fan's look here ("" = its default); the big moment's cue wins

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
    rules: PlanRules = field(default_factory=PlanRules)
    source: str = "rekordbox"
    palette: str = ""                      # look notes hook_palette: one hue for the drop family, else the seed walk
    mood_words: tuple[str, ...] = ()       # look notes mood, for the tools / report
    style: SongStyle = DEFAULT_STYLE       # the song's own look set (song_style.py), held for the whole track
    laser_scene: str = ""                  # look notes laser_scene: the fan's look on every moment ("" = default)
    laser_cues: tuple[tuple[str, str], ...] = ()   # look notes laser_cues: (trigger, scene) for one kind of moment
    cues: tuple[tuple[int, int, str], ...] = ()    # look notes cues as (beat, end_beat, look): timed look overrides
    flash_white: float = 0.0                       # D-36: song brightness 0..1 -> how white the strobe flashes / holds are
    teases: tuple[tuple[int, int], ...] = ()       # P-24: Tease windows as (beat, end_beat), kick-free bars into a drop
    teases_bright: tuple[int, ...] = ()            # P-24: start beats of the Teases that start inside a drop (bright)

    def cue_look(self, beat: int) -> str:
        """The timed look override running at ``beat`` ("" = none). Later cues win on an overlap."""
        look = ""
        for b0, b1, name in self.cues:
            if b0 <= beat < b1:
                look = name
        return look

    def tease_at(self, beat: int) -> float:
        """Progress 0..1 through the Tease running at ``beat``; -1.0 = none (P-24)."""
        for b0, b1 in self.teases:
            if b0 <= beat < b1:
                return min(1.0, max(0.0, (beat - b0) / max(1, b1 - b0)))
        return -1.0

    def tease_bright(self, beat: int) -> bool:
        """The Tease running at ``beat`` started inside a drop (an ear-note cue): bright, following the stabs."""
        return any(b0 <= beat < b1 and b0 in self.teases_bright for b0, b1 in self.teases)

    def tease_hold(self, beat: int) -> bool:
        """``beat`` is inside a roll lead-in a Tease runs straight into: the wash holds the Tease's level
        under the roll strobe (P-24, Josh: "it cant jump from intense to not intense")."""
        for s in self.sections:
            if s.roll and s.beat <= beat < s.end_beat:
                return any(b1 == s.beat for _, b1 in self.teases)
        return False

    def roll_ahead(self, beat: int) -> bool:
        """The next planned drop boundary strictly ahead of ``beat`` is a roll lead-in (PlannedSection.roll):
        the roll is the count-in, so the strobe count-in stays quiet (engines/reactive.py)."""
        for i, s in enumerate(self.sections):
            if s.kind == "drop" and s.beat > beat and not self.continues_roll(i):
                return bool(s.roll)
        return False

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
        for i, s in enumerate(self.sections):
            # consecutive drops were merged, so each one is a boundary -- except the drop a roll
            # lead-in runs into: that drop has already landed on the roll
            if s.kind == "drop" and s.beat > beat and not self.continues_roll(i):
                return s.beat - beat
        return -1

    def continues_roll(self, i: int) -> bool:
        """Section ``i`` is the drop a roll lead-in (PlannedSection.roll) runs straight into."""
        return (0 < i < len(self.sections) and self.sections[i].kind == "drop"
                and self.sections[i - 1].roll and self.sections[i - 1].end_beat == self.sections[i].beat)

    @property
    def drops(self) -> list[PlannedSection]:
        return [s for s in self.sections if s.kind == "drop"]

    def summary(self) -> str:
        parts = []
        for s in self.sections:
            tag = "**" if s.big_moment else ("*" if s.is_hook else "")
            parts.append(f"{s.label}={'roll' if s.roll else s.kind}{tag}@{s.beat}")
        return (f"plan track {self.track_id} {self.title!r} palette_seed={self.palette_seed} hook={self.hook_scene!r} "
                f"style[{self.style.summary()}]: " + " > ".join(parts))


def _look_big_moment(look: LookNotes | None, raw: list[dict], runs: list[list[int]], *, grid, bpm: float) -> list[int] | None:
    """The drop section Josh named in the look notes, as a run; [] for "none"; None = decide automatically."""
    if look is None:
        return None
    if look.no_drop:
        return []
    bm = look.big_moment
    if bm == "auto":
        return None
    if bm == "none":
        return []
    drops = [r for r in raw if r["kind"] == "drop" and not r.get("roll")]   # a roll lead-in is part of its drop
    if not drops:
        return []
    if "drop" in bm:
        k = int(bm["drop"])
        return [drops[min(max(k, 1), len(drops)) - 1]["index"]]
    beat = None
    if "beat" in bm:
        beat = int(bm["beat"])
    elif "t" in bm:
        t = float(bm["t"])
        if grid:
            beat = max(1, sum(1 for g in grid if g[2] / 1000.0 <= t))
        elif bpm > 0:
            beat = int(t * bpm / 60.0) + 1
    if beat is None:
        return None
    inside = [r for r in drops if r["beat"] <= beat < r["end_beat"]]
    if inside:
        return [inside[0]["index"]]
    nearest = min(drops, key=lambda r: abs(r["beat"] - beat))
    return [nearest["index"]]


def _pick(pool: dict[str, list[str]], bucket: str, seed: int) -> str:
    names = pool.get(bucket) or DEFAULT_SCENE_POOL.get(bucket) or []
    if not names:
        return ""
    return names[seed % len(names)]


def _timed_cues(look: "LookNotes", seconds, end_beat: int) -> tuple[tuple[int, int, str], ...]:
    """Look-note cues as (beat, end_beat, look). A cue given in seconds lands on the first grid beat at
    or after that time (the grid is the XDJ's own, so Josh's ear times taken from the file line up)."""
    out = []
    for c in look.cues:
        if c.beat is not None:
            b = int(c.beat)
        else:
            b = None
            for cand in range(1, max(2, int(end_beat)) + 1):
                t = seconds(cand)
                if t is not None and t >= float(c.t) - 1e-6:
                    b = cand
                    break
            if b is None:
                continue   # past the end of the grid
        out.append((b, b + int(c.beats), c.look))
    return tuple(out)


def _bar_kick_red(waveform: ColorWaveform | None, seconds, bar_beat: int) -> float | None:
    """The waveform's low band (red, 0..7) at the four beat onsets of the bar starting at ``bar_beat``
    (the loudest red in the first eighth of each beat, averaged): ~7 with a kick, low without one."""
    if waveform is None:
        return None
    ts = [seconds(bar_beat + k) for k in range(5)]
    if any(t is None for t in ts):
        return None
    vals = []
    for a, b in zip(ts, ts[1:]):
        i0, i1 = waveform.window(a, a + max(1e-3, (b - a) / 8.0))
        if i1 <= i0:
            return None
        vals.append(max(waveform.reds[i0:i1]))
    return sum(vals) / len(vals)


def _kick_free(waveform: ColorWaveform | None, seconds, bar_beat: int) -> bool | None:
    red = _bar_kick_red(waveform, seconds, bar_beat)
    return None if red is None else red <= TEASE_KICK_RED


def plan_track(table: PhraseTable, *, track_id: int, title: str = "", bpm: float = 0.0,
               grid: list[tuple[int, float, int]] | None = None, waveform: ColorWaveform | None = None,
               scene_pool: dict[str, list[str]] | None = None, rules: PlanRules | None = None,
               look: LookNotes | None = None, classes: ClassBook | None = None,
               previous_style: SongStyle | None = None) -> TrackPlan:
    """``classes``: the section classes (section_classes.load()). Every section is tagged with its
    class; only an APPROVED class changes the show (its scene replaces the bucket pick, unless a look
    note names the track's hook scene), and an approved palette_by_profile picks the drop palette
    from the track's frequency profile when no look note names one (D-23). ``previous_style``: the
    style of the song before this one (song_style.py: this one differs from it in at least
    MIN_DIFFERENCES ways)."""
    rules = rules or PlanRules()
    pool = scene_pool or DEFAULT_SCENE_POOL
    seed = int(track_id) if track_id else 0
    hook_scene = (look.hook_scene if look is not None and look.hook_scene else None) or _pick(pool, "drop", seed)
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

    # Exception (D-23, "a drop's look never carries into a break"): a short break-bucket section
    # right after a drop is where the kick leaves (rekordbox "down" 3-4 bars, then "up"); absorbed
    # backwards it would hold the drop look -- PEAK and the laser fan on the big one -- through the
    # breakdown. It joins its successor instead (the build or outro starts where the drop ends);
    # before another drop it stays absorbed into the drop as before.
    def forward(i: int, m: dict) -> bool:
        return (m["bucket"] == "break" and merged[i - 1]["kind"] == "drop"
                and merged[i + 1]["kind"] != "drop")

    absorbed: list[dict] = []
    carry: dict | None = None   # a short break waiting to be prepended to its successor
    for i, m in enumerate(merged):
        if carry is not None:
            m = dict(m, beat=carry["beat"], phrases=m["phrases"] + carry["phrases"],
                     energy=(carry["energy"] * (carry["end_beat"] - carry["beat"])
                             + m["energy"] * (m["end_beat"] - m["beat"])) / max(1, m["end_beat"] - carry["beat"]))
            carry = None
        elif 0 < i < len(merged) - 1 and absorbed and too_short(m):
            if forward(i, m):
                carry = m
                continue
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

    # --- the snare roll before a drop (PLAN_ROLL_BEATS): the end of the build becomes a drop section ---
    def energy_of(b0: int, b1: int, fallback: float) -> float:
        if waveform is not None:
            t0, t1 = seconds(b0), seconds(b1)
            if t0 is not None and t1 is not None and t1 > t0:
                return 0.5 * waveform.energy(t0, t1) + 0.5 * waveform.peak(t0, t1)
        return fallback

    def crescendo(drop_beat: int, half: int) -> bool:
        """The last ``half`` beats before the drop are a crescendo over the ``half`` before them (PLAN_ROLL_RISE)."""
        if waveform is None or half <= 4:
            return False
        ts = [seconds(drop_beat - k) for k in (2 * half, 2 * half - (half - 4), half, 4)]
        if any(t is None for t in ts):
            return False
        before, last = waveform.energy(ts[0], ts[1]), waveform.energy(ts[2], ts[3])
        return before > 0 and last >= rules.roll_rise * before

    # --- P-24: a drop rekordbox left inside a build (see TEASE_SPLIT_*) ---
    if waveform is not None:
        loudest0 = max((float(x["energy"]) for x in merged if x["kind"] == "drop"), default=0.0)
        split: list[dict] = []
        for m in merged:
            if m["kind"] != "build" or loudest0 <= 0.0:
                split.append(m)
                continue
            bars = list(range(m["beat"], m["end_beat"] - 3, 4))
            free = [_kick_free(waveform, seconds, b) for b in bars]
            pieces: list[dict] = []
            cur = dict(m)
            k = 0
            while k < len(bars):
                if free[k] is False and k * 4 >= TEASE_SPLIT_MIN_FREE_BEATS and all(free[max(0, k - TEASE_SPLIT_MIN_FREE_BEATS // 4):k]):
                    j = k
                    while j < len(bars) and free[j] is False:
                        j += 1
                    b0, b1 = bars[k], (bars[j] if j < len(bars) else m["end_beat"])
                    e = energy_of(b0, b1, 0.0)
                    if b1 - b0 >= TEASE_SPLIT_MIN_BEATS and e >= TEASE_MIN_SHARE * loudest0 and b0 > cur["beat"]:
                        pieces.append(dict(cur, end_beat=b0, energy=energy_of(cur["beat"], b0, cur["energy"])))
                        pieces.append(dict(m, beat=b0, end_beat=b1, kind="drop", bucket=mapping.bucket_for_section("drop"),
                                           energy=e, phrases=1, fill_beat=None))
                        cur = dict(m, beat=b1, fill_beat=m["fill_beat"])
                        log.debug("plan %s: drop inside the build at beat %d-%d (energy %.2f)", title, b0, b1, e)
                    k = j
                    continue
                k += 1
            if cur["beat"] < m["end_beat"]:
                if pieces and m["end_beat"] - cur["beat"] < rules.min_build_beats and pieces[-1]["kind"] == "drop":
                    pieces[-1]["end_beat"] = m["end_beat"]   # a stub after the drop belongs to it
                else:
                    pieces.append(dict(cur, end_beat=m["end_beat"],
                                       energy=energy_of(cur["beat"], m["end_beat"], cur["energy"]) if pieces else cur["energy"]))
            split.extend(pieces)
        merged = split

    rolled: list[dict] = []
    loudest = max((float(x["energy"]) for x in merged if x["kind"] == "drop"), default=0.0)
    for i, m in enumerate(merged):
        build = rolled[-1] if rolled else None
        if m["kind"] == "drop":
            # D-36: only a drop near the song's loudest earns a lead-in (roll strobe, count-in)
            m["lead_in"] = loudest <= 0.0 or float(m["energy"]) >= rules.roll_min_share * loudest
        if (m["kind"] == "drop" and m["lead_in"] and build is not None and build["kind"] == "build"
                and rules.roll_beats > 0):
            blen = build["end_beat"] - build["beat"]
            rb = rules.roll_beats // 2 if crescendo(m["beat"], rules.roll_beats // 2) else rules.roll_beats
            if rb > 0 and blen - rb >= rules.roll_min_build_beats:
                start = build["end_beat"] - rb
                roll = dict(build, beat=start, kind="drop", bucket="drop", roll=True, phrases=1, lead_in=True,
                            energy=energy_of(start, build["end_beat"], _KIND_ENERGY["drop"]),
                            fill_beat=build["fill_beat"])
                build["end_beat"] = start
                build["energy"] = energy_of(build["beat"], start, build["energy"])
                build["fill_beat"] = None if (build["fill_beat"] or 0) >= start else build["fill_beat"]
                rolled.append(roll)
        rolled.append(m)
    merged = rolled
    for i, m in enumerate(merged):
        m["index"] = i
    raw = merged

    # --- P-24: the Tease windows (kick-free bars straight into a drop near enough the loudest) ---
    teases: list[tuple[int, int]] = []
    bright: list[int] = []
    drop_spans = [(seconds(x["beat"]), seconds(x["end_beat"])) for x in raw if x["kind"] == "drop" and not x.get("roll")]
    drop_spans = [(a, b) for a, b in drop_spans if a is not None and b is not None and b > a]
    song_drop_energy = (sum(waveform.energy(a, b) for a, b in drop_spans) / len(drop_spans)
                        if waveform is not None and drop_spans else 0.0)
    if waveform is not None and loudest > 0.0 and song_drop_energy >= TEASE_SONG_MIN_DROP_ENERGY:
        def section_at(beat: int) -> dict | None:
            return next((x for x in raw if x["beat"] <= beat < x["end_beat"]), None)
        for i, m in enumerate(raw):
            if m["kind"] != "drop" or (i > 0 and raw[i - 1].get("roll") and raw[i - 1]["end_beat"] == m["beat"]):
                continue   # not a boundary: the drop a roll lead-in runs into has already landed
            body = raw[i + 1] if m.get("roll") and i + 1 < len(raw) else m
            if float(body["energy"]) < TEASE_MIN_SHARE * loudest:
                continue
            start, hit_used, b = m["beat"], False, m["beat"] - 4
            while b >= 1 and m["beat"] - b <= TEASE_MAX_BEATS:
                host = section_at(b)
                if host is None or host["kind"] in ("drop", "outro"):
                    break
                free = _kick_free(waveform, seconds, b)
                if free is None:
                    break
                if not free:
                    # one drum bar between kick-free ones is a hit, not the groove coming back
                    if hit_used or start == m["beat"] or not _kick_free(waveform, seconds, b - 4):
                        break
                    hit_used = True
                start = b if free else start
                b -= 4
            if start < m["beat"]:
                teases.append((start, m["beat"]))
    if look is not None:
        for b0, b1, name in _timed_cues(look, seconds, table.end_beat):
            if name == "tease":   # an ear-note Tease (Josh's times win where the waveform cannot see one)
                # it runs to the next drop boundary (a roll lead-in counts) when that is within 16 bars,
                # so the hand-off lands on the drop; else for the cue's own beats
                nxt = next((x["beat"] for i, x in enumerate(raw) if x["kind"] == "drop" and x["beat"] > b0
                            and not (i > 0 and raw[i - 1].get("roll") and raw[i - 1]["end_beat"] == x["beat"])), None)
                if nxt is not None and nxt - b0 <= 64:
                    b1 = nxt
                teases = [w for w in teases if w[1] <= b0 or w[0] >= b1] + [(b0, b1)]
                host = next((x for x in raw if x["beat"] <= b0 < x["end_beat"]), None)
                if host is not None and host["kind"] == "drop":
                    bright.append(b0)   # it starts inside a drop: bright, following the stabs (engines/tease.py)
    teases.sort()

    # --- the loudest drop section is the big moment ---
    # a roll lead-in and the drop it runs into are one run: one hit, one big moment (Josh's drop IS the roll)
    runs: list[list[int]] = []
    for r in raw:
        if r["kind"] != "drop":
            continue
        if runs and raw[runs[-1][-1]].get("roll") and runs[-1][-1] == r["index"] - 1:
            runs[-1].append(r["index"])
        else:
            runs.append([r["index"]])
    big_run: list[int] = []
    forced = _look_big_moment(look, raw, runs, grid=grid, bpm=bpm)   # None = planner decides
    if forced is not None:
        big_run = next((run for run in runs if forced and forced[0] in run), forced)
    elif runs:
        if rules.one_big_moment:
            def run_score(run: list[int]) -> tuple[float, int, int]:
                body = [i for i in run if not raw[i].get("roll")] or run   # ranked on the drop, not its lead-in
                beats = sum(raw[i]["end_beat"] - raw[i]["beat"] for i in body) or 1
                e = sum(raw[i]["energy"] * (raw[i]["end_beat"] - raw[i]["beat"]) for i in body) / beats
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
        strobe_mode = look.strobe if look is not None else "hard"
        if strobe_mode == "none" or (look is not None and look.no_drop):
            strobe_allowed = False
        elif strobe_mode == "soft":
            strobe_allowed = is_drop and not quiet
        else:
            strobe_allowed = (is_drop or kind == "build") and not quiet
        if kind == "build":
            # D-36: no count-in / roll accent before a drop that is not one of the song's intense parts
            nxt = next((x for x in raw if x["kind"] == "drop" and x["beat"] >= r["end_beat"]), None)
            if nxt is not None and not nxt.get("lead_in", True):
                strobe_allowed = False
        laser_allowed = not quiet
        laser_scene = ""
        if look is not None:
            cues = dict(look.laser_cues)
            laser_scene = (cues.get("big_moment", "") if big else "") or look.laser_scene or ""
        if look is not None and kind in ("intro", "outro"):
            edge = look.intro if kind == "intro" else look.outro
            if edge.laser is not None:
                laser_allowed = edge.laser
            if edge.strobe is not None:
                strobe_allowed = edge.strobe
        sections.append(PlannedSection(
            index=r["index"], beat=r["beat"], end_beat=r["end_beat"], label=r["label"], kind=kind, bucket=bucket,
            scene=per_bucket_scene.get(bucket, ""), energy=round(r["energy"], 4),
            hit=(BIG_MOMENT_HIT if big else REGULAR_DROP_HIT) if is_drop else 0.0,
            is_hook=is_drop and rules.repeat_hook, big_moment=big,
            strobe_allowed=strobe_allowed,
            laser_allowed=laser_allowed,
            fill_beat=r["fill_beat"],
            roll=bool(r.get("roll")),
            laser_scene=laser_scene,
        ))
    # --- the song's own look set (song_style.py, 2026-10-28): its build and break looks replace the pool's ---
    features = song_features(sections, seconds=seconds, waveform=waveform)
    style = choose_style(features, previous_style)
    if features is not None:
        looks = {"build": style.build, "break": style.brk}
        sections = [replace(s, scene=looks[s.bucket]) if s.bucket in looks else s for s in sections]
    palette = (look.hook_palette or "") if look is not None else ""
    if not palette and classes is not None:
        palette = classes.palette_for(track_profile(waveform), seed, profile_position(waveform))
    plan = TrackPlan(track_id=int(track_id), title=title, bpm=float(bpm), mood=table.mood, palette_seed=seed,
                     hook_scene=hook_scene, sections=tuple(sections),
                     big_moment_index=big_run[0] if big_run else None, rules=rules,
                     palette=palette,
                     mood_words=tuple(look.mood) if look is not None else (), style=style,
                     laser_scene=(look.laser_scene or "") if look is not None else "",
                     laser_cues=tuple(look.laser_cues) if look is not None else (),
                     cues=tuple(c for c in _timed_cues(look, seconds, table.end_beat) if c[2] != "tease")
                     if look is not None else (),
                     teases=tuple(teases),
                     teases_bright=tuple(bright),
                     flash_white=round(features.unit("brightness"), 3) if features is not None else 0.0)
    if classes is not None and classes.classes:
        plan = _apply_classes(plan, classes, table, grid=grid, waveform=waveform, bpm=bpm, look=look)
    log.debug("%s", plan.summary())
    return plan


def _apply_classes(plan: TrackPlan, classes: ClassBook, table: PhraseTable, *, grid, waveform, bpm: float,
                   look: LookNotes | None) -> TrackPlan:
    feats = section_features(plan, table, grid=grid, waveform=waveform, bpm=bpm)
    out = []
    for s, f in zip(plan.sections, feats):
        c = classes.classify(f)
        if c is None:
            out.append(s)
            continue
        scene = s.scene
        look_hook = look is not None and look.hook_scene and s.kind == "drop"
        if c.approved and c.scene and not look_hook:
            scene = c.scene
        out.append(replace(s, cls=c.name, scene=scene))
    return replace(plan, sections=tuple(out))
