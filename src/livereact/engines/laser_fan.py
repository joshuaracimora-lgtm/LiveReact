"""Laser FAN profile (2026-10-28, for the 2026-10-31 event): the Junior Crib
fan, fired by the music instead of the Show tab's buttons.

Josh: "the laser fan (ones we had at Junior crib) ... we should just stick
with the pattern we have now, and we can switch up the colors based on the
frequency of the sound that triggers the lasers", then: "make sure lasers
are also only cued AFTER the drop, and shouldnt necessarily fire after every
drop".

WHEN: engines/laser_trigger.py's moments (dark by default, 2-bar moments,
MIN_GAP_BARS spacing, DUTY_MAX duty, J hit / K cue exempt), with the auto
triggers narrowed by the criteria below. Every auto trigger is already
inside DROP/PEAK (the drop has landed); on top of that:

  plan    a track plan that forbids the laser here (intro / outro) refuses it,
          and so does one whose section here is not a drop (rekordbox says
          build / verse while the energy already reads DROP: not after the drop)
  hit     the drop must be a big one: drop_hit_intensity >= DROP_MIN_HIT
          (first drops measured 1.0, second drops 0.88-0.97 on the
          2026-09-15/16 captures), so softer drops pass without a laser

Each refused or fired auto trigger is logged in ``candidates`` (time, bar,
trigger, hit, the three band envelopes, the color it would get, and the
verdict) -- tools/agent/laser_report.py turns that into the per-drop table
Josh reviews to tune these criteria. First-guess numbers, not measured.

WHAT: the Junior Crib scene bytes verbatim (FAN_SCENE, laser_show.py's
"Rainbow", the manual profile's default), except CH12, picked once when a
moment opens from the sound that opened it (each band's self-normalised
transient envelope, engines/envelope.py):

  mids AND highs both >= BROAD  -> rainbow (the scene's own CH12 colour cycle)
  else the strongest band       -> bass red / mids green / highs blue

Held for the whole moment, so the colour never flickers mid-fan.
"""
from __future__ import annotations

from ..clock import Clock
from ..core.show_state import ShowSnapshot
from ..fixtures.profiles import Laser
from ..fixtures.rig import Rig
from ..merge.merge import Contribution, ManualParams
from .envelope import NORMALIZE_TAU_S, BandEnvelope
from .laser_show import LASER_SCENES
from .laser_trigger import (AUTO_TRIGGERS, MIN_GAP_BARS, MOOD_READY, TRIGGER_DROP, TRIGGER_PEAK,
                            LaserTriggerEngine)

FAN_SCENE_NAME = "Rainbow"
FAN_SCENE = LASER_SCENES[FAN_SCENE_NAME]
RAINBOW = FAN_SCENE[11]          # CH12 255: the scene's own colour cycle
DROP_MIN_HIT = 0.95              # a drop must hit at least this hard to fire the lasers
BROAD = 0.8                      # mids and highs both this strong = a broad, colourful sound

COLOR_NAMES = {Laser.COLOR_RED: "red", Laser.COLOR_GREEN: "green", Laser.COLOR_BLUE: "blue", RAINBOW: "rainbow"}


def fan_color(low: float, mid: float, high: float) -> int:
    """CH12 for a moment opened by a sound with these band envelopes (each 0..1)."""
    if mid >= BROAD and high >= BROAD:
        return RAINBOW
    best = max(low, mid, high)
    if best == low:
        return Laser.COLOR_RED
    return Laser.COLOR_GREEN if best == mid else Laser.COLOR_BLUE


class LaserFanEngine(LaserTriggerEngine):
    """LaserTriggerEngine's timing, the Junior Crib scene, colour by frequency."""

    def __init__(self, clock: Clock, manual: ManualParams | None = None) -> None:
        super().__init__(clock, manual=manual)
        self._mid = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self._high = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self.mid = 0.0
        self.high = 0.0
        self._picked_for: float | None = None    # the moment start the colour was picked for
        self._noted: set[tuple[str, int]] = set()  # (trigger, bar) already in candidates
        self._pending: dict | None = None        # this tick's candidate, verdict still open
        self.candidates: list[dict] = []
        self.color = self.color_l = self.color_r = RAINBOW
        self.color_name = "rainbow"
        self._ch12 = RAINBOW

    def _refusal(self, snap: ShowSnapshot) -> str:
        if snap.plan_active and not snap.planned_laser_allowed:
            return "plan forbids"
        if snap.plan_active and snap.section_kind and snap.section_kind != "drop":
            return f"plan says {snap.section_kind}"   # the energy reads DROP, the track's phrases don't yet
        if float(snap.drop_hit_intensity) < DROP_MIN_HIT:
            return "soft drop"
        return ""

    def _auto_trigger(self, now: float, snap: ShowSnapshot) -> str:
        fired = super()._auto_trigger(now, snap)
        self._pending = None
        if not fired or self.active:
            return fired   # lit already: the base class refuses it; nothing to log
        key = (fired, int(snap.bar))
        new = key not in self._noted
        if new:
            self._noted.add(key)
            self._pending = {"t": round(now, 2), "bar": int(snap.bar), "trigger": fired,
                             "bucket": str(snap.bucket).upper(), "hit": round(float(snap.drop_hit_intensity), 3),
                             "low": round(self.wub, 3), "mid": round(self.mid, 3), "high": round(self.high, 3),
                             "color": COLOR_NAMES[fan_color(self.wub, self.mid, self.high)]}
        why = self._refusal(snap)
        if why:
            self._close_pending(why)
            return ""
        return fired

    def _auto_allowed(self, now: float, snap: ShowSnapshot) -> bool:
        ok = super()._auto_allowed(now, snap)
        if not ok:
            spaced = now - self._last_end < MIN_GAP_BARS * self._bar_s(snap)
            self._close_pending("too soon after the last" if spaced else "duty limit")
        else:
            self._close_pending("fired")
        return ok

    def _close_pending(self, verdict: str) -> None:
        # drop and peak edges are always logged; a wub only when it fires (it re-arms every tick)
        if self._pending is not None and (verdict == "fired" or self._pending["trigger"] in (TRIGGER_DROP, TRIGGER_PEAK)):
            self._pending["verdict"] = verdict
            self.candidates.append(self._pending)
        self._pending = None

    def tick(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        # the envelopes must be current before the base class reads triggers this tick
        self.mid = self._mid.update(snap.audio_band_mid_abs, snap.audio_fresh)
        self.high = self._high.update(snap.audio_band_high_abs, snap.audio_fresh)
        return super().tick(snap, rig)

    def _write(self, c: Contribution, snap: ShowSnapshot, rig: Rig) -> None:
        if (self.active and self.trigger in AUTO_TRIGGERS and snap.plan_active
                and not snap.planned_laser_allowed):
            self._end_moment(self.clock.monotonic())   # a moment running into the plan's laser-free edge stops there
            self.mood, self.primed, self.source = MOOD_READY, True, ""
        if self.active and self._picked_for != self._moment_start:
            self._picked_for = self._moment_start
            self._ch12 = fan_color(self.wub, self.mid, self.high)
            self.color_name = COLOR_NAMES[self._ch12]
        ch12 = self._ch12
        self.color = self.color_l = self.color_r = ch12
        values = FAN_SCENE[:11] + (ch12,)
        for fx in rig.by_role("laser"):
            fx.set_scene(c, values, lit=self.active)
