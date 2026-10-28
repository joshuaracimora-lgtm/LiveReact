"""Tempo-locked moving-head movement for the post-drop looks, anchored on
the CROWD.

Why this exists (Josh, 2026-10-05, first garage runs with both heads up):
after the drop the heads were "just stagnant pointing downwards on my DJ
stand" -- the core DROP look (`DropStrobeStab`) parked both heads at a
fixed 0.5/0.5 for its whole 8-bar hold, and `PeakFullRig`'s only motion
was a 4-bar sine fan.  The first rewrite (same day) gave them five shapes
but centred every one on a raw tilt of ~0.55, which on this hang is the
same zone as the break's stand aim (0.70-0.81): "it spends too much time
pointing down and not at the crowd".  Session-20261005-185803: drop tilt
0.40-0.71, median 0.52; the one moment Josh read as "widened" was the
lowest tilt of the song (0.48).

So the shapes are now built around two ANCHORS per head, not a guess:
  crowd  -- the head's own aim at the venue's `dancefloor_centre`
  stand  -- its aim at `dj_booth`
Both come from fixtures/geometry.py's `aim()` when the head is calibrated
(TESTING.md Test 5: Jog live -> SET AIM on dj_booth, Save rig).  Those
positions are written with `set_aim` (absolute: the finaliser's +-10 %
home remap is skipped, the slew limit still applies), so a calibrated rig
gets full-size motion centred where the floor really is.  Uncalibrated
heads fall back to CROWD_FALLBACK / STAND_FALLBACK below -- the direction
(crowd = lower tilt than the stand) is what the 185803 capture showed on
Josh's truss; the numbers are placeholders until the aim is captured.

Time budget: every shape keeps the beam on the CROWD side of the
crowd/stand midpoint for >= 80 % of its ticks (tests/test_movement.py
checks this) -- lifts away from the stand are fine, time on the stand is
what Josh complained about.  The stand is visited by exactly one corner
of the beat chase, on every other cycle (one beat in eight), and by
nothing else.

ReactiveLayerEngine's own BPM-locked sweep never shows: every look sets
position and Phrase (priority 50) wins the MOVEMENT merge over Reactive
(25), see merge/merge.py.  All functions here are pure in the
ShowSnapshot (no per-scene state), so a crossfade or a bucket flap lands
on a continuous position.

Sizing against the slew limit (PAN 260 deg/s over 540, TILT 200 deg/s
over 270): the beat chase's longest jump (crowd-out -> stand) must land
inside one beat at 130 BPM (0.46 s); the kick lift's tilt kick (0.15 =
40 deg) is up in 0.2 s.  The beat-locked shapes ignore the PEAK speed
multiplier: a half-beat step could only glide.

Functions (the names are what the recorder logs as `head_movement`):
  fan         mirrored sine fan around the crowd, tilt a half-cycle behind
  beat_chase  a new position every beat, the two heads alternating; three
              corners at the crowd, one on the stand
  cross       the two beams swing through the crowd centre and cross
  kick_lift   tilt kicks UP off the crowd on every beat and falls back
  crowd_sweep triangle sweep across the crowd, wide and even
`apply_drop_movement()` picks one per 8-bar block from the drop's first
bar; PEAK (a sustained drop) runs the continuous shapes at double speed.
Amplitudes are UNTUNED by eye.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from ..core.show_state import ShowSnapshot
from ..dynamics import musical_bar_position
from ..fixtures.geometry import aim
from ..fixtures.profiles import MovingHead
from ..fixtures.rig import Rig
from ..merge.merge import Contribution

# --- anchors ------------------------------------------------------------------
CROWD_FALLBACK = (0.5, 0.40)    # (pan, tilt) when the head has no captured aim: PLACEHOLDER
STAND_FALLBACK = (0.5, 0.70)    # the break looks' own "centred, angled down" guess (scenes/base.py)
STAND_BUDGET = 0.20             # time-budget rule: at most this share of ticks on the stand side of the midpoint


@dataclass(frozen=True)
class Anchors:
    """Where the crowd and the stand are, in this head's own raw pan/tilt."""
    crowd_pan: float
    crowd_tilt: float
    stand_pan: float
    stand_tilt: float
    absolute: bool          # True = computed from a captured calibration (write with set_aim)

    @property
    def up(self) -> float:
        """The tilt direction that lifts the beam away from the stand, past
        the crowd (towards the back wall / ceiling): the sign of crowd-minus-
        stand, or -1 (lower tilt) when the two coincide."""
        d = self.crowd_tilt - self.stand_tilt
        return 1.0 if d > 0 else -1.0


