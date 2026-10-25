"""The scorer: judge a harness run's DMX output against the lighting rules.

Reads ONLY what came out of the (simulated) DMX port -- frames.npz, the
per-tick signals, the track's ear-verified Timeline markers and the rig
profile. It never reads engine code and makes no LLM calls, so it can't be
talked into a verdict. Each rule is a small function returning a Verdict:

  PASS / FAIL            measured here
  UNVERIFIABLE_IN_SIM    physically unmeasurable from bytes (strobe Hz)
  NOT_SCORED             needs something this run lacks (e.g. no markers)

Rule IDs are the bible's (spec/conformance.md) plus STRUCT-* from
spec/PENDING_RULES.md. Scorer v2 (2026-10-21) added STRUCT-04..09 for Josh's
roll / cut / groove vocabulary (whiteout, groove, weak roll, clap-driven drop,
power-off shutter fade, fading build); their thresholds are FIRST GUESSES,
listed together under "scorer v2 tunables" below and in tools/harness/README.md. Severity weights: critical 3, major 2, minor 1. Any
critical FAIL marks the run BLOCKED regardless of score (the hard gate).
Show-quality metrics are reported alongside so an optimizer can't "win" by
doing less (see scorecard()).

Bar/beat arithmetic uses the Timeline grid, never frame counts: the harness
ticks at ~43 Hz (one audio block), not the live writer's 40 Hz.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ..fixtures.profiles import ChannelKind as K
from ..fixtures.rig import load_rig
from .timeline import Marker, Timeline

SEVERITY_WEIGHT = {"critical": 3, "major": 2, "minor": 1}
DROP_FAMILY = ("DROP", "PEAK")
MIN_HOLD_BARS = {"BREAK": 8, "BUILD": 4, "DROP": 8, "PEAK": 8, "OUTRO": 8}    # TIME-11 (OUTRO = BREAK, D4)
LOOK_MIN_HOLD_BARS = {"BREAK": 16, "OUTRO": 16}                                  # TIME-02 (default 8)
LEGAL = {  # scenes/catalog.py LEGAL_TRANSITIONS, as the bible states them -- except OUTRO (D4)
    "BREAK": {"BUILD", "DROP", "OUTRO"}, "BUILD": {"BREAK", "DROP"},
    "DROP": {"BREAK", "BUILD", "PEAK", "OUTRO"}, "PEAK": {"BREAK", "DROP", "OUTRO"},
    "OUTRO": {"BREAK", "BUILD", "DROP"},   # D4 (Josh, 2026-10-25): an outro is a quiet part, never a dead end
}
# D5 (Josh, 2026-10-25): the dwell floors (TIME-11) and look holds (TIME-02) guard against flapping,
# never against following the music. A run that ends within this many bars of an ear-labeled section
# edge (any marker's start, or a drop's labeled end) is a correct reaction, not a violation. The
# opening run of a track is where the engine was standing when the music started, so it is exempt
# like the last run (truncated by the end of the track) already was.
SECTION_CHANGE_TOL_BARS = 1.0
STATIC_CUT_LOOKS = ("Blackout Hit", "Slam")     # LOOK-03: "the cut IS the effect"
BUILD_BARS = 16                                   # window before a labeled drop
FILL_BARS = 8                                     # window after a labeled fill
DROP_LATENCY_MAX_BEATS = 2.0                      # STRUCT-03 threshold (first pass)

# ---- scorer v2 tunables (2026-10-21). All first guesses: tune by arguing with real verdicts.
WHITEOUT_MAX_BARS = 2             # STRUCT-04: window after a whiteout marker (cut short by the next marker)
WHITEOUT_SETTLE_BEATS = 1.0       # STRUCT-04: beats skipped at the top of the window (the cut itself)
WHITE_LEVEL = 0.6                 # STRUCT-04: a wash fixture reads white when W >= this, or min(R,G,B) >= this ...
WHITE_CHROMA = 0.15               # ... with max(R,G,B) - min(R,G,B) <= this
WHITE_DIMMER = 0.6                # STRUCT-04: and its dimmer is at least this
WHITE_FRACTION = 0.75             # STRUCT-04: share of the settled window every wash fixture must be white+lit
DJ_POSE_TOL = 0.03                # STRUCT-04: pan/tilt distance (0..1 units; ~16 deg of a 540 deg pan) that counts as "at the DJ"
DJ_APPROACH_FRACTION = 0.5        # STRUCT-04: or the head closes at least this much of its initial distance
GROOVE_MIN_BARS = 2               # STRUCT-07: shorter groove windows are skipped
GROOVE_CORR_MIN = 0.15            # STRUCT-07a: corr(bar dimmer, synth band) must reach this ...
GROOVE_CORR_MARGIN = 0.05         # ... and beat corr(bar dimmer, kick band) by this
GROOVE_HEAD_SPEED_FRACTION = 0.5  # STRUCT-07b: a frame counts as "at speed" when an axis moves >= this x its slew clamp
GROOVE_HEAD_MOVING_FRACTION = 0.5 # STRUCT-07b: share of frames that must be at speed
GROOVE_LASER_MIN_BARS = 4         # STRUCT-07c: laser cadence needs at least this long a window
GROOVE_BURST_BEATS = (1.5, 4.5)   # STRUCT-07c: a burst is 2-4 beats, +-half a beat
GROOVE_PERIOD_BEATS = (5.0, 10.0) # STRUCT-07c: burst start-to-start; "every other bar" = 8 beats, "~1 bar between" = 6-8
ROLL_ENERGY_RATIO = 1.15          # STRUCT-08: rolls whose high-band energy differs by less than this are not ordered
ROLL_STROBE_VISIBLE_STEP = 20     # STRUCT-08: bytes a weaker roll's strobe ceiling must sit below a stronger one's
CLAP_WORDS = ("claps", "crashes", "clap-driven", "react to the clap", "no snare roll", "no roll")  # STRUCT-09 trigger words in a drop note
#   (plural / explicit on purpose: messy's "starts on the clap" is a roll that lands on a clap, not a clap-driven drop)
CLAP_STROBE_CEILING = 96          # STRUCT-09: max strobe byte ("a soft, slower strobe at most")
CLAP_ONSET_CORR_MIN = 0.15        # STRUCT-09: corr(dimmer up-flux, high-band up-flux) at the best lag ...
CLAP_ONSET_MAX_LAG_TICKS = 3      # ... searched over 0..this many ticks of light-after-audio lag
POWEROFF_PRIOR_LEVEL = 0.15       # STRUCT-05: some fixture must average at least this in the bar before (something to switch off)
POWEROFF_START_BARS = 2           # STRUCT-05: the first step must land within this many bars of the breakdown marker
POWEROFF_MAX_BARS = 2             # STRUCT-05: and the rig must be dark within this many bars of the first step
POWEROFF_STEPS = (3, 6)           # STRUCT-05: distinct downward steps (PENDING_RULES' "3-6 steps over 1-2 bars")
POWEROFF_STEP_MIN = 0.05          # STRUCT-05: a step is a fall of at least this (lit-fixture mean, 0..1) within 3 ticks
POWEROFF_HOLD_TICKS = 3           # STRUCT-05: steps closer than this merge into one edge (a smooth ramp is one edge or none)
POWEROFF_DARK = 0.05              # STRUCT-05: "dark"
FADE_WORDS = ("fade", "fading")   # STRUCT-06: a breakdown whose note says the build fades out into it
FADE_DIM_DROP = 0.10              # STRUCT-06: last-quarter lit-fixture mean must sit this far below the first quarter
FADE_MAX_STEP = 0.05              # STRUCT-06: a per-tick jump bigger than this is not "smooth"
FADE_MAX_RISE_FRACTION = 0.10     # STRUCT-06: share of ticks allowed to rise >= 0.01
FADE_CHROMA_WHITE = 0.15          # STRUCT-06: ending chroma that already counts as white ...
FADE_CHROMA_DROP = 0.10           # ... or chroma must fall by this across the window
FADE_MAX_MOTION = 0.01            # STRUCT-06: max head per-frame delta (0..1) that still reads as "minimal movement"


@dataclass
class Verdict:
    rule: str
    status: str                  # PASS | FAIL | UNVERIFIABLE_IN_SIM | NOT_SCORED
    severity: str
    metric: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)   # [(t_seconds, note)]
    note: str = ""


# ----------------------------------------------------------------- context

class Run:
    """A loaded run with the per-tick arrays every rule needs."""

    def __init__(self, frames, t, signals: list[dict], timeline: Timeline, rig):
        import numpy as np
        self.np = np
        self.frames = frames
        self.t = t
        self.n = len(t)
        self.signals = signals
        self.tl = timeline
        self.rig = rig
        self.dt = float(np.median(np.diff(t))) if self.n > 1 else 0.0232
        self.bucket = np.array([r["bucket"] for r in signals])
        self.scene = np.array([r["scene"] for r in signals])
        self.fixtures = list(rig.fixtures)
        self.slots = [getattr(fx, "slot_id", fx.label) for fx in self.fixtures]
        nf = len(self.fixtures)
        self.has_dimmer = np.zeros(nf, bool)
        self.has_strobe = np.zeros(nf, bool)
        self.dimmer = np.zeros((self.n, nf))
        self.strobe = np.zeros((self.n, nf))
        self.color: list = []          # per fixture: (n, k) float or None
        self.move: list = []           # per fixture: (n, k) float or None
        self.laser_any = np.zeros(self.n, bool)
        for j, fx in enumerate(self.fixtures):
            kinds = fx.channel_kinds()
            cols = frames[:, fx.offset: fx.offset + fx.channel_count].astype(float) / 255.0
            d = [i for i, k in enumerate(kinds) if k == K.DIMMER]
            s = [i for i, k in enumerate(kinds) if k == K.STROBE]
            c = [i for i, k in enumerate(kinds) if k == K.COLOR]
            m = [i for i, k in enumerate(kinds) if k == K.MOVEMENT]
            la = [i for i, k in enumerate(kinds) if k == K.LASER]
            if d:
                self.has_dimmer[j] = True
                self.dimmer[:, j] = cols[:, d[0]]
            if s:
                self.has_strobe[j] = True
                self.strobe[:, j] = cols[:, s[0]]
            self.color.append(cols[:, c] if c else None)
            if m and len(m) % 2 == 0:
                # profiles order movement as pan, pan-fine, tilt, tilt-fine: decode 16-bit pairs
                # so the fine byte's wrap doesn't read as a jump
                pairs = [(m[i], m[i + 1]) for i in range(0, len(m), 2)]
                self.move.append(np.stack([cols[:, a] + cols[:, b] / 255.0 for a, b in pairs], axis=1) * (255.0 / 256.0))
            else:
                self.move.append(cols[:, m] if m else None)
            if la:
                self.laser_any |= (cols[:, la].max(axis=1) > 0)
        self.active = (self.dimmer > 0.02) & self.has_dimmer[None, :]
        self.strobe_any = (self.strobe > 0).any(axis=1)
        self.total = self.dimmer.sum(axis=1) / max(1, int(self.has_dimmer.sum()))
        # scorer v2: the audio bands the engine saw (signals.csv; inputs/audio.py
        # LOW 20-250 Hz = kick, MID 250-2k = synths/snare body, HIGH 2k-16k =
        # claps/hats/roll crack) and the high-band onset rate. Missing columns read 0.
        self.band_low = self._signal_col("band_low")
        self.band_mid = self._signal_col("band_mid")
        self.band_high = self._signal_col("band_high")
        self.onset_rate = self._signal_col("onset_rate")
        self.wash = [j for j, fx in enumerate(self.fixtures) if fx.role == "wash"]
        # "the wash bar": the ok103 bar(s) when the rig has one, else every wash fixture with a dimmer
        bars = [j for j, fx in enumerate(self.fixtures) if fx.type_name == "ok103_wash_bar" and self.has_dimmer[j]]
        self.wash_bar = bars or [j for j in self.wash if self.has_dimmer[j]]
        self.heads = [j for j, mv in enumerate(self.move) if mv is not None and mv.shape[1] >= 2]
        dj = getattr(rig, "dj_pose", None)
        self.dj_pose = [dj(fx) if (dj is not None and j in self.heads) else None for j, fx in enumerate(self.fixtures)]
        # bar index per tick, bar 0 starting at the first grid downbeat
        self.bar_dur = timeline.bar_dur
        self.beat_dur = timeline.beat_dur
        self.db0 = timeline.first_downbeat_s % self.bar_dur
        self.bar_of = np.floor((t - self.db0) / self.bar_dur).astype(int)

    # helpers
    def _signal_col(self, name: str):
        out = self.np.zeros(self.n)
        for i, r in enumerate(self.signals):
            v = r.get(name)
            if v not in (None, ""):
                try:
                    out[i] = float(v)
                except (TypeError, ValueError):
                    pass
        return out

    def lit_mean(self, w, ref=None):
        """Per-tick mean dimmer over the fixtures that were lit (active > 20 % of
        ``ref``, default ``w``) -- so one par powering off reads 1.0 -> 0, not 0.33 -> 0."""
        ref = w if ref is None else ref
        lit = [j for j in range(len(self.fixtures)) if self.has_dimmer[j] and self.active[ref, j].mean() > 0.2]
        if not lit:
            return self.np.zeros(len(w)), []
        return self.dimmer[w][:, lit].mean(axis=1), lit

    def head_clamp(self, j: int):
        """(pan, tilt) max per-frame delta in 0..1 units -- the profile's slew limit
        (LIGHTING_BIBLE.md §6.6, fixtures/profiles.py MovingHead) at this run's tick."""
        fx = self.fixtures[j]
        if not hasattr(fx, "PAN_MAX_DEG_PER_S"):
            return None
        pr, tr = fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG
        cal = getattr(fx, "calibration", None)
        if cal is not None and hasattr(cal, "ranges"):
            pr, tr = cal.ranges(pr, tr)
        return (fx.PAN_MAX_DEG_PER_S * self.dt / pr, fx.TILT_MAX_DEG_PER_S * self.dt / tr)

    def idx(self, t0: float, t1: float):
        return self.np.where((self.t >= t0) & (self.t < t1))[0]

    def runs(self, arr):
        """[(value, start_idx, end_idx_exclusive)] of constant runs."""
        out = []
        if self.n == 0:
            return out
        s = 0
        for i in range(1, self.n + 1):
            if i == self.n or arr[i] != arr[s]:
                out.append((arr[s], s, i))
                s = i
        return out

    def bars_in(self, s: int, e: int) -> float:
        return (self.t[e - 1] - self.t[s] + self.dt) / self.bar_dur

    def markers(self, kind: str) -> list[Marker]:
        return [m for m in self.tl.markers if m.kind == kind]

    def section_edges(self) -> list[float]:
        """Ear-labeled section boundaries: every marker's start plus a drop's labeled end (its cut).
        Empty unless the markers are verified -- an `est:` guess never excuses a short run."""
        if not self.verified_markers():
            return []
        edges = [m.t for m in self.tl.markers] + [m.end for m in self.tl.markers if m.end is not None]
        return sorted(edges)

    def ends_on_section_change(self, e: int) -> bool:
        """True when the run ending at index ``e`` (exclusive) hands over within
        SECTION_CHANGE_TOL_BARS of a labeled section edge (D5)."""
        if e >= self.n:
            return False
        t_change = float(self.t[e])
        tol = SECTION_CHANGE_TOL_BARS * self.bar_dur
        return any(abs(t_change - edge) <= tol for edge in self.section_edges())

    def next_marker_t(self, after: float) -> float:
        later = [m.t for m in self.tl.markers if m.t > after + 1e-6]
        return min(later) if later else float(self.t[-1]) + self.dt

    def verified_markers(self) -> bool:
        return bool(self.tl.markers) and not any(m.note.startswith("est:") for m in self.tl.markers)

    def prev_marker(self, before: float) -> Marker | None:
        earlier = [m for m in self.tl.markers if m.t < before - 1e-6]
        return max(earlier, key=lambda m: m.t) if earlier else None

    def next_marker(self, after: float) -> Marker | None:
        later = [m for m in self.tl.markers if m.t > after + 1e-6]
        return min(later, key=lambda m: m.t) if later else None


