"""The Tease (P-24, 2026-10-30): the kick-free bars before a drop, lit as a rise toward the drop.

Josh: "In songs, when the kicks and claps leave, yet the intensity is still high, this is when that
scene should appear ... before a drop (intensity before a drop to tease the incoming drop) ... because
this is a long stretch of time, it should start dim and then increase brightness as the scene
progresses". The planner (rekordbox/planner.py `teases`) says WHERE from the colour waveform; this
module says HOW it looks, tick by tick:

  level   a steady climb across the Tease on bar position (the Bible's LOOK-04: predictable), TEASE_FLOOR ->
          TEASE_CEIL, that the music nudges a little either way (Josh's pick, 2026-10-30, after the first
          "follow the music" build: "The Bible is probably correct ... at a similar speed"): loudness
          against its own running average inside this Tease moves the level by up to TEASE_NUDGE.
  bright  a Tease that starts INSIDE a drop (only an ear-note cue does: Thriller 1:03-1:17, "where most of
          those stabs live, so it can be bright there, and when the intensity drops in the periods between
          stabs (short ~1 sec bursts), the brightness can decrease with it") holds TEASE_BRIGHT and follows
          the stab bursts quickly, by up to TEASE_BRIGHT_NUDGE.
  punch   a stab or a lone hit inside the Tease (Tongue Tied 1:22.8, Thriller's stabs) punches up fast and
          "shutters down when it cools down off the punch" -- a short attack, a smooth decay back to the
          level, never a step from one value to the next (Josh, 2026-10-30).
  colour  the song colour drifting to the song's flash tint (engines/reactive.py flash_colour) across the
          Tease, so it ends on the colour the roll strobe flashes in (Josh: "Song colour -> white").
  hold    inside a roll the Tease runs into, the wash keeps the level the Tease reached under the
          strobe: "the transition between the bright light and then the strobe needs to be smooth, it
          cant jump from intense to not intense, must keep some intensity factor".

Applied after the engines' merge and before finalize (core/show_engine.py), like the look-note cues, so
master brightness, blackout, the slew limit and every laser clamp still apply.
"""
from __future__ import annotations

import math

from ..core.show_state import ShowSnapshot
from ..merge.merge import Contribution
from ..core.frame_buffer import UNIVERSE_SIZE
from .palette import rgb as palette_rgb
from .reactive import flash_colour

# Levels are 0..1 BEFORE the dimmer's gamma 2.2 + byte-31 floor (fixtures/profiles.py _intensity_to8):
# anything under ~0.4 lands on the floor byte, so 0.12 -> 0.40 measured as one flat byte in the first
# simulation. 0.42 is byte ~40 (dim, visibly on), 0.72 byte ~124; a punch can reach ~0.97.
TEASE_FLOOR = 0.42          # where a quiet Tease starts
TEASE_CEIL = 0.72           # the loudest a Tease's base gets: under the drop's held wash (reactive DROP_HELD_LOW/HIGH 0.66-0.76), so the drop still lands bigger
TEASE_PEAK_TAU_S = 60.0     # loudness is read against a peak follower decaying this slowly (gain-independent)
TEASE_FOLLOW_TAU_S = 1.0    # the loudness the nudge reads, smoothed ~2 beats ...
TEASE_AVG_TAU_S = 4.0       # ... against its own average inside this Tease
TEASE_NUDGE = 0.08          # the music moves the climbing level by at most this either way ...
TEASE_NUDGE_SPAN = 0.15     # ... when it is this far (share of the peak) above or below its average
TEASE_BRIGHT = 0.80         # a bright Tease's level (a drop holds 0.66-0.76) ...
TEASE_BRIGHT_NUDGE = 0.20   # ... rising and falling with the stab bursts by up to this,
TEASE_BRIGHT_FOLLOW_TAU_S = 0.15   # read fast enough to dip between ~1 s bursts
TEASE_BRIGHT_AVG_TAU_S = 2.0
TEASE_HOLD_OFF_SHARE = 0.6  # under the roll after a Tease: between flashes the wash holds this share of the
                            # Tease's dimmer byte, a flash at least the full byte -- a hold brighter than the roll's
                            # early flashes (Thriller's bright Tease, 0.75) hid the strobe until it out-climbed it