FALLBACK_ANCHORS = Anchors(CROWD_FALLBACK[0], CROWD_FALLBACK[1],
                           STAND_FALLBACK[0], STAND_FALLBACK[1], absolute=False)


def anchors_for(rig: Rig, fx) -> Anchors:
    """This head's crowd/stand anchors from real geometry (venue targets +
    a captured calibration), else the placeholders."""
    venue = rig.venue
    if (isinstance(fx, MovingHead) and venue is not None and fx.position is not None
            and fx.calibration.captured):
        crowd = venue.targets.get("dancefloor_centre")
        stand = venue.targets.get("dj_booth")
        if crowd is not None and stand is not None:
            cp, ct, _ = aim(fx.position, fx.facing_deg, crowd, fx.calibration,
                            fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
            sp, st, _ = aim(fx.position, fx.facing_deg, stand, fx.calibration,
                            fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
            return Anchors(cp, ct, sp, st, absolute=True)
    return FALLBACK_ANCHORS


# --- shape constants (0..1 pan/tilt units) ------------------------------------
FAN_PERIOD_BARS = 2.0
FAN_PAN_AMP = 0.30
FAN_TILT_AMP = 0.08

CHASE_PAN_STEP = 0.14           # crowd-out -> stand is ~1.5 steps = 113 deg, ~0.44 s: inside one beat
CHASE_TILT_LIFT = 0.08

CROSS_PERIOD_BARS = 4.0
CROSS_PAN_AMP = 0.25
CROSS_TILT_AMP = 0.05

LIFT_TILT_KICK = 0.15           # 40 deg: the tilt slew (200 deg/s) lands it in 0.2 s, inside the beat
LIFT_PAN_PERIOD_BARS = 4.0
LIFT_PAN_AMP = 0.20

SWEEP_PERIOD_BARS = 2.0
SWEEP_PAN_AMP = 0.40

BLOCK_BARS = 8                  # one movement function per 8-bar block
PEAK_SPEED = 2.0                # PEAK runs the continuous shapes at double speed

MovementFn = Callable[[ShowSnapshot, str, Anchors, float], tuple[float, float]]


def _mirror(side: str) -> float:
    return 1.0 if side == "left" else -1.0


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def fan(snap: ShowSnapshot, side: str, a: Anchors, speed: float = 1.0) -> tuple[float, float]:
    """Mirrored sine fan around the crowd, period 2 bars, tilt a half-cycle behind pan."""
    angle = musical_bar_position(snap) * (2 * math.pi * speed / FAN_PERIOD_BARS)
    m = _mirror(side)
    pan = a.crowd_pan + FAN_PAN_AMP * math.sin(angle) * m
    tilt = a.crowd_tilt + FAN_TILT_AMP * math.sin(angle + math.pi) * m
    return _clamp(pan), _clamp(tilt)


def beat_chase(snap: ShowSnapshot, side: str, a: Anchors, speed: float = 1.0) -> tuple[float, float]:
    """A new position on every beat (`speed` is ignored, see the module
    docstring); the heads alternate -- left moves on the odd steps, right
    on the even ones -- so each kick has one beam landing somewhere new.
    Four corners in order: crowd-out, STAND on every other cycle (the one
    stand visit, one beat in eight; crowd-in-lifted on the cycles between),
    crowd-out-lifted, crowd-in.  Constant within a step: the slew limiter
    supplies the motion, the clock supplies the hit."""
    step = int(musical_bar_position(snap) * 4)               # beats since bar 0
    mine = step // 2 if (step % 2 == 0) == (side == "left") else (step - 1) // 2
    corner = mine % 4
    m = _mirror(side)
    if corner == 1:
        if (mine // 4) % 2 == 0:
            return _clamp(a.stand_pan), _clamp(a.stand_tilt)
        return _clamp(a.crowd_pan - CHASE_PAN_STEP * m * 0.5), _clamp(a.crowd_tilt + a.up * CHASE_TILT_LIFT)
    if corner == 0:
        return _clamp(a.crowd_pan + CHASE_PAN_STEP * m), _clamp(a.crowd_tilt)
    if corner == 2:
        return _clamp(a.crowd_pan + CHASE_PAN_STEP * m), _clamp(a.crowd_tilt + a.up * CHASE_TILT_LIFT)
    return _clamp(a.crowd_pan - CHASE_PAN_STEP * m * 0.5), _clamp(a.crowd_tilt)


def cross(snap: ShowSnapshot, side: str, a: Anchors, speed: float = 1.0) -> tuple[float, float]:
    """Both beams swing through the crowd centre at once (same phase,
    opposite sign), crossing twice a cycle, with a small lift at each crossing."""
    angle = musical_bar_position(snap) * (2 * math.pi * speed / CROSS_PERIOD_BARS)
    m = _mirror(side)
    pan = a.crowd_pan + CROSS_PAN_AMP * math.sin(angle) * m
    tilt = a.crowd_tilt + a.up * CROSS_TILT_AMP * (0.5 + 0.5 * math.cos(2 * angle))
    return _clamp(pan), _clamp(tilt)


def kick_lift(snap: ShowSnapshot, side: str, a: Anchors, speed: float = 1.0) -> tuple[float, float]:
    """Tilt kicks UP off the crowd at the start of every beat and decays
    back across it (a quadratic fall, sharp at the hit), over a slow
    mirrored pan.  `speed` is ignored (beat-locked, see the module docstring)."""
    phase = snap.beat_phase
    tilt = a.crowd_tilt + a.up * LIFT_TILT_KICK * (1.0 - phase) ** 2
    angle = musical_bar_position(snap) * (2 * math.pi / LIFT_PAN_PERIOD_BARS)
    pan = a.crowd_pan + LIFT_PAN_AMP * math.sin(angle) * _mirror(side)
    return _clamp(pan), _clamp(tilt)


def crowd_sweep(snap: ShowSnapshot, side: str, a: Anchors, speed: float = 1.0) -> tuple[float, float]:
    """Triangle sweep (constant speed, no slow-down at the ends) across the
    crowd, wide, tilt held on the crowd."""
    frac = (musical_bar_position(snap) * speed / SWEEP_PERIOD_BARS) % 1.0
    tri = 4.0 * abs(frac - 0.5) - 1.0            # -1 .. 1 .. -1
    pan = a.crowd_pan + SWEEP_PAN_AMP * tri * _mirror(side)
    return _clamp(pan), _clamp(a.crowd_tilt)


# The post-drop order: a choreographed fan first (the drop lands on a
# shape, not a scramble), then the beat hits, then the wide shapes.
DROP_SEQUENCE: tuple[MovementFn, ...] = (fan, beat_chase, cross, kick_lift, crowd_sweep)
FUNCTION_NAMES = {fan: "fan", beat_chase: "beat_chase", cross: "cross",
                  kick_lift: "kick_lift", crowd_sweep: "crowd_sweep"}


def block_index(snap: ShowSnapshot) -> int:
    """Which 8-bar block of the current drop this is (0 = the first eight
    bars after the hit).  Before any drop, blocks count from bar 0."""
    start = snap.drop_start_bar if snap.drop_start_bar is not None else 0
    bars_in = max(0, snap.bar - start)
    return bars_in // BLOCK_BARS


def current_shape(snap: ShowSnapshot) -> MovementFn:
    # the song's own opening shape (rekordbox/song_style.py move_start), then the same order
    return DROP_SEQUENCE[(block_index(snap) + int(snap.planned_move_start)) % len(DROP_SEQUENCE)]


def drop_movement(snap: ShowSnapshot, side: str, a: Anchors = FALLBACK_ANCHORS,
                  peak: bool = False) -> tuple[float, float]:
    """The head position for a DROP (or PEAK) look this tick: the movement
    function for this 8-bar block, at double speed under PEAK."""
    speed = (PEAK_SPEED if peak else 1.0) * float(snap.planned_move_speed)   # song_style.py move_speed
    return current_shape(snap)(snap, side, a, speed)


def drop_movement_name(snap: ShowSnapshot) -> str:
    return FUNCTION_NAMES[current_shape(snap)]


def apply_drop_movement(c: Contribution, rig: Rig, snap: ShowSnapshot, peak: bool = False) -> None:
    """Position every spot for this tick.  Calibrated heads get an absolute
    aim (set_aim: no home remap); the rest get the raw fallback shape."""
    for fx in rig.by_role("spot"):
        a = anchors_for(rig, fx)
        pan, tilt = drop_movement(snap, fx.side, a, peak)
        if a.absolute:
            fx.set_aim(c, pan, tilt)
        else:
            fx.set_position(c, pan, tilt)
