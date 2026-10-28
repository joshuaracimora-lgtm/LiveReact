"""Laser TRIGGER profile -- the lasers are punctuation, fired by moments
(2026-10-06).

Josh: "I have the lasers envisioned as something that shouldn't necessarily
have SCENES, rather a specific trigger (to a certain sound or scene -- or
if too difficult, we can have manual scene buttons for the lasers). The
lasers add the novel 'wow' factor to the sets, and should be used more
sparingly."  That is LIGHTING_BIBLE.md §8.5 / LASER-14 ("punctuation, not
texture": gated on DROP/PEAK, <= 10 % of set time, spaced), which the
feature profile (engines/laser_feature.py, lit for the whole set) had
deliberately overridden.

DARK by default.  A MOMENT lights the lasers for MOMENT_BARS bars and then
they go dark again.  Moments come from:

  AUTO triggers (each needs fresh audio AND fresh Pro DJ Link -- LASER-12):
    drop   the bucket ENTERS DROP from below (the hit itself): the big one
    peak   a sustained PEAK (hit >= PEAK_MIN_HIT): one moment per
           PEAK_BLOCK_BARS-bar block boundary, counted from drop_start_bar
           (the accent engine's verified mechanism)
    wub    DROP/PEAK and the low-band "wub" envelope (engines/envelope.py,
           self-normalised) clears WUB_TRIGGER -- the sound trigger
    Spacing: an auto moment needs MIN_GAP_BARS of darkness since the last
    moment ended.  Duty: over the last DUTY_WINDOW_S the lit share may not
    exceed DUTY_MAX, or auto triggers are refused.  A refused trigger
    consumes nothing (LASER-15).

  MANUAL (the operator's call, exempt from spacing and duty):
    hit    ManualParams.laser_hit (console LASER HIT / J): one moment, now
    cue    ManualParams.laser_cue (CUE LASER / K, held): lit while held,
           sweep speed from the mids + highs (unchanged from 2026-09-30)

What a moment SHOWS is still the one hardware-verified pattern
(Laser.set_dotted_line_sweep): the dotted line sweeping, speed = FAN_BASE
+ (1 - FAN_BASE) * wub for every auto trigger and the hit; the cue follows
the stab envelope.  Colour = the phrase palette snapped to a CH12 macro
(engines/laser.py's table).  More looks need the bench probe (TESTING.md
Test 6a) -- when they exist, each trigger can get its own.

Between moments the pattern is written PRIMED (every channel but CH1, which
stays 0 = off) whenever audio is fresh, so the unit already holds the line
when CH1 goes to manual and a 2-bar moment shows the line, not the unit's
default circle (the 2026-09-16 finding).  Priming never lights anything:
finalize's arm switch (Laser.apply_safety) zeroes everything when
disarmed, and apply_laser_gate forces CH1 to 0 unless this engine says
`active`.

Interface: the same attributes ShowEngine, the console and the recorder
read from the other two laser engines (`active`, `primed`, `sweep`,
`source`, `stab`, `wub`, `color`, `color_l`, `color_r`, `target`,
`manual_cued`, `mood`), plus `trigger` (which one lit the current moment)
and `budget` (lit share over the duty window).  `mood` is "off" (signal
lost), "ready" (dark, armed or not), or the trigger name while lit, so the
console's chip reads TRIGGER · READY / DROP / WUB / HIT / CUE.
"""
from __future__ import annotations

from collections import deque

from ..clock import Clock
from ..core.show_state import ShowSnapshot
from ..fixtures.profiles import Laser
from ..fixtures.rig import Rig
from ..merge.merge import Contribution, ManualParams
from ..scenes.base import EnergyBucket
from .envelope import NORMALIZE_TAU_S, BandEnvelope
from .laser import LASER_MIN_HIT_INTENSITY, LASER_SWEEP_BASE, laser_color_for_palette