# ----------------------------------------------------------------- rules

RULES: list[tuple[str, str, Callable[[Run], Verdict]]] = []


def rule(rid: str, severity: str):
    def deco(fn):
        RULES.append((rid, severity, fn))
        return fn
    return deco


def _needs_markers(run: Run, rid, sev, what="labeled markers") -> Verdict | None:
    if not run.tl.markers:
        return Verdict(rid, "NOT_SCORED", sev, note=f"needs {what} in the timeline")
    if not run.verified_markers():
        return Verdict(rid, "NOT_SCORED", sev, note="markers are unverified estimates (note starts with 'est:')")
    return None


@rule("TIME-11", "critical")
def time_11_bucket_dwell(run: Run) -> Verdict:
    """Each energy state holds its own minimum dwell (bars). OUTRO's floor is BREAK's (D4). The
    opening run and a run that ends on a labeled section change are not scored (D5)."""
    fails, worst = [], {}
    rs = run.runs(run.bucket)
    for k, (b, s, e) in enumerate(rs):
        if k == 0 or k == len(rs) - 1:
            continue  # power-on state / truncated by end of track (D5)
        need = MIN_HOLD_BARS.get(b)
        bars = run.bars_in(s, e)
        if need and bars < need - 0.25 and not run.ends_on_section_change(e):
            fails.append((round(float(run.t[s]), 2), f"{b} held {bars:.1f} bars, needs {need}"))
            worst[b] = min(worst.get(b, 99), bars)
    return Verdict("TIME-11", "FAIL" if fails else "PASS", "critical",
                   {"violations": len(fails), "shortest_dwell_bars": {k: round(v, 2) for k, v in worst.items()}},
                   fails[:12])