TEASE_PUNCH_DEPTH = 0.25    # a punch adds up to this over the base level (capped at full)
TEASE_PUNCH_ATTACK_S = 0.03
TEASE_PUNCH_DECAY_S = 0.45  # the "shutter down" off the punch
TEASE_ONSET_FAST_S = 0.03   # stab detector on the LOW band (stabs and lone hits carry bass, vocals do not):
TEASE_ONSET_SLOW_S = 0.60   # a fast follower against a slow one ...
TEASE_ONSET_RATIO = 1.5     # ... an onset when the fast one clears the slow one by this much. Tuned on the
                            # simulated signals: Thriller's once-a-bar stabs at 27.6 / 29.1 s (low ~21 over ~10)
                            # and Tongue Tied's lone hit at 1:22.4-1:23.0 fire; overall loudness (first try,
                            # ratio 1.6 on RMS) never fired on Thriller's sustained stabs
TEASE_ONSET_FLOOR = 0.5     # slow low band (gained abs units) under this is too quiet to punch
TEASE_ONSET_REFRACTORY_S = 0.3   # punches stay under ~3.3 per second (SAFE-01's 4 Hz line)


def _ema(prev: float, x: float, dt: float, tau: float) -> float:
    return x if tau <= 0 else prev + (1.0 - math.exp(-max(0.0, dt) / tau)) * (x - prev)