MOMENT_BARS = 2.0            # one laser moment lasts this many bars
PEAK_BLOCK_BARS = 8          # a sustained PEAK gets one moment per block of this many bars
PEAK_MIN_HIT = LASER_MIN_HIT_INTENSITY
MIN_GAP_BARS = 8.0           # darkness required between auto moments (end -> next start)
DUTY_WINDOW_S = 120.0        # the duty rule's rolling window
DUTY_MAX = 0.10              # LASER-14: lit for at most this share of the window (auto only)
WUB_TRIGGER = 0.9            # the normalised low-band envelope that counts as "that sound"
FAN_BASE = LASER_SWEEP_BASE  # 0.5: a moment's sweep idles here, the wub adds on top
CUE_FLOOR = 0.0              # the held cue: stab 0 = the slowest confirmed sweep

TRIGGER_DROP = "drop"
TRIGGER_PEAK = "peak"
TRIGGER_WUB = "wub"
TRIGGER_HIT = "hit"
TRIGGER_CUE = "cue"
AUTO_TRIGGERS = (TRIGGER_DROP, TRIGGER_PEAK, TRIGGER_WUB)

MOOD_OFF = "off"
MOOD_READY = "ready"

_HOT = (EnergyBucket.DROP, EnergyBucket.PEAK)