@rule("TIME-02", "critical")
def time_02_look_hold(run: Run) -> Verdict:
    """A committed look may not change more often than min_hold_bars (8; 16 for BREAK/OUTRO).
    A look is measured within its section: the opening look and a look that hands over on a labeled
    section change are not scored (D5)."""
    fails = []
    rs = run.runs(run.scene)
    for k, (sc, s, e) in enumerate(rs):
        if k == 0 or k == len(rs) - 1:
            continue  # D5
        bucket = run.bucket[s]
        need = LOOK_MIN_HOLD_BARS.get(bucket, 8)
        bars = run.bars_in(s, e)
        if bars < need - 0.25 and not run.ends_on_section_change(e):
            fails.append((round(float(run.t[s]), 2), f"'{sc}' held {bars:.1f} bars in {bucket}, needs {need}"))
    return Verdict("TIME-02", "FAIL" if fails else "PASS", "critical",
                   {"violations": len(fails), "look_changes": max(0, len(rs) - 1)}, fails[:12])


@rule("TIME-13", "critical")
def time_13_legal_transitions(run: Run) -> Verdict:
    fails = []
    rs = run.runs(run.bucket)
    for (a, _, e), (b, s, _) in zip(rs, rs[1:]):
        if b not in LEGAL.get(a, set()):
            fails.append((round(float(run.t[s]), 2), f"{a} -> {b} is not a legal transition"))
    return Verdict("TIME-13", "FAIL" if fails else "PASS", "critical",
                   {"illegal": len(fails), "transitions": max(0, len(rs) - 1)}, fails[:12])


@rule("TIME-04", "critical")
def time_04_predrop_snap(run: Run) -> Verdict:
    """Dark on the final beat before a labeled drop, full on its downbeat, as a hard snap."""
    if (nv := _needs_markers(run, "TIME-04", "critical", "drop markers")):
        return nv
    np = run.np
    drops = run.markers("drop")
    if not drops:
        return Verdict("TIME-04", "NOT_SCORED", "critical", note="no drop markers")
    total = run.dimmer.sum(axis=1) / max(1, run.has_dimmer.sum())
    fails, ev = [], []
    for m in drops:
        pre = run.idx(m.t - run.beat_dur, m.t)
        post = run.idx(m.t, m.t + run.beat_dur)
        if len(pre) == 0 or len(post) == 0:
            continue
        dark = float(total[pre].mean())
        lit = float(total[post].max())
        # snap: from <15% to >=60% within 3 ticks (~70 ms) somewhere in [-1 beat, +1 beat]
        w = run.idx(m.t - run.beat_dur, m.t + run.beat_dur)
        snap = False
        for i in range(len(w) - 3):
            if total[w[i]] < 0.15 and total[w[i + 3]] >= 0.60:
                snap = True
                break
        ok = dark < 0.15 and lit >= 0.60 and snap
        (ev if ok else fails).append((round(m.t, 2), f"pre-beat mean {dark:.2f}, post max {lit:.2f}, snap={snap}"))
    return Verdict("TIME-04", "FAIL" if fails else "PASS", "critical",
                   {"drops": len(drops), "failed": len(fails)}, fails or ev)


def _build_window(run: Run, drop: Marker):
    """The build is the BUILD_BARS before a drop, cut short by any earlier marker."""
    start = max(drop.t - BUILD_BARS * run.bar_dur,
                max([m.t for m in run.tl.markers if m.t < drop.t - 1e-6], default=0.0))
    return start, drop.t


@rule("TIME-05", "major")
def time_05_build_ramp(run: Run) -> Verdict:
    """Over a build, dimmer ramps up and the dark-fixture fraction ramps down."""
    if (nv := _needs_markers(run, "TIME-05", "major", "drop markers")):
        return nv
    np = run.np
    fails, ev = [], []
    total = run.dimmer.sum(axis=1) / max(1, run.has_dimmer.sum())
    dark_frac = 1 - run.active.sum(axis=1) / max(1, run.has_dimmer.sum())
    for m in run.markers("drop"):
        s, e = _build_window(run, m)
        w = run.idx(s, e - run.beat_dur)   # exclude the pre-drop blackout beat
        if len(w) < 40:
            continue
        q = len(w) // 4
        d0, d1 = float(total[w[:q]].mean()), float(total[w[-q:]].mean())
        k0, k1 = float(dark_frac[w[:q]].mean()), float(dark_frac[w[-q:]].mean())
        ok = d1 > d0 + 0.05 and k1 <= k0 + 1e-6
        (ev if ok else fails).append((round(s, 2), f"build into {m.t:.1f}s: dimmer {d0:.2f}->{d1:.2f}, dark {k0:.2f}->{k1:.2f}"))
    if not fails and not ev:
        return Verdict("TIME-05", "NOT_SCORED", "major", note="no build window long enough")
    return Verdict("TIME-05", "FAIL" if fails else "PASS", "major", {"builds": len(fails) + len(ev), "failed": len(fails)}, fails or ev)


@rule("LOOK-05", "major")
def look_05_build_holds_back(run: Run) -> Verdict:
    """A build holds back strobe and laser until the drop."""
    if (nv := _needs_markers(run, "LOOK-05", "major", "drop markers")):
        return nv
    fails, ev = [], []
    for m in run.markers("drop"):
        s, e = _build_window(run, m)
        w = run.idx(s, e)
        if len(w) == 0:
            continue
        st = float(run.strobe_any[w].mean() * (e - s))
        la = float(run.laser_any[w].mean() * (e - s))
        ok = st < 0.1 and la < 0.1
        (ev if ok else fails).append((round(s, 2), f"build into {m.t:.1f}s: strobe {st:.1f}s, laser {la:.1f}s"))
    if not fails and not ev:
        return Verdict("LOOK-05", "NOT_SCORED", "major", note="no drops")
    return Verdict("LOOK-05", "FAIL" if fails else "PASS", "major", {"builds": len(fails) + len(ev), "failed": len(fails)}, fails or ev)