class TeaseLook:
    """Per-tick Tease state. ``update`` returns (level 0..1, rgb, hold) or None when no Tease applies."""

    def __init__(self) -> None:
        self.level = 0.0            # exposed for the recorder: this tick's Tease level (0 = none)
        self.punch = 0.0
        self._x = None              # smoothed loudness vs the peak follower
        self._avg = None            # its average inside this Tease
        self._peak = 0.0
        self._fast = 0.0
        self._slow = 0.0
        self._last_t: float | None = None
        self._warm_since: float | None = None   # the stab detector's followers settle before it may fire
        self._last_onset = -1e9
        self._attack_until = -1e9
        self._hold: tuple[float, tuple[float, float, float]] | None = None
        self.onsets = 0             # count, for tests and the report

    def update(self, snap: ShowSnapshot) -> tuple[float, tuple[float, float, float], bool] | None:
        now = snap.t
        dt = 0.0 if self._last_t is None else max(0.0, now - self._last_t)
        self._last_t = now
        # the stab detector runs every tick so it is warm when a Tease starts
        e = float(snap.audio_band_low_abs) if snap.audio_fresh else 0.0
        self._fast = _ema(self._fast, e, dt, TEASE_ONSET_FAST_S)
        self._slow = _ema(self._slow, e, dt, TEASE_ONSET_SLOW_S)
        if not snap.audio_fresh:
            self._warm_since = None
        elif self._warm_since is None:
            self._warm_since = now
        warm = self._warm_since is not None and now - self._warm_since >= 2 * TEASE_ONSET_SLOW_S
        # the song's recent loudest (gain-independent ratio below): rises at once, decays over a minute
        smooth = float(snap.energy) if snap.audio_fresh else 0.0
        self._peak = max(smooth, self._peak * (math.exp(-dt / TEASE_PEAK_TAU_S) if dt > 0 else 1.0))
        progress = float(snap.planned_tease_progress)
        if progress < 0.0:
            self._x = None
            self._avg = None
            self.punch = 0.0
            if snap.planned_tease_hold and self._hold is not None:
                self.level = self._hold[0]
                return self._hold[0], self._hold[1], True
            self._hold = None
            self.level = 0.0
            return None

        bright = bool(snap.planned_tease_bright)
        x_in = (smooth / self._peak) if (snap.audio_fresh and self._peak > 1e-6) else None
        if x_in is None:
            nudge01 = 0.0   # no audio: the bar-position climb alone
        else:
            ft, at = (TEASE_BRIGHT_FOLLOW_TAU_S, TEASE_BRIGHT_AVG_TAU_S) if bright else (TEASE_FOLLOW_TAU_S, TEASE_AVG_TAU_S)
            self._x = x_in if self._x is None else _ema(self._x, x_in, dt, ft)
            self._avg = self._x if self._avg is None else _ema(self._avg, self._x, dt, at)
            nudge01 = max(-1.0, min(1.0, (self._x - self._avg) / TEASE_NUDGE_SPAN))
        if bright:
            base = TEASE_BRIGHT + TEASE_BRIGHT_NUDGE * nudge01
        else:
            base = TEASE_FLOOR + (TEASE_CEIL - TEASE_FLOOR) * progress + TEASE_NUDGE * nudge01
        base = max(TEASE_FLOOR - TEASE_NUDGE, min(1.0, base))

        if (warm and self._slow > TEASE_ONSET_FLOOR and self._fast > TEASE_ONSET_RATIO * self._slow
                and now - self._last_onset >= TEASE_ONSET_REFRACTORY_S):
            self._last_onset = now
            self._attack_until = now + 3 * TEASE_PUNCH_ATTACK_S
            self.onsets += 1
        if now < self._attack_until:
            self.punch = _ema(self.punch, 1.0, dt, TEASE_PUNCH_ATTACK_S)
        else:
            self.punch *= math.exp(-dt / TEASE_PUNCH_DECAY_S) if dt > 0 else 1.0
        level = min(1.0, base + TEASE_PUNCH_DEPTH * self.punch)

        song = snap.planned_song_palette or snap.palette
        r0, g0, b0 = palette_rgb(song)
        r1, g1, b1 = flash_colour(song, snap.planned_flash_white)
        rgb = (r0 + (r1 - r0) * progress, g0 + (g1 - g0) * progress, b0 + (b1 - b0) * progress)
        self._hold = (base, (r1, g1, b1))   # what a roll after this Tease holds: the level reached, the flash tint
        self.level = level
        return level, rgb, False


def apply_tease(merged: bytearray, rig, level: float, rgb: tuple[float, float, float], *,
                strobe_on: bool, hold: bool) -> None:
    """Write a Tease onto the merged frame. In the Tease itself the washes and heads take its colour and
    level outright (it starts dim even over a brighter build wash); while a strobe flash is ON, or in the
    hold under a roll, the level is a floor (a brighter flash stays) and the flash keeps its own colour."""
    r, g, b = rgb
    fixtures = list(rig.by_role("wash")) + ([] if hold else list(rig.by_role("spot")))
    # a flash keeps its own colour on the washes; the heads keep the Tease's (run 6: they flipped
    # between the drop's magenta and the Tease's orange on every flash)
    painted = fixtures if not strobe_on else [fx for fx in fixtures if fx.role == "spot"]
    if painted:
        col = Contribution()
        for fx in painted:
            fx.set_color(col, r, g, b, min(r, g, b))
        for i in range(UNIVERSE_SIZE):
            if col.touched[i]:
                merged[i] = col.values[i]
    lvl = Contribution()
    for fx in fixtures:
        fx.set_intensity(lvl, level)
    floor_only = strobe_on or hold
    for i in range(UNIVERSE_SIZE):
        if lvl.touched[i]:
            v = lvl.values[i]
            if hold and not strobe_on:
                v = max(1, round(v * TEASE_HOLD_OFF_SHARE))   # between flashes: dipped, never dark
            merged[i] = max(merged[i], v) if floor_only else v
