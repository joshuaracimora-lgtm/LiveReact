"""Laser FEATURE profile -- the lasers are the main light (2026-10-01).

Josh's bar residency: his wash bar on the stand at the booth, two Shehds 3W
lasers at 9 ft above a small dancefloor pointing straight (tested to clear
heads), haze available, and the lasers on for (almost) the whole set. The
home rig keeps engines/laser.py's ACCENT profile (one sweep per 8-bar block
in PEAK, verified live) byte-for-byte untouched: a rig file picks THIS engine
with ``"laser_profile": "feature"`` (fixtures/rig.py) and app.py constructs
one or the other. Same priority, same attributes (`active`, `primed`,
`sweep`, `source`, `stab`, `wub`, `color`, `manual_cued`), so ShowEngine's
laser gate, the console and the recorder work unchanged; `mood`, `color_l`
and `color_r` are new.

MOOD, decided every tick from the (already debounced) bucket:

  OFF      no fresh audio (LASER-12). Nothing is written and the gate closes.
  FAN      DROP or PEAK whose held hit (`snap.drop_hit_intensity`) is at
           least FAN_MIN_HIT, or the operator's K cue: the full fan. CH7
           sweep = FAN_BASE + (1 - FAN_BASE) * wub, the verified mechanism
           from the accent engine, held for the WHOLE drop (Josh: "after
           intense beat drops ... the fast fanning pattern"), not in blocks.
  GROOVE   DROP/PEAK with a softer hit: a medium sweep, so a modest drop
           still moves without spending the fan.
  TENSION  BUILD while the rise detector says rising, or while the drop is
           armed: the sweep climbs with the rise index toward TENSION_MAX
           (the RISE_ON..RISE_FULL mapping the build strobe uses).
  CALM     everything else (BREAK, OUTRO, an unarmed BUILD): the line at the
           slowest confirmed sweep (CH7 128 -- "static, slowly moving" on the
           2026-09-15 run), its color walking CALM_CYCLE one step every
           CALM_COLOR_BARS bars from the moment calm began, the right-hand
           laser one step ahead of the left so the pair reads as two colors
           (Josh, 2026-10-01: unison, one palette step apart). TENSION holds
           whatever color calm had reached; FAN/GROOVE use the drop palette
           on both.

`in_transition` is deliberately NOT a mood input: it is a fader-motion flag
that lasts 0.5 s past the motion (core/metronome.py TRANSITION_COOLDOWN),
not "the DJ is in a transition phase" -- the bucket already reads a mix as
BREAK/OUTRO, which is CALM.

The CH7 target is eased over SWEEP_EASE_S so a DROP<->PEAK blip or a mood
flip never snaps the fan; the mood itself changes immediately.

Signal loss: audio loss cuts the laser at once (LASER-12, as on every laser
path). PDL loss does NOT -- the manual-cue precedent (spec/conformance.md
LASER-14): this look is audio-driven, bars free-run on the last BPM for the
color steps, and a main light that blinks out on every Pro DJ Link hiccup
is the wrong trade for a show where the lasers ARE the light. Recorded in
spec/conformance.md's LASER-12 row.

The Bible's §8.5 duty rule ("punctuation, not texture", LASER-14) is
deliberately overridden by this profile -- Josh's call for this venue. The
arm switch (Laser.apply_safety) is not: this engine cannot light anything
finalize won't.

Patterns: only the hardware-verified dotted line + sweep is written
(Laser.set_dotted_line_sweep). The "solid line" / "curvy line" / still-line
looks Josh described need the bench probe in TESTING.md Test 6 first;
nothing unverified reaches the live path until then.
"""

from __future__ import annotations

import math

from ..clock import Clock
from ..core.rise import RISE_FULL, RISE_ON
from ..core.show_state import ShowSnapshot
from ..fixtures.profiles import Laser
from ..fixtures.rig import Rig
from ..merge.merge import Contribution, ManualParams
from ..scenes.base import EnergyBucket
from .envelope import NORMALIZE_TAU_S, BandEnvelope
from .laser import LASER_MIN_HIT_INTENSITY, LASER_SWEEP_BASE, laser_color_for_palette

MOOD_OFF = "off"
MOOD_CALM = "calm"
MOOD_TENSION = "tension"
MOOD_GROOVE = "groove"
MOOD_FAN = "fan"
MOODS = (MOOD_OFF, MOOD_CALM, MOOD_TENSION, MOOD_GROOVE, MOOD_FAN)