@rule("TIME-06", "major")
def time_06_breakdown(run: Run) -> Verdict:
    """Breakdown: <=2 fixtures, static hue, 50-70% dark, no strobe, no fast motion."""
    if (nv := _needs_markers(run, "TIME-06", "major", "breakdown markers")):
        return nv
    np = run.np
    fails, ev = [], []
    for m in run.markers("breakdown"):
        e = run.next_marker_t(m.t)
        w = run.idx(m.t + run.bar_dur, e)   # skip the first bar (transition)
        if len(w) < 40:
            continue
        n_active = float(run.active[w].sum(axis=1).mean())
        strobe_s = float(run.strobe_any[w].sum() * run.dt)
        hue_std, motion = 0.0, 0.0
        for j, fx in enumerate(run.fixtures):
            c = run.color[j]
            if c is not None and c.shape[1] >= 3 and run.active[w, j].mean() > 0.2:
                hue_std = max(hue_std, float(c[w].std(axis=0).max()))
            mv = run.move[j]
            if mv is not None and mv.shape[1] and len(w) > 1:
                motion = max(motion, float(np.abs(np.diff(mv[w], axis=0)).max()))
        probs = []
        if n_active > 2.0:
            probs.append(f"{n_active:.1f} fixtures lit (max 2)")
        if strobe_s > 0.1:
            probs.append(f"strobe {strobe_s:.1f}s")
        if hue_std > 0.08:
            probs.append(f"hue moves (std {hue_std:.2f})")
        if motion > 0.02:
            probs.append(f"fast motion ({motion*255:.0f} DMX/frame)")
        (fails if probs else ev).append((round(m.t, 2), "; ".join(probs) or f"{n_active:.1f} lit, static, no strobe"))
    if not fails and not ev:
        return Verdict("TIME-06", "NOT_SCORED", "major", note="no breakdown window long enough")
    return Verdict("TIME-06", "FAIL" if fails else "PASS", "major", {"breakdowns": len(fails) + len(ev), "failed": len(fails)}, fails or ev)


@rule("TIME-21", "major")
def time_21_restraint(run: Run) -> Verdict:
    """Not every fixture in every look."""
    rs = [r for r in run.runs(run.scene) if run.bars_in(r[1], r[2]) >= 2]
    if not rs:
        return Verdict("TIME-21", "NOT_SCORED", "major", note="no look held 2+ bars")
    nd = max(1, int(run.has_dimmer.sum()))
    full = [(round(float(run.t[s]), 2), sc) for sc, s, e in rs if run.active[s:e].sum(axis=1).mean() >= nd - 0.1]
    return Verdict("TIME-21", "FAIL" if len(full) == len(rs) else "PASS", "major",
                   {"looks": len(rs), "all_fixtures_lit": len(full)}, full[:8])


@rule("LOOK-03", "critical")
def look_03_static_cut(run: Run) -> Verdict:
    """A 'the cut IS the effect' drop look renders fully static."""
    np = run.np
    rs = [r for r in run.runs(run.scene) if any(tag in r[0] for tag in STATIC_CUT_LOOKS)]
    if not rs:
        return Verdict("LOOK-03", "NOT_SCORED", "critical", note="no static-cut look was selected in this run")
    fails, ev = [], []
    for sc, s, e in rs:
        if e - s < 6:
            continue
        seg = run.frames[s + 2:e - 1].astype(int)
        changing = int((seg.max(axis=0) != seg.min(axis=0)).sum())
        (fails if changing else ev).append((round(float(run.t[s]), 2), f"'{sc}': {changing} channels moved"))
    return Verdict("LOOK-03", "FAIL" if fails else "PASS", "critical", {"looks": len(rs), "failed": len(fails)}, fails or ev)


def _strobe_bursts(run: Run):
    return [(s, e) for v, s, e in run.runs(run.strobe_any) if v]


@rule("SAFE-04", "critical")
def safe_04_burst_timing(run: Run) -> Verdict:
    """Burst <= 1.5 s, gap >= 5.0 s (the Hz part is SAFE-01..03)."""
    bursts = _strobe_bursts(run)
    fails = []
    longest, shortest_gap = 0.0, None
    for k, (s, e) in enumerate(bursts):
        dur = float(run.t[e - 1] - run.t[s] + run.dt)
        longest = max(longest, dur)
        if dur > 1.5 + run.dt:
            fails.append((round(float(run.t[s]), 2), f"burst {dur:.2f}s > 1.5s"))
        if k:
            gap = float(run.t[s] - run.t[bursts[k - 1][1] - 1])
            shortest_gap = gap if shortest_gap is None else min(shortest_gap, gap)
            if gap < 5.0:
                fails.append((round(float(run.t[s]), 2), f"gap {gap:.2f}s < 5.0s"))
    return Verdict("SAFE-04", "FAIL" if fails else "PASS", "critical",
                   {"bursts": len(bursts), "longest_s": round(longest, 2),
                    "shortest_gap_s": round(shortest_gap, 2) if shortest_gap is not None else None},
                   fails[:12])


@rule("SAFE-09", "critical")
def safe_09_full_field(run: Run) -> Verdict:
    """Never full-field AND >4 Hz. Hz is unmeasurable; full-field seconds is the proxy."""
    ns = int(run.has_strobe.sum())
    ff = ((run.strobe > 0).sum(axis=1) >= ns) if ns else run.np.zeros(run.n, bool)
    secs = float(ff.sum() * run.dt)
    return Verdict("SAFE-09", "UNVERIFIABLE_IN_SIM", "critical",
                   {"full_field_strobe_s": round(secs, 2), "strobe_fixtures": ns,
                    "max_strobe_byte": int(run.strobe.max() * 255)},
                   note="rate (Hz) lives in fixture firmware; only the full-field half is measured")


for _rid in ("SAFE-01", "SAFE-02", "SAFE-03", "SAFE-05", "SAFE-06"):
    def _mk(rid):
        def fn(run: Run) -> Verdict:
            return Verdict(rid, "UNVERIFIABLE_IN_SIM", "critical",
                           {"max_strobe_byte": int(run.strobe.max() * 255)},
                           note="strobe Hz is set by fixture firmware from one byte; needs physical calibration")
        return fn
    RULES.append((_rid, "critical", _mk(_rid)))


@rule("STRUCT-01", "critical")
def struct_01_fills_no_strobe(run: Run) -> Verdict:
    """A fill (energy entry that is not a drop) never strobes or uses a DROP/PEAK look."""
    if (nv := _needs_markers(run, "STRUCT-01", "critical", "fill markers")):
        return nv
    fills = run.markers("fill")
    if not fills:
        return Verdict("STRUCT-01", "NOT_SCORED", "critical", note="no fill markers")
    fails, ev = [], []
    for m in fills:
        e = min(m.t + FILL_BARS * run.bar_dur, run.next_marker_t(m.t))
        w = run.idx(m.t, e)
        if len(w) == 0:
            continue
        st = float(run.strobe_any[w].sum() * run.dt)
        dropish = float(run.np.isin(run.bucket[w], DROP_FAMILY).mean())
        looks = sorted({sc for sc in run.scene[w] if sc.startswith(("Drop", "Peak"))})
        probs = []
        if st > 0.05:
            probs.append(f"strobe {st:.1f}s")
        if dropish > 0.1:
            probs.append(f"DROP/PEAK bucket {dropish*100:.0f}% of the window")
        if looks:
            probs.append("looks: " + ", ".join(looks))
        (fails if probs else ev).append((round(m.t, 2), "; ".join(probs) or "clean"))
    return Verdict("STRUCT-01", "FAIL" if fails else "PASS", "critical", {"fills": len(fills), "failed": len(fails)}, fails or ev)


@rule("STRUCT-02", "major")
def struct_02_escalation(run: Run) -> Verdict:
    """A drop marked as escalating builds intensity across the drop rather than slamming."""
    if (nv := _needs_markers(run, "STRUCT-02", "major", "drop markers")):
        return nv
    drops = [m for m in run.markers("drop") if "escalat" in m.note.lower()]
    if not drops:
        return Verdict("STRUCT-02", "NOT_SCORED", "major", note="no drop marked as escalating")
    total = run.dimmer.sum(axis=1) / max(1, run.has_dimmer.sum()) + run.strobe.max(axis=1)
    fails, ev = [], []
    for m in drops:
        e = m.end or run.next_marker_t(m.t)
        w = run.idx(m.t, e)
        if len(w) < 40:
            continue
        q = len(w) // 4
        a, b = float(total[w[:q]].mean()), float(total[w[-q:]].mean())
        ok = b > a * 1.15
        (ev if ok else fails).append((round(m.t, 2), f"intensity first quarter {a:.2f} -> last quarter {b:.2f}"))
    return Verdict("STRUCT-02", "FAIL" if fails else "PASS", "major", {"drops": len(drops), "failed": len(fails)}, fails or ev)


