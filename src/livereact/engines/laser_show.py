"""Laser SHOW profile -- the lasers are lit for the whole set (2026-10-08,
Sophia's Thai: two Shehds 3W lasers + the wash bar, nothing else).

Josh: "In Laser Show mode the lasers should be on with the default scene
for the whole set. The existing laser gate (armed + DROP/PEAK + kick count,
etc.) should not keep them dark here."  So `active` is True on every tick;
finalize's gate reads it and lets CH1 through.  Blackout (B) still zeroes
the universe in finalize and the arm switch (L) still zeroes the laser
channels in Laser.apply_safety -- app.py arms the lasers at launch for
this profile so the gate does not keep them dark.

THREE MODES (ManualParams.laser_mode, the console's Laser Show tab):

  follow   "Follow the music" (the default, 2026-10-08 afternoon).  The
           look comes from the existing detection, nothing new is detected:
             drop_pending (the bar before a confirmed drop) -> Dark
             BREAK / OUTRO                                  -> Slow Rainbow
             BUILD, not armed                               -> Tension
             BUILD, drop armed (D / snare roll / rise)      -> Armed
             DROP, first FAN_BARS bars from drop_start_bar  -> Fan
             DROP after that                                -> Rainbow
             PEAK                                           -> Ping-Pong
           Needs fresh audio AND a fresh beat grid; otherwise it falls
           back to the rotation below so the lasers never just stop.
  rotate   Every ROTATE_BARS bars on the beat grid (ROTATE_FALLBACK_S of
           wall-clock without one) step through the STATIC scenes in
           order, starting from the picked one.
  hold     The manual override: show the picked look (any of LOOKS) and
           nothing else.  Pressing a look button on the tab picks it and
           switches to hold.

LOOKS.  Static scenes are verbatim 12-channel byte lists written by
Laser.set_scene -- "Rainbow" was read off Josh's QLC+ scene, the rest are
variations on it or on the hardware-verified dotted-line sweep.  Dynamic
looks are built per tick from the same verified vocabulary (the dotted
line CH2 118, the dot lattice CH11 190, the CH7 128-255 sweep speed, the
CH12 colour bands):
    Fan        the fast dotted sweep in the phrase palette's colour; the
               low-band "wub" envelope (engines/envelope.py) pushes the
               speed on every bass hit
    Tension    blue, sweep speed ramping from slowest to fastest across the
               build -- the rise index and bars-into-the-build, whichever
               is further along
    Armed      Tension's bytes chopped on/off (CH1 only) on the beat; on
               8th notes when a snare roll armed the drop
    Ping-Pong  L and R alternate per bar (L blue, R red, wub speed); both
               lit on each PEAK_BLOCK_BARS-bar block boundary bar
               (a single-laser rig keeps its one head lit, blue/red per bar).
    Dark       everything primed, CH1 0
Every on/off chop touches only CH1 (the documented OFF band), so the
pattern channels stay primed and the unit comes back to the same look
(the 2026-09-16 priming finding; chops faster than a beat are avoided
because sub-0.25 s flashes once showed the default circle).

DROP ACCENT (ManualParams.laser_drop_accent): in rotate / hold modes, when
the bucket ENTERS DROP from below, the current look's CH1 is chopped at
8th notes for ACCENT_BARS bars, then it returns.  Follow mode has the Fan
instead.  UNVERIFIED on hardware (built the day of the set).

Interface: the attributes ShowEngine, the console and the recorder read
from the other laser engines (`active`, `primed`, `sweep`, `source`,
`stab`, `wub`, `color`, `color_l`, `color_r`, `target`, `manual_cued`,
`mood`), plus `mode`, `scene_name` (the look being shown right now),
`reason` (why, in follow mode), `rotating`, `accent` and `bars_to_next`.
"""
from __future__ import annotations

from ..clock import Clock
from ..core.show_state import ShowSnapshot
from ..fixtures.profiles import Laser
from ..fixtures.rig import Rig
from ..merge.merge import Contribution, ManualParams
from ..scenes.base import EnergyBucket
from .envelope import NORMALIZE_TAU_S, BandEnvelope
from .laser import laser_color_for_palette