# The fan needs the same hit the accent engine's PEAK sweep needs (0.95):
# first drops measured 1.0, second drops 0.88-0.97 on the 2026-09-15/16
# captures, so a track's big drop fans and its softer ones groove. Tune
# against tools/simulations/replay_laser.py before a live run.
FAN_MIN_HIT = LASER_MIN_HIT_INTENSITY
FAN_BASE = LASER_SWEEP_BASE          # 0.5: the fan idles at the speed Josh liked, the wub adds on top
GROOVE_BASE = 0.2                    # a softer drop: medium sweep ...
GROOVE_WUB_GAIN = 0.4                # ... that still breathes with the wub (tops out at 0.6)
TENSION_MAX = 0.4                    # a full rise reaches this; the fan starts at 0.5, so the
                                     # drop is still a step up
TENSION_LEAD_BEATS = 32              # a planned drop within this many beats ramps TENSION (drive mode)
TENSION_ARMED_FLOOR = 0.15           # armed (D key / roll) but not yet rising still reads
CALM_SWEEP = 0.0                     # CH7 128, the slowest confirmed sweep
SWEEP_EASE_S = 0.5                   # the CH7 target's time constant
CALM_COLOR_BARS = 8                  # one color step per this many bars (the Bible's phrase unit)
# The calm walk. Verified CH12 macros only (COLOR_GREEN is the one spot-
# checked on hardware; the others are the QLC+ file's bands). Cool first:
# the Bible's §3.8 "BREAK cool" direction.
CALM_CYCLE = (Laser.COLOR_BLUE, Laser.COLOR_CYAN, Laser.COLOR_PURPLE, Laser.COLOR_GREEN)
# Where each side sits on the walk: the right laser one step ahead.
CALM_SIDE_STEP = {"left": 0, "right": 1}

LASER_COLOR_NAMES: dict[int, str] = {
    Laser.COLOR_WHITE: "white",
    Laser.COLOR_RED: "red",
    Laser.COLOR_YELLOW: "yellow",
    Laser.COLOR_GREEN: "green",
    Laser.COLOR_CYAN: "cyan",
    Laser.COLOR_BLUE: "blue",
    Laser.COLOR_PURPLE: "purple",
}


def laser_color_name(macro: int) -> str:
    return LASER_COLOR_NAMES.get(macro, str(macro))


def laser_mood(snap: ShowSnapshot, cued: bool = False) -> str:
    """The mood table in the module docstring. Audio loss wins over everything,
    then the operator's cue, then the bucket. Returns a MOOD_* string."""
    if not snap.audio_fresh:
        return MOOD_OFF
    if cued:
        return MOOD_FAN
    if snap.plan_active and not snap.planned_laser_allowed:
        return MOOD_OFF   # the track plan's quiet edges (intro / outro): no laser
    bucket = EnergyBucket(snap.bucket)
    if bucket in (EnergyBucket.DROP, EnergyBucket.PEAK):
        return MOOD_FAN if float(snap.drop_hit_intensity) >= FAN_MIN_HIT else MOOD_GROOVE
    if bucket == EnergyBucket.BUILD and (snap.rising or snap.drop_armed or _plan_countdown(snap) > 0.0):
        return MOOD_TENSION
    return MOOD_CALM


def _plan_countdown(snap: ShowSnapshot) -> float:
    """0..1 progress of the planned count-in to the next drop (0 when no plan / too far)."""
    if snap.plan_active and 0 < snap.beats_to_drop <= TENSION_LEAD_BEATS:
        return 1.0 - (snap.beats_to_drop - 1) / float(TENSION_LEAD_BEATS)
    return 0.0


def tension_sweep(snap: ShowSnapshot) -> float:
    """TENSION's CH7 target: the rise index mapped RISE_ON..RISE_FULL onto
    0..TENSION_MAX while rising; an armed drop that is not (yet) rising holds
    TENSION_ARMED_FLOOR so the arm itself shows."""
    rise = 0.0
    if snap.rising:
        rise = (float(snap.rise_index) - RISE_ON) / (RISE_FULL - RISE_ON)
        rise = max(0.0, min(1.0, rise))
    floor = TENSION_ARMED_FLOOR if snap.drop_armed else 0.0
    return max(floor, rise * TENSION_MAX, _plan_countdown(snap) * TENSION_MAX)