@rule("STRUCT-03", "major")
def struct_03_drop_latency(run: Run) -> Verdict:
    """How long after a labeled drop the engine itself enters DROP/PEAK."""
    if (nv := _needs_markers(run, "STRUCT-03", "major", "drop markers")):
        return nv
    drops = run.markers("drop")
    if not drops:
        return Verdict("STRUCT-03", "NOT_SCORED", "major", note="no drop markers")
    np = run.np
    lat, fails, ev = [], [], []
    for m in drops:
        e = m.end or run.next_marker_t(m.t)
        w = run.idx(m.t - run.beat_dur, e)
        hit = [i for i in w if run.bucket[i] in DROP_FAMILY]
        if not hit:
            fails.append((round(m.t, 2), "never entered DROP/PEAK during the drop"))
            continue
        beats = float((run.t[hit[0]] - m.t) / run.beat_dur)
        lat.append(beats)
        (fails if beats > DROP_LATENCY_MAX_BEATS else ev).append((round(m.t, 2), f"detected {beats:+.2f} beats after the drop"))
    metric = {"drops": len(drops), "latency_beats": [round(x, 2) for x in lat],
              "mean_latency_beats": round(float(np.mean(lat)), 2) if lat else None, "missed": len(drops) - len(lat)}
    return Verdict("STRUCT-03", "FAIL" if fails else "PASS", "major", metric, fails or ev)


# ----------------------------------------------------------------- scorer v2 (Josh's roll / cut / groove vocabulary)

def _corr(np, a, b) -> float:
    if len(a) < 3 or len(a) != len(b) or a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _whiteness(c):
    """(white[n], chroma[n]) for a wash fixture's COLOR columns (R,G,B[,W])."""
    rgb = c[:, :3]
    chroma = rgb.max(axis=1) - rgb.min(axis=1)
    white = (rgb.min(axis=1) >= WHITE_LEVEL) & (chroma <= WHITE_CHROMA)
    if c.shape[1] >= 4:
        white = white | (c[:, 3] >= WHITE_LEVEL)   # a dedicated white emitter counts on its own
    return white, chroma


def _bool_runs(arr):
    out, s = [], None
    for i, v in enumerate(arr):
        if v and s is None:
            s = i
        elif not v and s is not None:
            out.append((s, i))
            s = None
    if s is not None:
        out.append((s, len(arr)))
    return out


def _is_clap_drop(m: Marker) -> bool:
    n = m.note.lower()
    return any(w in n for w in CLAP_WORDS)