class LaserTriggerEngine:
    """Stateful (moment window, spacing, duty history, envelopes); tick once
    per ShowEngine frame.  Drop-in for engines/laser.py's LaserEngine."""

    priority = 60

    def __init__(self, clock: Clock, manual: ManualParams | None = None) -> None:
        self.clock = clock
        self.manual = manual
        self._wub = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self._stab = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self._moment_until = 0.0
        self._moment_start = 0.0
        self._last_end = -1e9               # when the last moment (auto or manual) ended
        self._history: deque[tuple[float, float]] = deque()   # (start, end) of past moments
        self._prev_bucket: EnergyBucket | None = None
        self._last_peak_block: int | None = None
        self._last_peak_origin: int | None = None
        self._hits_seen = 0                 # ManualParams.laser_hit is a counter: fire on each increment
        self.mood = MOOD_OFF
        self.trigger = ""                   # what lit the current moment ("" = dark)
        self.active = False
        self.primed = False
        self.sweep = 0.0
        self.target = 0.0
        self.source = ""                    # "auto" / "manual" / ""
        self.wub = 0.0
        self.stab = 0.0
        self.budget = 0.0                   # lit share of the duty window (telemetry)
        self.color = Laser.COLOR_WHITE
        self.color_l = Laser.COLOR_WHITE
        self.color_r = Laser.COLOR_WHITE

    @property
    def manual_cued(self) -> bool:
        return bool(self.manual is not None and self.manual.laser_cue)

    @property
    def name(self) -> str:
        return "laser"

    # ---------------------------------------------------------------- budget
    def _bar_s(self, snap: ShowSnapshot) -> float:
        return 4.0 * (60.0 / snap.bpm if snap.bpm > 0 else 0.5)

    def _lit_share(self, now: float) -> float:
        lo = now - DUTY_WINDOW_S
        while self._history and self._history[0][1] < lo:
            self._history.popleft()
        lit = sum(max(0.0, min(e, now) - max(s, lo)) for s, e in self._history)
        if self.active:
            lit += now - max(self._moment_start, lo)
        return lit / DUTY_WINDOW_S

    def _auto_allowed(self, now: float, snap: ShowSnapshot) -> bool:
        if self.active:
            return False
        if now - self._last_end < MIN_GAP_BARS * self._bar_s(snap):
            return False
        return self._lit_share(now) < DUTY_MAX

    def _open(self, now: float, snap: ShowSnapshot, trigger: str, bars: float = MOMENT_BARS) -> None:
        self._moment_start = now
        self._moment_until = now + bars * self._bar_s(snap)
        self.trigger = trigger

    def _end_moment(self, now: float) -> None:
        if self.active:
            self._history.append((self._moment_start, now))
            self._last_end = now
        self.active = False
        self.trigger = ""

    # ---------------------------------------------------------------- triggers
    def _auto_trigger(self, now: float, snap: ShowSnapshot) -> str:
        """The first auto trigger that fires this tick, or ''.  Evaluated
        every tick so the bucket-entry and block-boundary edges are seen even
        when the budget refuses them (a refused trigger consumes nothing)."""
        bucket = EnergyBucket(snap.bucket)
        entered_drop = (bucket == EnergyBucket.DROP
                        and self._prev_bucket not in _HOT and self._prev_bucket is not None)
        fired = ""
        if entered_drop:
            fired = TRIGGER_DROP
        elif bucket == EnergyBucket.PEAK and float(snap.drop_hit_intensity) >= PEAK_MIN_HIT:
            origin = snap.drop_start_bar if snap.drop_start_bar is not None else 0
            if origin != self._last_peak_origin:
                self._last_peak_origin = origin
                self._last_peak_block = None
            bars_in = snap.bar - origin
            if bars_in >= 0 and bars_in % PEAK_BLOCK_BARS == 0:
                block = bars_in // PEAK_BLOCK_BARS
                if block != self._last_peak_block:
                    self._last_peak_block = block
                    fired = TRIGGER_PEAK
        if not fired and bucket in _HOT and self.wub >= WUB_TRIGGER:
            fired = TRIGGER_WUB
        return fired

    def tick(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        c = Contribution()
        now = self.clock.monotonic()
        self.wub = self._wub.update(snap.audio_band_low_abs, snap.audio_fresh)
        self.stab = self._stab.update(snap.audio_band_mid_abs + snap.audio_band_high_abs, snap.audio_fresh)
        self.color = self.color_l = self.color_r = laser_color_for_palette(snap.palette)

        if not snap.audio_fresh:                      # LASER-12: audio loss = dark, now
            self._end_moment(now)
            self._prev_bucket = None
            self.mood = MOOD_OFF
            self.primed = False
            self.source = ""
            self.sweep = self.target = 0.0
            self.budget = self._lit_share(now)
            return c

        # --- manual, exempt from spacing and duty
        hits = int(getattr(self.manual, "laser_hit", 0)) if self.manual is not None else 0
        if hits != self._hits_seen:
            self._hits_seen = hits
            if not self.active or self.trigger != TRIGGER_CUE:
                self._end_moment(now)
                self._open(now, snap, TRIGGER_HIT)
                self.active = True
        if self.manual_cued:
            if self.trigger != TRIGGER_CUE:
                self._end_moment(now)
                self.trigger = TRIGGER_CUE
                self._moment_start = now
                self.active = True
            self._moment_until = now + 1.0                # held: keep it open while the cue lasts
        elif self.trigger == TRIGGER_CUE:
            self._end_moment(now)                         # released: straight back to dark

        # --- auto (needs the beat grid too)
        if snap.pdl_fresh:
            fired = self._auto_trigger(now, snap)
            if fired and self._auto_allowed(now, snap):
                self._open(now, snap, fired)
                self.active = True
        elif self.active and self.trigger in AUTO_TRIGGERS:
            self._end_moment(now)                         # LASER-12 for the auto path: PDL loss closes it
        self._prev_bucket = EnergyBucket(snap.bucket)

        # --- the moment window
        if self.active and now >= self._moment_until:
            self._end_moment(now)

        if self.active:
            if self.trigger == TRIGGER_CUE:
                self.target = CUE_FLOOR + (1.0 - CUE_FLOOR) * self.stab
            else:
                self.target = FAN_BASE + (1.0 - FAN_BASE) * self.wub
            self.sweep = self.target
            self.source = "manual" if self.trigger in (TRIGGER_HIT, TRIGGER_CUE) else "auto"
            self.mood = self.trigger
            self.primed = False
        else:
            self.target = self.sweep = FAN_BASE
            self.source = ""
            self.mood = MOOD_READY
            self.primed = True
        self.budget = self._lit_share(now)
        self._write(c, snap, rig)
        return c

    def _write(self, c: Contribution, snap: ShowSnapshot, rig: Rig) -> None:
        """What a moment shows (and what is primed between them); engines/laser_fan.py overrides it."""
        for fx in rig.by_role("laser"):
            fx.set_dotted_line_sweep(c, color=self.color, sweep_intensity=self.sweep, lit=self.active)