class LaserFeatureEngine:
    """Stateful (mood, eased sweep, calm color walk, wub/stab envelopes);
    tick once per ShowEngine frame. Drop-in for engines/laser.py's
    LaserEngine where app.py, the console and the recorder are concerned."""

    priority = 60  # same tier as the accent engine: owns the laser channels over any look

    def __init__(self, clock: Clock, manual: ManualParams | None = None,
                 ease_s: float = SWEEP_EASE_S) -> None:
        self.clock = clock
        self.manual = manual
        self.ease_s = ease_s
        self._wub = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self._stab = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self._last_t: float | None = None
        self._calm_entry_bar: int | None = None  # the bar the current calm stretch began
        self._calm_index = 0                     # steps taken on CALM_CYCLE since then
        self.mood = MOOD_OFF
        self.active = False      # lit (any mood but OFF) -- ShowEngine's gate reads this
        self.primed = False      # never: this profile holds the line lit instead of primed
        self.sweep = 0.0         # the eased 0..1 CH7 sweep intensity actually sent
        self.target = 0.0        # this tick's un-eased target (telemetry)
        self.source = ""         # "feature" / "manual" (K cue forcing the fan) / ""
        self.wub = 0.0           # low-band envelope (fan / groove speed)
        self.stab = 0.0          # mids + highs envelope (recorder parity with the accent engine)
        self.color = Laser.COLOR_BLUE    # == color_l, for callers that know one color
        self.color_l = Laser.COLOR_BLUE
        self.color_r = Laser.COLOR_CYAN

    @property
    def manual_cued(self) -> bool:
        return bool(self.manual is not None and self.manual.laser_cue)

    @property
    def name(self) -> str:
        return "laser"

    def color_for(self, fx: Laser) -> int:
        return self.color_r if fx.side == "right" else self.color_l

    def _off(self) -> None:
        self.sweep = 0.0
        self.target = 0.0
        self.active = False
        self.primed = False
        self.source = ""
        self._calm_entry_bar = None

    def tick(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        c = Contribution()
        now = self.clock.monotonic()
        dt = (now - self._last_t) if self._last_t is not None else 1.0 / 60.0
        self._last_t = now
        self.wub = self._wub.update(snap.audio_band_low_abs, snap.audio_fresh)
        self.stab = self._stab.update(snap.audio_band_mid_abs + snap.audio_band_high_abs, snap.audio_fresh)

        prev = self.mood
        mood = laser_mood(snap, self.manual_cued)
        self.mood = mood
        if mood == MOOD_OFF:
            self._off()
            return c

        # --- color ---------------------------------------------------------
        if mood in (MOOD_CALM, MOOD_TENSION):
            if mood == MOOD_CALM and (self._calm_entry_bar is None or prev in (MOOD_FAN, MOOD_GROOVE)):
                self._calm_entry_bar = snap.bar        # a new calm stretch: restart the walk
                self._calm_index = 0
            if self._calm_entry_bar is None:
                self._calm_entry_bar = snap.bar        # TENSION straight out of a drop: start here
            if mood == MOOD_CALM:                      # TENSION holds the index calm reached
                self._calm_index = max(0, snap.bar - self._calm_entry_bar) // CALM_COLOR_BARS
            n = len(CALM_CYCLE)
            self.color_l = CALM_CYCLE[(self._calm_index + CALM_SIDE_STEP["left"]) % n]
            self.color_r = CALM_CYCLE[(self._calm_index + CALM_SIDE_STEP["right"]) % n]
        else:
            self.color_l = self.color_r = laser_color_for_palette(snap.palette)
        self.color = self.color_l

        # --- CH7 sweep target, eased -------------------------------------------
        if mood == MOOD_FAN:
            target = FAN_BASE + (1.0 - FAN_BASE) * self.wub
        elif mood == MOOD_GROOVE:
            target = GROOVE_BASE + GROOVE_WUB_GAIN * self.wub
        elif mood == MOOD_TENSION:
            target = tension_sweep(snap)
        else:
            target = CALM_SWEEP
        self.target = target
        if self.ease_s > 0:
            self.sweep += (1.0 - math.exp(-dt / self.ease_s)) * (target - self.sweep)
        else:
            self.sweep = target
        self.sweep = max(0.0, min(1.0, self.sweep))

        self.active = True
        self.primed = False
        self.source = "manual" if self.manual_cued else "feature"
        for fx in rig.by_role("laser"):
            fx.set_dotted_line_sweep(c, color=self.color_for(fx), sweep_intensity=self.sweep, lit=True)
        return c