@rule("STRUCT-04", "major")
def struct_04_whiteout(run: Run) -> Verdict:
    """Whiteout (the cut): drums drop out, track stays intense -> the rig goes
    white and the moving heads turn to the DJ.

    Window: from a ``whiteout`` marker + WHITEOUT_SETTLE_BEATS to the next
    marker, capped at WHITEOUT_MAX_BARS. Three sub-checks, FAIL if any is off:
    (a) every wash fixture (role "wash" with RGB[W] colour) is white
        (W >= WHITE_LEVEL, or R~G~B: min >= WHITE_LEVEL and spread <= WHITE_CHROMA)
        with dimmer >= WHITE_DIMMER for >= WHITE_FRACTION of the window;
    (b) no strobe byte anywhere (> 2 % of the window strobing fails);
    (c) each moving head with a known DJ pose (rig ``dj_pan_tilt`` or a
        calibrated ``dj_booth`` aim, see Rig.dj_pose) ends within DJ_POSE_TOL
        of it, or has closed DJ_APPROACH_FRACTION of its starting distance.
        No DJ pose configured -> (c) is NOT_SCORED (reported in the metric),
        never guessed.
    Tolerances are first guesses (2026-10-21)."""
    if (nv := _needs_markers(run, "STRUCT-04", "major", "whiteout markers")):
        return nv
    ws = run.markers("whiteout")
    if not ws:
        return Verdict("STRUCT-04", "NOT_SCORED", "major", note="no whiteout markers")
    np = run.np
    wash = [j for j in run.wash if run.color[j] is not None and run.color[j].shape[1] >= 3]
    posed = [j for j in run.heads if run.dj_pose[j] is not None]
    fails, ev = [], []
    for m in ws:
        e = min(run.next_marker_t(m.t), m.t + WHITEOUT_MAX_BARS * run.bar_dur)
        w = run.idx(m.t + WHITEOUT_SETTLE_BEATS * run.beat_dur, e)
        if len(w) < 10:
            continue
        probs = []
        worst, worst_slot = 1.0, None
        for j in wash:
            white, _ = _whiteness(run.color[j][w])
            frac = float((white & (run.dimmer[w, j] >= WHITE_DIMMER)).mean())
            if frac < worst:
                worst, worst_slot = frac, run.slots[j]
        if wash and worst < WHITE_FRACTION:
            probs.append(f"{worst_slot} white+lit only {worst * 100:.0f}% of the window")
        st = float(run.strobe_any[w].mean())
        if st > 0.02:
            probs.append(f"strobe on {st * 100:.0f}% of the window")
        for j in posed:
            pan, tilt = run.dj_pose[j]
            mv = run.move[j][w]
            d = np.hypot(mv[:, 0] - pan, mv[:, 1] - tilt)
            q = max(1, len(d) // 4)
            d0, d1 = float(d[:q].mean()), float(d[-q:].mean())
            if not (d1 <= DJ_POSE_TOL or d1 <= d0 * (1 - DJ_APPROACH_FRACTION)):
                probs.append(f"{run.slots[j]} ends {d1:.3f} from the DJ pose (started {d0:.3f})")
        ok_note = "white, no strobe" + (", heads to DJ" if posed else "")
        (fails if probs else ev).append((round(m.t, 2), "; ".join(probs) or ok_note))
    if not fails and not ev:
        return Verdict("STRUCT-04", "NOT_SCORED", "major", note="no whiteout window long enough")
    metric = {"whiteouts": len(fails) + len(ev), "failed": len(fails),
              "dj_pose": "configured" if posed else "not configured -> head-to-DJ sub-check NOT_SCORED"}
    note = "" if posed or not run.heads else "rig has no dj_pan_tilt / calibrated dj_booth: heads-to-DJ not scored"
    return Verdict("STRUCT-04", "FAIL" if fails else "PASS", "major", metric, fails or ev, note)


@rule("STRUCT-07", "major")
def struct_07_groove(run: Run) -> Verdict:
    """Groove: the main full-energy section after the cut. Over each ``groove``
    window (>= GROOVE_MIN_BARS) three sub-metrics, FAIL if any is clearly off:
    (a) synth-synced wash bar: corr(bar dimmer, band_mid or band_high) >=
        GROOVE_CORR_MIN and >= corr(bar dimmer, band_low) + GROOVE_CORR_MARGIN;
    (b) heads at full speed: on some head, >= GROOVE_HEAD_MOVING_FRACTION of
        frames move an axis at >= GROOVE_HEAD_SPEED_FRACTION of its slew clamp
        (profiles.py PAN/TILT_MAX_DEG_PER_S over the range, per tick);
    (c) laser bursts: in windows >= GROOVE_LASER_MIN_BARS, >= 2 bursts whose
        median length is within GROOVE_BURST_BEATS and median start-to-start
        within GROOVE_PERIOD_BEATS ("2-4 beats every other bar").
    A sub-check whose hardware the rig lacks is reported as n/a, not failed.
    Tolerances are first guesses (2026-10-21)."""
    if (nv := _needs_markers(run, "STRUCT-07", "major", "groove markers")):
        return nv
    grooves = run.markers("groove")
    if not grooves:
        return Verdict("STRUCT-07", "NOT_SCORED", "major", note="no groove markers")
    np = run.np
    fails, ev, subs = [], [], []
    for m in grooves:
        e = m.end or run.next_marker_t(m.t)
        w = run.idx(m.t, e)
        bars = (e - m.t) / run.bar_dur
        if bars < GROOVE_MIN_BARS or len(w) < 20:
            continue
        probs, sub = [], {"t": round(m.t, 2), "bars": round(bars, 1)}
        # (a) synth-synced bar
        if run.wash_bar:
            bar = run.dimmer[w][:, run.wash_bar].mean(axis=1)
            synth = max(_corr(np, bar, run.band_mid[w]), _corr(np, bar, run.band_high[w]))
            kick = _corr(np, bar, run.band_low[w])
            sub["bar_corr_synth"], sub["bar_corr_kick"] = round(synth, 2), round(kick, 2)
            if not (synth >= GROOVE_CORR_MIN and synth >= kick + GROOVE_CORR_MARGIN):
                probs.append(f"bar not synth-synced (r synth {synth:.2f}, kick {kick:.2f})")
        else:
            sub["bar_corr_synth"] = "n/a"
        # (b) heads at full speed
        best = None
        for j in run.heads:
            cl = run.head_clamp(j)
            if cl is None:
                continue
            dd = np.abs(np.diff(run.move[j][w], axis=0))
            ratio = np.maximum(dd[:, 0] / cl[0], dd[:, 1] / cl[1])
            frac = float((ratio >= GROOVE_HEAD_SPEED_FRACTION).mean()) if len(ratio) else 0.0
            best = frac if best is None else max(best, frac)
        if best is None:
            sub["head_frames_at_speed"] = "n/a"
        else:
            sub["head_frames_at_speed"] = round(best, 2)
            if best < GROOVE_HEAD_MOVING_FRACTION:
                probs.append(f"heads at speed only {best * 100:.0f}% of frames")
        # (c) laser cadence
        if not run.rig.by_role("laser"):
            sub["laser_bursts"] = "n/a"
        elif bars >= GROOVE_LASER_MIN_BARS:
            bursts = _bool_runs(run.laser_any[w])
            tw = run.t[w]
            durs = [float((tw[b - 1] - tw[a] + run.dt) / run.beat_dur) for a, b in bursts]
            periods = [float((tw[bursts[k + 1][0]] - tw[bursts[k][0]]) / run.beat_dur) for k in range(len(bursts) - 1)]
            sub["laser_bursts"] = len(bursts)
            sub["laser_burst_beats_median"] = round(float(np.median(durs)), 2) if durs else None
            sub["laser_period_beats_median"] = round(float(np.median(periods)), 2) if periods else None
            if len(bursts) < 2:
                probs.append(f"laser: {len(bursts)} burst(s) in {bars:.0f} bars"
                             + (f" ({durs[0]:.1f} beats long)" if durs else ""))
            else:
                md, mp = float(np.median(durs)), float(np.median(periods))
                if not (GROOVE_BURST_BEATS[0] <= md <= GROOVE_BURST_BEATS[1]):
                    probs.append(f"laser bursts {md:.1f} beats long (want 2-4)")
                if not (GROOVE_PERIOD_BEATS[0] <= mp <= GROOVE_PERIOD_BEATS[1]):
                    probs.append(f"laser bursts every {mp:.1f} beats (want ~every other bar)")
        subs.append(sub)
        (fails if probs else ev).append((round(m.t, 2), "; ".join(probs) or "synth-synced bar, heads at speed, laser cadence ok"))
    if not fails and not ev:
        return Verdict("STRUCT-07", "NOT_SCORED", "major", note="no groove window long enough")
    return Verdict("STRUCT-07", "FAIL" if fails else "PASS", "major",
                   {"grooves": len(fails) + len(ev), "failed": len(fails), "sub": subs}, fails or ev)


@rule("STRUCT-08", "minor")
def struct_08_weak_roll(run: Run) -> Verdict:
    """Strobe ceiling scales with the roll: a weaker snare roll gets a visibly
    lower peak strobe byte than a stronger one. Needs >= 2 roll drops (clap-
    driven drops, STRUCT-09, are excluded). Roll strength comes from Josh's
    note when he wrote it ("weak" in the note = the weaker roll), else from the
    mean high-band energy over the drop window; two rolls within
    ROLL_ENERGY_RATIO of each other are not ordered. Every clearly-stronger
    roll must peak >= ROLL_STROBE_VISIBLE_STEP bytes above every clearly-weaker
    one (two rolls that never strobe are "equal", so they fail too).
    Tolerances are first guesses (2026-10-21)."""
    if (nv := _needs_markers(run, "STRUCT-08", "minor", "drop markers")):
        return nv
    drops = [m for m in run.markers("drop") if not _is_clap_drop(m)]
    if len(drops) < 2:
        return Verdict("STRUCT-08", "NOT_SCORED", "minor", note="needs >= 2 roll drops to order")
    rows = []
    for m in drops:
        e = m.end or run.next_marker_t(m.t)
        w = run.idx(m.t, e)
        if len(w) < 10:
            continue
        rows.append({"t": round(m.t, 2), "weak_note": "weak" in m.note.lower(),
                     "high_band": float(run.band_high[w].mean()),
                     "onset_rate": round(float(run.onset_rate[w].mean()), 1),
                     "peak_strobe": int(round(float(run.strobe[w].max()) * 255))})
    if len(rows) < 2:
        return Verdict("STRUCT-08", "NOT_SCORED", "minor", note="fewer than 2 drop windows long enough")
    by_note = any(r["weak_note"] for r in rows)

    def stronger(a, b) -> bool:
        if by_note:
            return (not a["weak_note"]) and b["weak_note"]
        return a["high_band"] >= b["high_band"] * ROLL_ENERGY_RATIO and b["high_band"] > 0

    fails, ev, pairs = [], [], 0
    for a in rows:
        for b in rows:
            if a is b or not stronger(a, b):
                continue
            pairs += 1
            gap = a["peak_strobe"] - b["peak_strobe"]
            line = (f"roll {a['t']}s (peak {a['peak_strobe']}) vs weaker roll {b['t']}s (peak {b['peak_strobe']})")
            (ev if gap >= ROLL_STROBE_VISIBLE_STEP else fails).append((b["t"], line))
    metric = {"rolls": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in rows],
              "ordered_by": "note" if by_note else "high_band", "pairs": pairs}
    if pairs == 0:
        return Verdict("STRUCT-08", "NOT_SCORED", "minor", metric,
                       note="rolls of similar energy and no 'weak' note: nothing to order")
    return Verdict("STRUCT-08", "FAIL" if fails else "PASS", "minor", metric, fails or ev)


