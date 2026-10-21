"""The scorer: judge a harness run's DMX output against the lighting rules.

Reads ONLY what came out of the (simulated) DMX port -- frames.npz, the
per-tick signals, the track's ear-verified Timeline markers and the rig
profile. It never reads engine code and makes no LLM calls, so it can't be
talked into a verdict. Each rule is a small function returning a Verdict:

  PASS / FAIL            measured here
  UNVERIFIABLE_IN_SIM    physically unmeasurable from bytes (strobe Hz)
  NOT_SCORED             needs something this run lacks (e.g. no markers)

Rule IDs are the bible's (spec/conformance.md) plus STRUCT-* from
spec/PENDING_RULES.md. Severity weights: critical 3, major 2, minor 1. Any
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
MIN_HOLD_BARS = {"BREAK": 8, "BUILD": 4, "DROP": 8, "PEAK": 8, "OUTRO": 16}   # TIME-11
LOOK_MIN_HOLD_BARS = {"BREAK": 16, "OUTRO": 16}                                  # TIME-02 (default 8)
LEGAL = {  # scenes/catalog.py LEGAL_TRANSITIONS, as the bible states them
    "BREAK": {"BUILD", "DROP", "OUTRO"}, "BUILD": {"BREAK", "DROP"},
    "DROP": {"BREAK", "BUILD", "PEAK", "OUTRO"}, "PEAK": {"BREAK", "DROP", "OUTRO"}, "OUTRO": set(),
}
STATIC_CUT_LOOKS = ("Blackout Hit", "Slam")     # LOOK-03: "the cut IS the effect"
BUILD_BARS = 16                                   # window before a labeled drop
FILL_BARS = 8                                     # window after a labeled fill
DROP_LATENCY_MAX_BEATS = 2.0                      # STRUCT-03 threshold (first pass)


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
        # bar index per tick, bar 0 starting at the first grid downbeat
        self.bar_dur = timeline.bar_dur
        self.beat_dur = timeline.beat_dur
        self.db0 = timeline.first_downbeat_s % self.bar_dur
        self.bar_of = np.floor((t - self.db0) / self.bar_dur).astype(int)

    # helpers
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

    def next_marker_t(self, after: float) -> float:
        later = [m.t for m in self.tl.markers if m.t > after + 1e-6]
        return min(later) if later else float(self.t[-1]) + self.dt

    def verified_markers(self) -> bool:
        return bool(self.tl.markers) and not any(m.note.startswith("est:") for m in self.tl.markers)


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
    """Each energy state holds its own minimum dwell (bars)."""
    fails, worst = [], {}
    rs = run.runs(run.bucket)
    for k, (b, s, e) in enumerate(rs):
        if k == len(rs) - 1:
            continue  # truncated by end of track
        need = MIN_HOLD_BARS.get(b)
        bars = run.bars_in(s, e)
        if need and bars < need - 0.25:
            fails.append((round(float(run.t[s]), 2), f"{b} held {bars:.1f} bars, needs {need}"))
            worst[b] = min(worst.get(b, 99), bars)
    return Verdict("TIME-11", "FAIL" if fails else "PASS", "critical",
                   {"violations": len(fails), "shortest_dwell_bars": {k: round(v, 2) for k, v in worst.items()}},
                   fails[:12])


@rule("TIME-02", "critical")
def time_02_look_hold(run: Run) -> Verdict:
    """A committed look may not change more often than min_hold_bars (8; 16 for BREAK/OUTRO)."""
    fails = []
    rs = run.runs(run.scene)
    for k, (sc, s, e) in enumerate(rs):
        if k == len(rs) - 1:
            continue
        bucket = run.bucket[s]
        need = LOOK_MIN_HOLD_BARS.get(bucket, 8)
        bars = run.bars_in(s, e)
        if bars < need - 0.25:
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