# name -> CH1..CH12 bytes, written verbatim by Laser.set_scene. Order = the
# rotation order. Channel roles per the Laser docstring (fixtures/profiles.py):
# CH1 mode (42-83 manual) · CH2 pattern group · CH3 sub-pattern · CH4 rotate ·
# CH5/6 flips · CH7 horizontal (128-255 sweep speed) · CH8 vertical ·
# CH9 size (nonzero = breathing) · CH10 painting speed · CH11 scan (0-127
# speed, 128-255 dot lattice) · CH12 colour.
# The static scenes live in scenes/laser_scenes.json (engines/laser_scenes.py loads, checks and
# describes them; 2026-10-29). The first four are Sophia's verbatim looks, unchanged; the rest are
# composed from the manufacturer's channel table and marked "manual" until seen on the unit.
from .laser_scenes import scene_values as _scene_values

LASER_SCENES: dict[str, tuple[int, ...]] = _scene_values()
DEFAULT_SCENE = "Rainbow"

LOOK_FAN = "Fan"
LOOK_TENSION = "Tension"
LOOK_ARMED = "Armed"
LOOK_PINGPONG = "Ping-Pong"
LOOK_DARK = "Dark"
DYNAMIC_LOOKS = (LOOK_FAN, LOOK_TENSION, LOOK_ARMED, LOOK_PINGPONG, LOOK_DARK)
LOOKS = tuple(LASER_SCENES) + DYNAMIC_LOOKS

MODE_FOLLOW = "follow"
MODE_ROTATE = "rotate"
MODE_HOLD = "hold"
MODES = (MODE_FOLLOW, MODE_ROTATE, MODE_HOLD)
DEFAULT_MODE = MODE_FOLLOW

ROTATE_BARS = 16             # rotate mode: advance one scene every N bars on the beat grid
ROTATE_FALLBACK_S = 30.0     # ...or every N seconds when Pro DJ Link is not fresh
ACCENT_BARS = 2.0            # drop accent length (rotate / hold modes)
ACCENT_FLASHES_PER_BEAT = 2  # CH1 chop rate: 8th notes
FAN_BARS = 8                 # follow mode: the Fan holds this long from the drop hit
PEAK_BLOCK_BARS = 8          # follow mode: Ping-Pong lights both sides on these block boundaries
TENSION_RAMP_BARS = 16.0     # a build reaches full sweep speed after this many bars...
TENSION_RISE_FULL = 2.0      # ...or when the rise index reaches this ratio, whichever first
FAN_BASE = 0.5               # the Fan / Ping-Pong sweep idles here, the wub adds on top
ARMED_CHOPS_PER_BEAT = 1     # Armed chops on the beat...
ARMED_ROLL_CHOPS_PER_BEAT = 2  # ...and on 8th notes when a snare roll armed the drop

MIN_DWELL_BARS = 2           # follow mode: a look holds at least this long before the bucket can change it
                             # (except INTO Fan on the drop hit and INTO/OUT OF Dark) -- replay showed
                             # DROP<->BUILD<->BREAK flaps swapping looks three times in 6 s
FALLBACK_GRACE_S = 8.0       # follow mode: hold the last look this long through a grid/audio blip
                             # before falling back to rotation (replay: 5 s gaps mid-track)

MOOD_SHOW = "show"
MOOD_ACCENT = "accent"

_HOT = (EnergyBucket.DROP, EnergyBucket.PEAK)
_LINE = (60, 118, 0, 0, 0, 0)   # CH1..CH6 of the verified dotted line
_SWEEP_MIN, _SWEEP_MAX = Laser.SWEEP_SPEED_MIN, Laser.SWEEP_SPEED_MAX


def scene_names() -> list[str]:
    """Every static scene in the library (the Laser Show tab's buttons)."""
    return list(LASER_SCENES)


def rotation_scene_names() -> list[str]:
    """What rotate mode steps through: only the scenes SEEN on the unit (Sophia's four), so a
    scene composed from the manual and not yet watched never comes up on its own at a set."""
    from .laser_scenes import library
    return [sc.name for sc in library() if sc.status == "seen"] or list(LASER_SCENES)


def look_names() -> list[str]:
    """Everything the hold mode can show: static scenes + the dynamic looks."""
    return list(LOOKS)


def _sweep_byte(x: float) -> int:
    x = max(0.0, min(1.0, x))
    return int(_SWEEP_MIN + x * (_SWEEP_MAX - _SWEEP_MIN))