@rule("STRUCT-09", "major")
def struct_09_clap_driven_drop(run: Run) -> Verdict:
    """Clap-driven drop (SWEETER): no roll, synths + claps/crashes carry it ->
    hits on the claps, a soft slow strobe at most, never the roll strobe.
    Applies to ``drop`` markers whose note contains one of CLAP_WORDS. Over the
    drop window, FAIL on any of:
    (a) peak strobe byte > CLAP_STROBE_CEILING;
    (b) roll behaviour: strobe rising across the window (last quarter > first
        quarter x 1.15 + 0.02 and above 10 %), or ramping inside any burst
        >= 1 beat long (last third > first third x 1.5 + 10 bytes);
    (c) hits not on the claps: corr(up-flux of the rig's mean dimmer, up-flux
        of band_high), best lag 0..CLAP_ONSET_MAX_LAG_TICKS, < CLAP_ONSET_CORR_MIN
        (a flat dimmer reads 0: no hits at all).
    Tolerances are first guesses (2026-10-21)."""
    if (nv := _needs_markers(run, "STRUCT-09", "major", "drop markers")):
        return nv
    drops = [m for m in run.markers("drop") if _is_clap_drop(m)]
    if not drops:
        return Verdict("STRUCT-09", "NOT_SCORED", "major", note="no drop noted as clap/crash-driven")
    np = run.np
    fails, ev, subs = [], [], []
    for m in drops:
        e = m.end or run.next_marker_t(m.t)
        w = run.idx(m.t, e)
        if len(w) < 40:
            continue
        probs = []
        peak = int(round(float(run.strobe[w].max()) * 255))
        if peak > CLAP_STROBE_CEILING:
            probs.append(f"strobe peak {peak} > soft ceiling {CLAP_STROBE_CEILING}")
        st = run.strobe[w].max(axis=1)
        q = len(w) // 4
        a, b = float(st[:q].mean()), float(st[-q:].mean())
        if b > 0.10 and b > a * 1.15 + 0.02:
            probs.append(f"strobe escalates like a roll ({a:.2f} -> {b:.2f})")
        min_len = max(3, int(run.beat_dur / run.dt))
        for s0, s1 in _bool_runs(st > 0):
            if s1 - s0 < min_len:
                continue
            third = max(1, (s1 - s0) // 3)
            lo, hi = float(st[s0:s0 + third].mean()) * 255, float(st[s1 - third:s1].mean()) * 255
            if hi > lo * 1.5 + 10:
                probs.append(f"strobe burst at +{run.t[w[s0]] - m.t:.1f}s ramps {lo:.0f} -> {hi:.0f} like a roll")
                break
        lf = np.maximum(0.0, np.diff(run.total[w]))
        af = np.maximum(0.0, np.diff(run.band_high[w]))
        best = max(_corr(np, lf[k:], af[:len(af) - k]) for k in range(0, CLAP_ONSET_MAX_LAG_TICKS + 1))
        if best < CLAP_ONSET_CORR_MIN:
            probs.append(f"dimmer hits don't follow the claps (r={best:.2f})")
        subs.append({"t": round(m.t, 2), "peak_strobe": peak, "strobe_q1": round(a, 2), "strobe_q4": round(b, 2),
                     "clap_hit_corr": round(best, 2)})
        (fails if probs else ev).append((round(m.t, 2), "; ".join(probs) or f"soft strobe (peak {peak}), hits on claps (r={best:.2f})"))
    if not fails and not ev:
        return Verdict("STRUCT-09", "NOT_SCORED", "major", note="no clap-driven drop window long enough")
    return Verdict("STRUCT-09", "FAIL" if fails else "PASS", "major",
                   {"drops": len(fails) + len(ev), "failed": len(fails), "sub": subs}, fails or ev)


def _step_edges(np, x, step_min: float, hold_ticks: int) -> list[int]:
    """Indices where ``x`` falls by >= step_min within 3 ticks, merged into one
    edge per group closer than hold_ticks. A smooth ramp has no (or one) edge;
    a hard cut has one; a shutter fade has several."""
    if len(x) < 4:
        return []
    fall = x[:-3] - x[3:]
    idx = np.where(fall >= step_min)[0]
    starts, prev = [], None
    for i in idx:
        if prev is None or i - prev > hold_ticks:
            starts.append(int(i))
        prev = i
    return starts


@rule("STRUCT-05", "major")
def struct_05_power_off(run: Run) -> Verdict:
    """Power-off shutter fade: leaving an intense scene NOT via a beat drop
    (Beautiful Now 1:04) reads as a stepped dim-to-dark, not a smooth fade or
    a hard cut. Candidates: ``breakdown`` markers that are not a drop's cut
    (no drop ``end`` within a beat; previous marker not an open-ended drop)
    where some fixture averaged >= POWEROFF_PRIOR_LEVEL over the bar before.
    On the lit-fixture mean dimmer (analysed from one beat before the marker,
    so an edge on the marker tick itself counts): the first step edge must
    land within POWEROFF_START_BARS of the marker, the rig must reach <= POWEROFF_DARK
    within POWEROFF_MAX_BARS of that edge, the number of edges (falls >=
    POWEROFF_STEP_MIN within 3 ticks, >= POWEROFF_HOLD_TICKS apart) must be
    within POWEROFF_STEPS, and no strobe byte during the fade.
    Placeholders from PENDING_RULES ("3-6 steps over 1-2 bars"); first guesses
    (2026-10-21)."""
    if (nv := _needs_markers(run, "STRUCT-05", "major", "breakdown markers")):
        return nv
    bds = run.markers("breakdown")
    if not bds:
        return Verdict("STRUCT-05", "NOT_SCORED", "major", note="no breakdown markers")
    np = run.np
    drops = run.markers("drop")
    fails, ev, skipped = [], [], []
    for m in bds:
        if any(d.end is not None and abs(d.end - m.t) <= run.beat_dur for d in drops):
            skipped.append((round(m.t, 2), "a drop's cut, not a power-off"))
            continue
        prev = run.prev_marker(m.t)
        if prev is not None and prev.kind == "drop" and prev.end is None:
            skipped.append((round(m.t, 2), "ends an open drop, not a power-off"))
            continue
        pre = run.idx(m.t - run.bar_dur, m.t)
        if len(pre) == 0 or float(run.dimmer[pre].mean(axis=0).max()) < POWEROFF_PRIOR_LEVEL:
            skipped.append((round(m.t, 2), "rig already dim before the breakdown"))
            continue
        w = run.idx(m.t - run.beat_dur, m.t + (POWEROFF_START_BARS + POWEROFF_MAX_BARS) * run.bar_dur)
        if len(w) < 20:
            continue
        x, _lit = run.lit_mean(w, pre)
        edges = _step_edges(np, x, POWEROFF_STEP_MIN, POWEROFF_HOLD_TICKS)
        probs = []
        start_lim = int((POWEROFF_START_BARS * run.bar_dur + run.beat_dur) / run.dt)
        if not edges or edges[0] > start_lim:
            dark_i = np.where(x <= POWEROFF_DARK)[0]
            how = (f"faded smoothly to dark by +{run.t[w[dark_i[0]]] - m.t:.1f}s" if len(dark_i)
                   else f"stayed lit (lit-fixture mean {x.mean():.2f})")
            probs.append(f"no stepped fade within {POWEROFF_START_BARS} bars: {how}")
            fade = None
        else:
            e0 = edges[0]
            seg = x[e0:]
            dark_i = np.where(seg <= POWEROFF_DARK)[0]
            if not len(dark_i):
                probs.append(f"never reached dark within {POWEROFF_MAX_BARS} bars of the first step")
                fade = (e0, len(x))
            else:
                fade = (e0, e0 + int(dark_i[0]) + 1)
                dur_bars = (fade[1] - fade[0]) * run.dt / run.bar_dur
                if dur_bars > POWEROFF_MAX_BARS:
                    probs.append(f"fade took {dur_bars:.1f} bars (max {POWEROFF_MAX_BARS})")
            n_steps = len([k for k in edges if fade[0] <= k < fade[1]])
            if not (POWEROFF_STEPS[0] <= n_steps <= POWEROFF_STEPS[1]):
                probs.append(f"{n_steps} step(s), want {POWEROFF_STEPS[0]}-{POWEROFF_STEPS[1]}"
                             + (" (hard cut)" if n_steps == 1 else ""))
            if run.strobe_any[w[fade[0]:fade[1]]].any():
                probs.append("strobe during the fade")
        (fails if probs else ev).append((round(m.t, 2), "; ".join(probs) or f"{len(edges)} steps to dark"))
    if not fails and not ev:
        return Verdict("STRUCT-05", "NOT_SCORED", "major", {"skipped": skipped},
                       note="no breakdown qualifies as a power-off (all were drop cuts or already dim)")
    return Verdict("STRUCT-05", "FAIL" if fails else "PASS", "major",
                   {"candidates": len(fails) + len(ev), "failed": len(fails), "skipped": skipped}, fails or ev)


@rule("STRUCT-06", "major")
def struct_06_fading_build(run: Run) -> Verdict:
    """Extended build that fades out instead of dropping (Beautiful Now
    0:30 -> 1:04): white, slow smooth dim, dramatic -- no strobe, minimal
    head movement. Windows: a ``build`` marker whose next marker is a
    ``breakdown``, or the BUILD_BARS before a ``breakdown`` whose note has one
    of FADE_WORDS (cut short by the previous marker). On each window, FAIL on:
    lit-fixture mean dimmer not falling by FADE_DIM_DROP (first vs last
    quarter); a per-tick jump > FADE_MAX_STEP or > FADE_MAX_RISE_FRACTION of
    ticks rising (not smooth); lit wash fixtures' chroma neither ending <=
    FADE_CHROMA_WHITE nor falling by FADE_CHROMA_DROP (hue not drifting to
    white); any strobe (> 2 % of ticks); head per-frame delta > FADE_MAX_MOTION.
    Tolerances are first guesses (2026-10-21)."""
    if (nv := _needs_markers(run, "STRUCT-06", "major", "build/breakdown markers")):
        return nv
    np = run.np
    windows: dict[float, float] = {}
    for m in run.markers("build"):
        nxt = run.next_marker(m.t)
        if nxt is not None and nxt.kind == "breakdown":
            windows[nxt.t] = m.t
    for m in run.markers("breakdown"):
        if any(wd in m.note.lower() for wd in FADE_WORDS) and m.t not in windows:
            prev = run.prev_marker(m.t)
            windows[m.t] = max(m.t - BUILD_BARS * run.bar_dur, prev.t if prev else 0.0)
    if not windows:
        return Verdict("STRUCT-06", "NOT_SCORED", "major",
                       note="no fading build (a build marker followed by a breakdown, or a breakdown noted as fading)")
    fails, ev = [], []
    for e, s in sorted(windows.items()):
        w = run.idx(s, e)
        if len(w) < 40:
            continue
        probs = []
        x, lit = run.lit_mean(w)
        q = len(w) // 4
        d0, d1 = float(x[:q].mean()), float(x[-q:].mean())
        if not lit:
            probs.append("rig dark through the build")
        elif d1 > d0 - FADE_DIM_DROP:
            probs.append(f"no slow dim ({d0:.2f} -> {d1:.2f})")
        dd = np.diff(x)
        if len(dd) and float(np.abs(dd).max()) > FADE_MAX_STEP:
            probs.append(f"jump of {float(np.abs(dd).max()):.2f} in one tick (not smooth)")
        rise = float((dd >= 0.01).mean()) if len(dd) else 0.0
        if rise > FADE_MAX_RISE_FRACTION:
            probs.append(f"not smooth: {rise * 100:.0f}% of ticks rise")
        c0s, c1s = [], []
        for j in run.wash:
            c = run.color[j]
            if c is None or c.shape[1] < 3 or run.active[w, j].mean() <= 0.2:
                continue
            _, chroma = _whiteness(c[w])
            c0s.append(float(chroma[:q].mean()))
            c1s.append(float(chroma[-q:].mean()))
        if c0s:
            c0, c1 = float(np.mean(c0s)), float(np.mean(c1s))
            if not (c1 <= FADE_CHROMA_WHITE or c1 <= c0 - FADE_CHROMA_DROP):
                probs.append(f"hue not drifting to white (chroma {c0:.2f} -> {c1:.2f})")
        if float(run.strobe_any[w].mean()) > 0.02:
            probs.append("strobe during the fading build")
        motion = max((float(np.abs(np.diff(run.move[j][w], axis=0)).max()) for j in run.heads if len(w) > 1), default=0.0)
        if motion > FADE_MAX_MOTION:
            probs.append(f"heads moving ({motion * 255:.1f} DMX/frame)")
        (fails if probs else ev).append((round(s, 2), "; ".join(probs) or f"white slow dim {d0:.2f} -> {d1:.2f} into {e:.1f}s"))
    if not fails and not ev:
        return Verdict("STRUCT-06", "NOT_SCORED", "major", note="no fading-build window long enough")
    return Verdict("STRUCT-06", "FAIL" if fails else "PASS", "major",
                   {"builds": len(fails) + len(ev), "failed": len(fails)}, fails or ev)


# ----------------------------------------------------------------- scoring

def show_quality(run: Run) -> dict:
    """Positive metrics an optimizer must not sacrifice. Reported, not gated."""
    np = run.np
    nd = max(1, int(run.has_dimmer.sum()))
    total = run.dimmer.sum(axis=1) / nd
    q = {
        "distinct_looks": int(len(set(run.scene.tolist()))),
        "look_changes": max(0, len(run.runs(run.scene)) - 1),
        "brightness_dynamic_range": round(float(np.percentile(total, 95) - np.percentile(total, 5)), 3),
        "fixture_utilization": round(float((run.active.mean(axis=0)[run.has_dimmer] > 0.05).mean()), 2) if nd else 0.0,
        "bucket_coverage": sorted(set(run.bucket.tolist())),
    }
    resp = []
    for m in run.markers("drop"):
        pre = run.idx(m.t - run.beat_dur, m.t)
        post = run.idx(m.t, m.t + run.beat_dur)
        if len(pre) and len(post):
            resp.append(float(total[post].max() - total[pre].mean()))
    q["drop_response_mean"] = round(float(np.mean(resp)), 3) if resp else None
    return q


def score_run(run: Run) -> dict:
    verdicts = [fn(run) for _, _, fn in RULES]
    scorable = [v for v in verdicts if v.status in ("PASS", "FAIL")]
    wt = sum(SEVERITY_WEIGHT[v.severity] for v in scorable)
    wf = sum(SEVERITY_WEIGHT[v.severity] for v in scorable if v.status == "FAIL")
    critical_fails = [v.rule for v in scorable if v.status == "FAIL" and v.severity == "critical"]
    return {
        "status": "BLOCKED" if critical_fails else "OK",
        "critical_fails": critical_fails,
        "compliance": round(1 - wf / wt, 3) if wt else None,
        "counts": {s: sum(1 for v in verdicts if v.status == s)
                   for s in ("PASS", "FAIL", "UNVERIFIABLE_IN_SIM", "NOT_SCORED")},
        "quality": show_quality(run),
        "verdicts": [asdict(v) for v in verdicts],
    }


def load_run(run_dir: str | Path, timeline: Timeline, rig_path: str | Path) -> Run:
    import numpy as np
    d = Path(run_dir)
    z = np.load(d / "frames.npz")
    with open(d / "signals.csv", encoding="utf-8") as fh:
        signals = list(csv.DictReader(fh))
    return Run(z["frames"], z["t"], signals, timeline, load_rig(str(rig_path)))


def scorecard_md(batch: list[tuple[str, dict]]) -> str:
    """One table: rules down, tracks across; P/F/U/- cells; quality rows beneath."""
    rules = [r for r, _, _ in RULES]
    names = [n for n, _ in batch]
    short = {"PASS": "P", "FAIL": "**F**", "UNVERIFIABLE_IN_SIM": "U", "NOT_SCORED": "-"}
    lines = ["# Scorecard", "", "P pass · **F** fail · U unverifiable in sim · - not scored", "",
             "| Rule | sev | " + " | ".join(n[:18] for n in names) + " |",
             "|---|---|" + "---|" * len(names)]
    for rid in rules:
        sev = next(s for r, s, _ in RULES if r == rid)
        cells = []
        for _, sc in batch:
            v = next(v for v in sc["verdicts"] if v["rule"] == rid)
            cells.append(short[v["status"]])
        lines.append(f"| {rid} | {sev[:4]} | " + " | ".join(cells) + " |")
    lines += ["", "| Metric | " + " | ".join(n[:18] for n in names) + " |", "|---|" + "---|" * len(names)]
    for key in ("status", "compliance"):
        lines.append(f"| {key} | " + " | ".join(str(sc[key]) for _, sc in batch) + " |")
    for key in ("distinct_looks", "look_changes", "brightness_dynamic_range", "fixture_utilization", "drop_response_mean"):
        lines.append(f"| {key} | " + " | ".join(str(sc["quality"][key]) for _, sc in batch) + " |")
    lines += ["", "## Failures", ""]
    for name, sc in batch:
        for v in sc["verdicts"]:
            if v["status"] == "FAIL":
                ev = "; ".join(f"{t}s {n}" for t, n in v["evidence"][:4])
                lines.append(f"- **{name[:30]}** {v['rule']} ({v['severity']}): {ev}")
    return "\n".join(lines) + "\n"