def _line(sweep: float, color: int) -> tuple[int, ...]:
    """The verified dotted-line sweep as a 12-channel scene."""
    return _LINE + (_sweep_byte(sweep), 71, Laser.SCALE_STATIC, 0, Laser.SCAN_DOTS, color)


class LaserShowEngine:
    """Stateful (mode bookkeeping, rotation origin, accent window, envelopes);
    tick once per ShowEngine frame."""

    priority = 60

    def __init__(self, clock: Clock, manual: ManualParams | None = None) -> None:
        self.clock = clock
        self.manual = manual if manual is not None else ManualParams()
        self._wub = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self._names = rotation_scene_names()
        self._selected = ""              # the last ManualParams.laser_scene we saw
        self._origin_bar: int | None = None
        self._origin_t = 0.0
        self._steps = 0                  # rotation steps taken since the origin
        self._prev_bucket: EnergyBucket | None = None
        self._accent_until = 0.0
        self._accent_start = 0.0
        self._accent_bpm = 120.0
        self._build_start_bar: int | None = None
        self._drop_entry_bar: int | None = None   # the bar the bucket ENTERED DROP/PEAK (not drop_start_bar,
                                                  # which is the bar the rise was first seen, 2-3 bars earlier)
        self._peak_seen = False                   # PEAK reached since the drop entry: Ping-Pong is sticky
        self._look_since_bar: int | None = None
        self._last_fresh_t = -1e9
        self._chop_origin = 0.0          # when the current Armed chop started
        # the shared laser-engine interface
        self.active = False
        self.primed = False
        self.sweep = 0.0
        self.target = 0.0
        self.source = ""
        self.stab = 0.0
        self.wub = 0.0
        self.color = self.color_l = self.color_r = 0
        self.mood = MOOD_SHOW
        self.mode = DEFAULT_MODE
        self.scene_name = DEFAULT_SCENE
        self.scene_index = 0
        self.reason = ""
        self.rotating = False
        self.accent = False
        self.bars_to_next: int | None = None

    @property
    def manual_cued(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "laser"

    # ---------------------------------------------------------------- helpers
    def _bar_s(self, snap: ShowSnapshot) -> float:
        return 4.0 * (60.0 / snap.bpm if snap.bpm > 0 else 0.5)

    def _beat_s(self, snap: ShowSnapshot) -> float:
        return 60.0 / snap.bpm if snap.bpm > 0 else 0.5

    def _restart(self, snap: ShowSnapshot, now: float) -> None:
        self._origin_bar = snap.bar if snap.pdl_fresh else None
        self._origin_t = now
        self._steps = 0

    def _advance(self, snap: ShowSnapshot, now: float) -> None:
        """Bump `_steps` when a rotation boundary passes: every ROTATE_BARS
        bars on the beat grid while Pro DJ Link is fresh, else every
        ROTATE_FALLBACK_S of wall-clock. A bar counter that goes backwards
        (a new track) just re-anchors."""
        if snap.pdl_fresh:
            if self._origin_bar is None or snap.bar < self._origin_bar:
                self._origin_bar = snap.bar
                self._origin_t = now
            elif snap.bar - self._origin_bar >= ROTATE_BARS:
                self._steps += 1
                self._origin_bar = snap.bar
                self._origin_t = now
            self.bars_to_next = ROTATE_BARS - (snap.bar - self._origin_bar)
        else:
            self._origin_bar = None
            if now - self._origin_t >= ROTATE_FALLBACK_S:
                self._steps += 1
                self._origin_t = now
            self.bars_to_next = None

    def _chop(self, now: float, period: float) -> bool:
        """True for the first half of each `period` since the chop began."""
        if period <= 0:
            return True
        phase = ((now - self._chop_origin) % period) / period
        return phase < 0.5

    # ---------------------------------------------------------------- looks
    def _follow_look(self, snap: ShowSnapshot) -> tuple[str, str]:
        """Follow mode: (look, reason) from the existing detection."""
        bucket = EnergyBucket(snap.bucket)
        if snap.drop_pending:
            return LOOK_DARK, "drop lands on the next downbeat"
        # the fan holds its full window from the drop hit even if the bucket
        # flaps out of DROP for a bar or two (replay: 2- and 6-bar fans)
        if self._drop_entry_bar is not None and 0 <= snap.bar - self._drop_entry_bar < FAN_BARS:
            bars_in = snap.bar - self._drop_entry_bar
            return LOOK_FAN, f"{bucket.value} · fan bar {bars_in + 1} of {FAN_BARS}"
        if bucket in _HOT:
            if bucket == EnergyBucket.PEAK or self._peak_seen:
                return LOOK_PINGPONG, "peak" if bucket == EnergyBucket.PEAK else "drop (after peak)"
            return DEFAULT_SCENE, "drop"
        if bucket in (EnergyBucket.BREAK, EnergyBucket.OUTRO):
            return "Slow Rainbow", bucket.value
        if bucket == EnergyBucket.BUILD:
            if snap.drop_armed:
                return LOOK_ARMED, f"build · drop armed ({snap.drop_arm_source or 'D'})"
            return LOOK_TENSION, "build"
        return DEFAULT_SCENE, bucket.value

    def _dwell_ok(self, look: str, snap: ShowSnapshot) -> bool:
        """May the follow look change to `look` now? Immediate into Fan (the
        drop hit), into or out of Dark; otherwise only after MIN_DWELL_BARS."""
        if look == self.scene_name:
            return True
        if look in (LOOK_FAN, LOOK_DARK) or self.scene_name == LOOK_DARK:
            return True
        if self._look_since_bar is None or not snap.pdl_fresh:
            return True
        return abs(snap.bar - self._look_since_bar) >= MIN_DWELL_BARS

    def _render(self, look: str, snap: ShowSnapshot, now: float, n: int) -> list[tuple[tuple[int, ...], bool]]:
        """Per-laser (bytes, lit) for a look. Index 0 = the first laser in
        the rig (L), 1 = the second (R); more lasers alternate by parity."""
        if look in LASER_SCENES:
            return [(LASER_SCENES[look], True)] * n
        if look == LOOK_DARK:
            return [(LASER_SCENES[DEFAULT_SCENE], False)] * n
        if look == LOOK_FAN:
            color = laser_color_for_palette(snap.palette)
            return [(_line(FAN_BASE + (1.0 - FAN_BASE) * self.wub, color), True)] * n
        if look in (LOOK_TENSION, LOOK_ARMED):
            bars_in = 0.0
            if self._build_start_bar is not None and snap.pdl_fresh:
                bars_in = max(0.0, float(snap.bar - self._build_start_bar))
            ramp_t = bars_in / TENSION_RAMP_BARS
            ramp_r = (float(snap.rise_index) - 1.0) / (TENSION_RISE_FULL - 1.0)
            ramp = max(0.0, min(1.0, max(ramp_t, ramp_r)))
            values = _line(ramp, Laser.COLOR_BLUE)
            lit = True
            if look == LOOK_ARMED:
                per_beat = ARMED_ROLL_CHOPS_PER_BEAT if snap.drop_arm_source == "roll" else ARMED_CHOPS_PER_BEAT
                lit = self._chop(now, self._beat_s(snap) / per_beat)
            return [(values, lit)] * n
        if look == LOOK_PINGPONG:
            origin = self._drop_entry_bar if self._drop_entry_bar is not None else 0
            bars_in = max(0, snap.bar - origin)
            both = bars_in % PEAK_BLOCK_BARS == 0
            sweep = FAN_BASE + (1.0 - FAN_BASE) * self.wub
            out = []
            for i in range(n):
                if n == 1:
                    # single-laser rig: nothing to alternate against, so the
                    # one head stays lit and swaps blue/red per bar instead
                    color = Laser.COLOR_BLUE if bars_in % 2 == 0 else Laser.COLOR_RED
                    lit = True
                else:
                    color = Laser.COLOR_BLUE if i % 2 == 0 else Laser.COLOR_RED
                    lit = both or (bars_in % 2 == i % 2)
                out.append((_line(sweep, color), lit))
            return out
        return [(LASER_SCENES[DEFAULT_SCENE], True)] * n

    # ---------------------------------------------------------------- tick
    def tick(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        c = Contribution()
        now = self.clock.monotonic()
        m = self.manual
        self.wub = self._wub.update(snap.audio_band_low_abs, snap.audio_fresh)
        bucket = EnergyBucket(snap.bucket)
        if bucket == EnergyBucket.BUILD and self._prev_bucket != EnergyBucket.BUILD:
            self._build_start_bar = snap.bar
        entered_drop = (bucket == EnergyBucket.DROP and self._prev_bucket is not None
                        and self._prev_bucket not in _HOT)
        if bucket in _HOT and (self._prev_bucket not in _HOT or self._drop_entry_bar is None):
            # a re-entry inside the previous fan window is the same drop (the
            # bucket flapped DROP->BUILD->DROP in replay); keep its origin
            if self._drop_entry_bar is None or not (0 <= snap.bar - self._drop_entry_bar < FAN_BARS):
                self._drop_entry_bar = snap.bar
                self._peak_seen = False
        if bucket == EnergyBucket.PEAK:
            self._peak_seen = True
        fresh = snap.pdl_fresh and snap.audio_fresh
        if fresh:
            self._last_fresh_t = now

        selected = str(getattr(m, "laser_scene", DEFAULT_SCENE) or DEFAULT_SCENE)
        if selected not in LOOKS:
            selected = DEFAULT_SCENE
        if selected != self._selected:
            self._selected = selected
            self._restart(snap, now)
        mode = str(getattr(m, "laser_mode", DEFAULT_MODE))
        if mode not in MODES:
            mode = DEFAULT_MODE
        hold_last = False
        if mode == MODE_FOLLOW and not fresh:
            if now - self._last_fresh_t < FALLBACK_GRACE_S:
                effective = MODE_FOLLOW      # a blip: hold the last look
                hold_last = True
            else:
                effective = MODE_ROTATE      # no grid / no audio: keep moving on the clock
            self.reason = "no beat grid or audio"
        else:
            effective = mode
            self.reason = ""
        self.mode = mode

        if effective == MODE_FOLLOW:
            self.rotating = False
            self.bars_to_next = None
            self._restart(snap, now)
            if hold_last:
                look, self.reason = self.scene_name, "signal blip — holding"
            else:
                look, reason = self._follow_look(snap)
                if self._dwell_ok(look, snap):
                    self.reason = reason
                else:
                    look = self.scene_name
                    self.reason = f"{self.reason or reason} (holding {MIN_DWELL_BARS} bars)"
            if look != self.scene_name:
                self._look_since_bar = snap.bar
        elif effective == MODE_ROTATE:
            self.rotating = True
            self._advance(snap, now)
            base = self._names.index(selected) if selected in self._names else 0
            look = self._names[(base + self._steps) % len(self._names)]
        else:
            self.rotating = False
            self.bars_to_next = None
            self._restart(snap, now)
            look = selected
            self.reason = "manual hold"

        # --- drop accent (rotate / hold only; follow has the Fan)
        if (effective != MODE_FOLLOW and bool(getattr(m, "laser_drop_accent", True))
                and snap.pdl_fresh and snap.audio_fresh and entered_drop):
            self._accent_start = now
            self._accent_bpm = snap.bpm if snap.bpm > 0 else 120.0
            self._accent_until = now + ACCENT_BARS * self._bar_s(snap)
        self.accent = effective != MODE_FOLLOW and now < self._accent_until
        if look != LOOK_ARMED or self.scene_name != LOOK_ARMED:
            self._chop_origin = now              # a fresh chop starts lit
        self._prev_bucket = bucket

        self.scene_name = look
        self.scene_index = LOOKS.index(look)
        lasers = rig.by_role("laser")
        rendered = self._render(look, snap, now, max(1, len(lasers)))
        if self.accent:
            period = self._beat_s(snap) / ACCENT_FLASHES_PER_BEAT
            phase = ((now - self._accent_start) % period) / period
            if phase >= 0.5:
                rendered = [(values, False) for values, _ in rendered]

        self.active = True                       # the gate never keeps them dark here
        self.primed = not any(lit for _, lit in rendered)
        self.source = "show"
        self.mood = MOOD_ACCENT if self.accent else MOOD_SHOW
        colors = [values[11] for values, _ in rendered]
        self.color = self.color_l = colors[0]
        self.color_r = colors[1] if len(colors) > 1 else colors[0]
        ch7 = rendered[0][0][6]
        self.sweep = self.target = (ch7 - _SWEEP_MIN) / float(_SWEEP_MAX - _SWEEP_MIN) if ch7 >= _SWEEP_MIN else 0.0
        for fx, (values, lit) in zip(lasers, rendered):
            fx.set_scene(c, values, lit=lit)
        return c
