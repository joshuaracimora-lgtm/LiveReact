"""Fixture profiles: translate semantic intent (intensity, color, position, strobe)
to/from DMX channels, and own the per-fixture boot defaults + safety clamps.

Channel maps are reproduced from docs/04 §3 and are "verified on Josh's unit" only
— treat them as hypotheses to confirm on hardware via the fixture-test panel
(Phase 7). They live here as data, never hard-coded into engine/scene logic.

Coordinate conventions used across the app:
  - intensity / color components / pan / tilt / strobe-rate are floats in [0, 1].
  - DMX channel addresses are 1-based (matching the rig docs); the universe buffer
    is 0-based, so channel N lives at index N-1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .geometry import HeadCalibration, Point3


class ChannelKind(IntEnum):
    """How the merge layer should combine a channel across engine contributions."""

    OTHER = 0       # last-write-wins, no special handling
    DIMMER = 1      # max across contributions
    COLOR = 2       # max across contributions
    MOVEMENT = 3    # last-write-wins (averaging pan/tilt would swing through center)
    STROBE = 4      # max, then rate-capped at finalize
    LASER = 5       # gated off unless explicitly enabled at finalize


@dataclass(frozen=True)
class VisualState:
    """What the visualizer needs to draw one fixture, derived from live channels."""

    label: str
    role: str
    side: str
    r: int          # 0-255 base color (before brightness)
    g: int
    b: int
    brightness: float  # 0..1
    pan: float         # 0..1 (0.5 = center)
    tilt: float        # 0..1
    active: bool
    strobe: float = 0.0  # 0..1, the fixture's native strobe-rate channel if it has one
                         # (2026-09-15: the recorder could only INFER strobe activity
                         # from brightness sitting at the cap byte before this)


def _clamp8(value: float) -> int:
    """Clamp a number to a valid DMX byte (0-255)."""
    return 0 if value <= 0 else 255 if value >= 255 else int(value)


def _to8(level01: float) -> int:
    """Map a 0..1 level to a 0-255 DMX byte."""
    return _clamp8(round(level01 * 255))


def _to16_int(level01: float) -> int:
    """Map a 0..1 level to a 16-bit DMX count (0..65535)."""
    v = round(level01 * 65535)
    return 0 if v <= 0 else 65535 if v >= 65535 else v


def _to16(level01: float) -> tuple[int, int]:
    """Map a 0..1 level to a (coarse, fine) DMX byte pair."""
    v = _to16_int(level01)
    return v >> 8, v & 0xFF


def _read16(uni, i: int) -> float:
    """Read a (coarse, fine) byte pair at ``i``/``i+1`` back to 0..1."""
    return ((uni[i] << 8) | uni[i + 1]) / 65535


# Dimmer/intensity-specific curve (LIGHTING_BIBLE.md §3.7, [VERIFIED]): budget
# LED pars shift hue and show visible discrete "steps" on a raw linear fade
# below ~10-15% output, so a straight `_to8` (used everywhere else — color,
# position, strobe, scale) is the wrong mapping specifically for dimmer
# channels. Two things, both from the Bible, not guessed:
#   - gamma/square-law curve (perceived brightness relates to measured output
#     by roughly a square law: ~1% measured looks like ~10% perceived), so a
#     linear fade dumps nearly all visible change into the bottom of the
#     range; `output = 255 * level^2.2` spreads it evenly instead.
#   - a hard floor at 12% (DMX 31): never dwell in the DMX 1-30 "stepping
#     zone" — any level intent above true zero (however dim) still shows at
#     AT LEAST the floor, and only a genuine zero intent produces true 0, so a
#     fade passing near zero cuts straight to black instead of crawling
#     visibly through the broken low range.
DIMMER_GAMMA = 2.2
DIMMER_FLOOR_DMX = 31  # 12% of 255


def _intensity_to8(level01: float) -> int:
    """Map a 0..1 intensity to a 0-255 DMX byte via the gamma+floor curve
    above. Use this (not `_to8`) for every fixture's actual dimmer channel."""
    if level01 <= 0.0:
        return 0
    curved = 255.0 * (min(1.0, level01) ** DIMMER_GAMMA)
    return max(DIMMER_FLOOR_DMX, _clamp8(round(curved)))


def _bias_factors(bias: float) -> tuple[float, float]:
    """(red_factor, blue_factor) for a warm/cool bias in [-1, +1].

    bias < 0 = warm (boost red, cut blue); bias > 0 = cool (cut red, boost blue).
    """
    warmth = -bias
    return (1.0 + 0.5 * warmth, 1.0 - 0.5 * warmth)


# Approximate color-wheel slot -> RGB for moving heads. The SHEHDS wheel slot
# boundaries are not documented in the manuals we have, so this is a reasonable
# default to be corrected on hardware (assumption A1). Used only for display and
# as a coarse target when a scene asks an MH for an RGB-ish color.
_MH_COLOR_WHEEL = [
    (0, (255, 255, 255)),    # open / white
    (8, (255, 0, 0)),        # red
    (16, (255, 120, 0)),     # orange/amber
    (24, (255, 255, 0)),     # yellow
    (32, (0, 255, 0)),       # green
    (40, (0, 200, 255)),     # cyan
    (48, (0, 0, 255)),       # blue
    (56, (255, 0, 255)),     # magenta/pink
]


def color_wheel_to_rgb(value: int) -> tuple[int, int, int]:
    """Map a moving-head color-wheel channel value to an approximate RGB."""
    chosen = _MH_COLOR_WHEEL[0][1]
    for threshold, rgb in _MH_COLOR_WHEEL:
        if value >= threshold:
            chosen = rgb
        else:
            break
    return chosen


def rgb_to_color_wheel(r: int, g: int, b: int) -> int:
    """Pick the nearest color-wheel slot value for a target RGB (coarse)."""
    best_value, best_dist = 0, 1 << 30
    for value, (cr, cg, cb) in _MH_COLOR_WHEEL:
        dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
        if dist < best_dist:
            best_value, best_dist = value, dist
    return best_value


class Buffer:
    """Minimal write-target protocol shared by ``Contribution`` and bare universes.

    Fixtures write through ``set(channel_1based, value)`` so the same semantic
    setters work whether an engine is building a sparse contribution or code is
    assembling a boot universe directly.
    """

    def set(self, channel_1based: int, value: int) -> None:  # pragma: no cover
        raise NotImplementedError


class Fixture:
    """Base class. Subclasses declare their channel count + kinds and override the
    semantic setters they support; unsupported ones are no-ops."""

    type_name: str = "generic"
    channel_count: int = 0

    def __init__(self, address: int, label: str, role: str, side: str) -> None:
        self.address = address  # 1-based DMX start address
        self.label = label
        self.role = role        # 'spot' | 'wash' | 'laser'
        self.side = side        # 'left' | 'right'
        # Optional custom position on the virtual rig canvas (normalized 0..1,
        # the fixture's on-screen center). None -> the visualizer's default
        # role/side layout. Set by the rig loader from a slot's "x"/"y", or
        # live by dragging the fixture in the console's Show tab, so an
        # operator can arrange the virtual rig to match a real venue's
        # physical layout per event.
        self.layout_x: float | None = None
        self.layout_y: float | None = None
        # Calibrated "facing the stage" pan/tilt (normalized 0..1), docs/14 §4.
        # None -> apply_home_position() is a no-op, keeping the old raw
        # 0.5-centered sweep behavior until a fixture is calibrated. Set by the
        # rig loader from a slot's "home_pan"/"home_tilt", or via the
        # fixture-test panel's "Save as Home Position" button.
        self.home_pan: float | None = None
        self.home_tilt: float | None = None
        # Real-world placement (2026-09-18 venue floor plan, fixtures/
        # geometry.py's convention: meters, origin at the booth wall's
        # stage-left corner, x to stage-right, y into the crowd, z up).
        # None -> not placed; the floor plan then falls back to the old
        # layout_x/layout_y canvas position or the role/side schematic.
        # `facing_deg` is the yaw of the body's pan=0.5 direction (0 = into
        # the crowd). `x_norm` (LIGHTING_BIBLE.md §1.2) is DERIVED by
        # Rig.refresh_geometry() from position + venue width; None without a
        # venue. `extra` keeps any rig-file keys the loader doesn't model
        # (e.g. a rig file's free-text notes) so a save round-trips them.
        self.position: Point3 | None = None
        self.facing_deg: float = 0.0
        self.x_norm: float | None = None
        self.extra: dict = {}

    # --- geometry ---
    @property
    def offset(self) -> int:
        """0-based index of this fixture's first channel in the universe buffer."""
        return self.address - 1

    def channels(self) -> range:
        """1-based DMX channels this fixture occupies."""
        return range(self.address, self.address + self.channel_count)

    def channel_kinds(self) -> list[ChannelKind]:
        """Per-local-channel kinds; length == channel_count. Override per fixture."""
        return [ChannelKind.OTHER] * self.channel_count

    def capabilities(self) -> set[str]:
        """Capability tokens for LIGHTING_BIBLE.md's `requires{}` look-gating
        (`LOOK-01`, Bible-integration Phase 1 -- docs/bible_integration/,
        /Users/joshuaracimora/.claude/plans/adaptive-squishing-pebble.md).
        Derived from `channel_kinds()` -- not new hardware data, just making
        an existing de facto signal explicitly queryable. Override per
        fixture only where this generic derivation would be misleading (e.g.
        a wheel-based color slot isn't true continuous RGB -- see
        `MovingHead.capabilities()`)."""
        kinds = set(self.channel_kinds())
        caps: set[str] = set()
        if ChannelKind.MOVEMENT in kinds:
            caps.add("pan_tilt")
        if ChannelKind.STROBE in kinds:
            caps.add("native_strobe_channel")
        if ChannelKind.LASER in kinds:
            caps.add("laser")
        if ChannelKind.COLOR in kinds:
            caps.add("rgb")
        return caps

    # --- helpers ---
    def _set(self, buf: Buffer, local_1based: int, value: int) -> None:
        buf.set(self.address + local_1based - 1, value)

    # --- boot ---
    def boot_defaults(self, buf: Buffer) -> None:
        """Write the fixture's safe power-on state. Override as needed."""

    # --- semantic setters (override what the fixture supports) ---
    def set_intensity(self, buf: Buffer, level01: float) -> None: ...
    def set_color(self, buf: Buffer, r: float, g: float, b: float, w: float = 0.0) -> None: ...
    def set_position(self, buf: Buffer, pan01: float, tilt01: float) -> None: ...
    def set_strobe(self, buf: Buffer, rate01: float) -> None: ...

    # --- finalize-stage clamps (operate on the assembled universe bytearray) ---
    def apply_master_brightness(self, uni: bytearray, factor01: float) -> None: ...
    def cap_strobe(self, uni: bytearray, cap01: float) -> None: ...
    def cap_brightness_while_strobing(self, uni: bytearray) -> None: ...
    def apply_safety(self, uni: bytearray, laser_enabled: bool) -> None: ...
    def apply_color_bias(self, uni: bytearray, bias: float) -> None: ...
    def apply_strobe_burst(self, uni: bytearray, cap01: float) -> None: ...
    def apply_home_position(self, uni: bytearray, bound: float = 0.20, absolute: bool = False) -> None: ...
    def apply_slew_limit(self, uni: bytearray, dt: float) -> None: ...
    def apply_laser_gate(self, uni: bytearray, laser_on: bool) -> None: ...

    # --- visualization ---
    def visual_state(self, uni: bytes) -> VisualState:  # pragma: no cover - overridden
        return VisualState(self.label, self.role, self.side, 0, 0, 0, 0.0, 0.5, 0.5, False)


@dataclass(frozen=True)
class MotionBounds:
    """A moving head's allowed RAW pan/tilt box (0..1), 2026-10-10 (Junior
    Crib, Josh: "I need the moving head's pan to bounce in between those
    [edge] points -- if it goes outside of those bounds people will not be
    able to see the lights"). Captured on the Venue tab by jogging the real
    beam onto each edge and pressing ADD BOUND POINT; the box is the envelope
    of every captured point. Enforced at finalize for EVERY scene, no
    geometry model involved: relative scene motion is remapped to fill the
    box, computed aims are clamped into it."""

    pan_min: float
    pan_max: float
    tilt_min: float
    tilt_max: float

    @staticmethod
    def point(pan01: float, tilt01: float) -> "MotionBounds":
        return MotionBounds(pan01, pan01, tilt01, tilt01)

    def include(self, pan01: float, tilt01: float) -> "MotionBounds":
        return MotionBounds(min(self.pan_min, pan01), max(self.pan_max, pan01),
                            min(self.tilt_min, tilt01), max(self.tilt_max, tilt01))

    def remap(self, pan01: float, tilt01: float) -> tuple[float, float]:
        """Raw 0..1 scene motion -> inside the box (0 = one edge, 1 = the other)."""
        return (self.pan_min + max(0.0, min(1.0, pan01)) * (self.pan_max - self.pan_min),
                self.tilt_min + max(0.0, min(1.0, tilt01)) * (self.tilt_max - self.tilt_min))

    def clamp(self, pan01: float, tilt01: float) -> tuple[float, float]:
        return (max(self.pan_min, min(self.pan_max, pan01)),
                max(self.tilt_min, min(self.tilt_max, tilt01)))


class MovingHead(Fixture):
    """SHEHDS 180W Beam Moving Head — 15CH (docs/04 §3.1).

    1 Pan · 2 Pan-fine · 3 Tilt · 4 Tilt-fine · 5 Speed · 6 Dimmer · 7 Strobe ·
    8 Color wheel · 9 Gobo · 10 Prism · 11 Focus · 12 Frost · 13 Macro(keep 0) ·
    14 Macro-speed · 15 Reset.
    """

    type_name = "shehds_180w_mh"
    channel_count = 15

    # Slew-rate limits (LIGHTING_BIBLE.md §6.6) — PLACEHOLDER values copied
    # from the Bible's own reference-rig example, NOT measured against this
    # specific SHEHDS 180W unit's real motor speed. The exact numbers matter
    # less than having SOME clamp at all: `[VERIFIED]` per the Bible, instant
    # dead-stop-to-full-speed jumps (e.g. a scene switch commanding a big
    # position change in one tick) produce visible stepping and motor stress
    # on real moving-head hardware. Verify/tune these against real hardware
    # before trusting them as final — same "measure, don't guess" rule as
    # everywhere else in this project.
    PAN_RANGE_DEG = 540.0
    TILT_RANGE_DEG = 270.0
    PAN_MAX_DEG_PER_S = 260.0
    TILT_MAX_DEG_PER_S = 200.0

    # 16-bit pan/tilt (2026-09-18, venue aiming): CH2/CH4 are the fine bytes
    # in the 15CH map above but were never written before -- 8-bit pan over
    # 540 deg is ~2.1 deg per step, too coarse to land a computed aim. Every
    # pan/tilt read/write below goes through _read16/_write16 so the home
    # remap and the slew limiter keep the fine resolution too. UNVERIFIED on
    # hardware whether this unit honours the fine bytes in 15CH mode: if it
    # ignores or jitters on them, flip this and the fine bytes are held at 0
    # (the coarse byte is identical either way).
    USE_FINE_CHANNELS = True

    def __init__(self, address: int, label: str, role: str, side: str) -> None:
        super().__init__(address, label, role, side)
        self._prev_pan_dmx: int | None = None
        self._prev_tilt_dmx: int | None = None
        # One-point aim capture (fixtures/geometry.py): the pan/tilt offsets
        # that make the trig model agree with where this head really points.
        # Uncaptured (default) -> named-target aiming falls back to each
        # scene's old hardcoded guess.
        self.calibration = HeadCalibration()
        # Raw motion box (MotionBounds, 2026-10-10): when set it REPLACES the
        # home_pan/home_tilt +-bound remap and also clamps computed aims.
        self.bounds: MotionBounds | None = None

    def channel_kinds(self) -> list[ChannelKind]:
        K = ChannelKind
        return [
            K.MOVEMENT, K.MOVEMENT, K.MOVEMENT, K.MOVEMENT,  # 1-4 pan/pan-fine/tilt/tilt-fine
            K.OTHER,                                          # 5 speed
            K.DIMMER, K.STROBE, K.COLOR,                      # 6-8 dimmer/strobe/color wheel
            K.OTHER, K.OTHER, K.OTHER, K.OTHER,               # 9-12 gobo/prism/focus/frost
            K.OTHER, K.OTHER, K.OTHER,                        # 13-15 macro/macro-speed/reset
        ]

    def capabilities(self) -> set[str]:
        # Override the base's generic derivation: CH8 is tagged ChannelKind.COLOR
        # for merge purposes (a known mistagging, MERGE-06) but it's a discrete
        # color-wheel slot, not continuous RGB mixing -- "rgb" would be
        # misleading for requires{} gating (e.g. a look needing real RGB
        # blending shouldn't be satisfied by a wheel fixture).
        return {"pan_tilt", "native_strobe_channel", "wheel_discrete"}

    def boot_defaults(self, buf: Buffer) -> None:
        # Raw CENTRE, deliberately not the calibrated home: apply_home_position
        # remaps raw 0.5 onto home at finalize, so an untouched pan already
        # boots to home through that path (writing home here would apply twice).
        self._set(buf, 1, 128)   # pan center
        self._set(buf, 2, 0)     # pan fine
        self._set(buf, 3, 80)    # tilt angled down
        self._set(buf, 4, 0)     # tilt fine
        self._set(buf, 5, 128)   # movement speed mid
        self._set(buf, 6, 0)     # dimmer off
        self._set(buf, 13, 0)    # macro off (drive via DMX, not built-in macros)

    def set_intensity(self, buf: Buffer, level01: float) -> None:
        self._set(buf, 6, _intensity_to8(level01))

    def set_color(self, buf: Buffer, r: float, g: float, b: float, w: float = 0.0) -> None:
        slot = rgb_to_color_wheel(_to8(r), _to8(g), _to8(b))
        self._set(buf, 8, slot)

    def set_position(self, buf: Buffer, pan01: float, tilt01: float) -> None:
        hi, lo = _to16(pan01)
        self._set(buf, 1, hi)
        self._set(buf, 2, lo if self.USE_FINE_CHANNELS else 0)
        hi, lo = _to16(tilt01)
        self._set(buf, 3, hi)
        self._set(buf, 4, lo if self.USE_FINE_CHANNELS else 0)

    def set_aim(self, buf: Buffer, pan01: float, tilt01: float) -> None:
        """A FINAL pan/tilt (a computed aim at a real target, fixtures/
        geometry.py), not a scene's raw 0.5-centred motion: marks the four
        movement channels so finalize skips apply_home_position's +-10%
        remap for them (a named target can be 90 deg from home). Slew
        limiting still applies. Buffers without an `absolute_movement` set
        (plain test buffers) just get the position."""
        self.set_position(buf, pan01, tilt01)
        marks = getattr(buf, "absolute_movement", None)
        if marks is not None:
            marks.update(range(self.offset, self.offset + 4))

    def set_strobe(self, buf: Buffer, rate01: float) -> None:
        self._set(buf, 7, _to8(rate01))

    def apply_master_brightness(self, uni: bytearray, factor01: float) -> None:
        i = self.offset + 5  # ch6 dimmer
        uni[i] = _clamp8(uni[i] * factor01)

    def cap_strobe(self, uni: bytearray, cap01: float) -> None:
        i = self.offset + 6  # ch7 strobe
        uni[i] = min(uni[i], _to8(cap01))

    def apply_strobe_burst(self, uni: bytearray, cap01: float) -> None:
        uni[self.offset + 6] = _to8(cap01)  # ch7 strobe at the capped rate

    def apply_home_position(self, uni: bytearray, bound: float = 0.20, absolute: bool = False) -> None:
        """Remap whatever raw pan/tilt a scene/engine set (all of them assume a
        0.5-centered convention) around this fixture's calibrated "facing the
        stage" position, scaled into a bounded range — docs/14 §4. A no-op
        until calibrated (home_pan/home_tilt are None), so uncalibrated rigs
        keep the old raw sweep behavior.

        Deliberately a finalize-stage transform, not a rewrite of every
        scene's position math: it preserves each scene's relative motion
        *shape* (PeakFullRig's slow fan, PeakBeamSweep's fast triangle
        sweep, etc.) while re-centering and re-scaling the *output* uniformly.

        ``absolute`` (2026-09-18): the winning contribution marked this
        fixture's movement as a final aim (see set_aim) -- pass it through.
        """
        if self.bounds is not None:
            # 2026-10-10 motion box: scenes' raw 0..1 motion fills the box;
            # a computed (absolute) aim is clamped into it so no look can
            # ever leave the captured edges.
            raw_pan = _read16(uni, self.offset + 0)
            raw_tilt = _read16(uni, self.offset + 2)
            new_pan, new_tilt = (self.bounds.clamp(raw_pan, raw_tilt) if absolute
                                 else self.bounds.remap(raw_pan, raw_tilt))
            self._write16(uni, self.offset + 0, _to16_int(max(0.0, min(1.0, new_pan))))
            self._write16(uni, self.offset + 2, _to16_int(max(0.0, min(1.0, new_tilt))))
            return
        if absolute or self.home_pan is None or self.home_tilt is None:
            return
        raw_pan = _read16(uni, self.offset + 0)
        raw_tilt = _read16(uni, self.offset + 2)
        new_pan = self.home_pan + (raw_pan - 0.5) * bound
        new_tilt = self.home_tilt + (raw_tilt - 0.5) * bound
        self._write16(uni, self.offset + 0, _to16_int(max(0.0, min(1.0, new_pan))))
        self._write16(uni, self.offset + 2, _to16_int(max(0.0, min(1.0, new_tilt))))

    def apply_slew_limit(self, uni: bytearray, dt: float) -> None:
        """Clamp the commanded pan/tilt delta per frame so a scene switch or
        crossfade can't slam the motor from a dead stop to full-range in one
        tick (LIGHTING_BIBLE.md §6.6). Large moves still complete, just ramped
        over multiple frames instead of one. Runs AFTER apply_home_position,
        so it clamps the final commanded position, not the pre-remap one.

        First tick after construction (no prior commanded value yet) passes
        the command through unclamped — there's nothing to ramp from.
        Works in 16-bit counts (the `_prev_*_dmx` references are 0..65535).
        """
        pan_i, tilt_i = self.offset + 0, self.offset + 2
        self._prev_pan_dmx = self._slew(uni, pan_i, self._prev_pan_dmx,
                                         self.PAN_MAX_DEG_PER_S, self.PAN_RANGE_DEG, dt)
        self._prev_tilt_dmx = self._slew(uni, tilt_i, self._prev_tilt_dmx,
                                          self.TILT_MAX_DEG_PER_S, self.TILT_RANGE_DEG, dt)

    def _slew(self, uni: bytearray, i: int, prev: int | None,
              max_deg_per_s: float, range_deg: float, dt: float) -> int:
        commanded = (uni[i] << 8) | uni[i + 1]
        if prev is None:
            return commanded
        max_delta = max_deg_per_s * dt / range_deg * 65535.0
        delta = max(-max_delta, min(max_delta, commanded - prev))
        new_value = max(0, min(65535, round(prev + delta)))
        self._write16(uni, i, new_value)
        return new_value

    def _write16(self, uni: bytearray, i: int, v16: int) -> None:
        uni[i] = v16 >> 8
        uni[i + 1] = (v16 & 0xFF) if self.USE_FINE_CHANNELS else 0

    def visual_state(self, uni: bytes) -> VisualState:
        dim = uni[self.offset + 5] / 255
        r, g, b = color_wheel_to_rgb(uni[self.offset + 7])
        pan = _read16(uni, self.offset + 0)
        tilt = _read16(uni, self.offset + 2)
        return VisualState(self.label, self.role, self.side, r, g, b, dim, pan, tilt, dim > 0)


class RgbwBeamHead(MovingHead):
    """XFALIGHTS 90W LED Beam Moving Head (RGBW 4-in-1, 2 deg beam) -- 15CH.

    1 Pan · 2 Pan-fine · 3 Tilt · 4 Tilt-fine · 5 Speed · 6 Dimmer · 7 Strobe ·
    8 Red · 9 Green · 10 Blue · 11 White · 12 Colour macro(keep 0) ·
    13 Macro speed · 14 Auto/sound(keep 0) · 15 Reset(keep 0).

    INFERRED, NOT FROM THE UNIT'S MANUAL (2026-10-31, AEPI rig): no 13/15CH
    chart was reachable online. The map is the 14CH chart of the same OEM's
    9/14CH "LED Mini Moving head" (CH6 dimmer+strobe combined, CH7-10 RGBW,
    CH11 colour macro, CH12 macro speed, CH13 auto/sound, CH14 reset) with
    dimmer and strobe split onto their own channels -- the only way that
    family reaches 15, and the same 1-7 layout as the SHEHDS 180W above.
    Amazon listing B0CP14BBB2: 540 pan / 180 tilt, 10 flashes/s strobe.
    Set the unit to its 15CH mode. Verify on the rig before trusting it.
    """

    type_name = "xfalights_90w_beam"

    TILT_RANGE_DEG = 180.0

    def channel_kinds(self) -> list[ChannelKind]:
        K = ChannelKind
        return [
            K.MOVEMENT, K.MOVEMENT, K.MOVEMENT, K.MOVEMENT,  # 1-4 pan/pan-fine/tilt/tilt-fine
            K.OTHER,                                          # 5 speed
            K.DIMMER, K.STROBE,                               # 6-7 dimmer/strobe
            K.COLOR, K.COLOR, K.COLOR, K.COLOR,               # 8-11 R/G/B/W
            K.OTHER, K.OTHER, K.OTHER, K.OTHER,               # 12-15 macro/macro-speed/auto/reset
        ]

    def capabilities(self) -> set[str]:
        return {"pan_tilt", "native_strobe_channel", "rgb"}

    def boot_defaults(self, buf: Buffer) -> None:
        super().boot_defaults(buf)
        self._set(buf, 3, 128)   # tilt mid: straight up on an upright (stand-mounted) unit
        for ch in (12, 14, 15):  # colour macro / auto-sound / reset: never let the unit run itself
            self._set(buf, ch, 0)

    def set_color(self, buf: Buffer, r: float, g: float, b: float, w: float = 0.0) -> None:
        self._set(buf, 8, _to8(r))
        self._set(buf, 9, _to8(g))
        self._set(buf, 10, _to8(b))
        self._set(buf, 11, _to8(w))

    def apply_color_bias(self, uni: bytearray, bias: float) -> None:
        if bias == 0.0:
            return
        rf, bf = _bias_factors(bias)
        uni[self.offset + 7] = _clamp8(uni[self.offset + 7] * rf)  # R ch8
        uni[self.offset + 9] = _clamp8(uni[self.offset + 9] * bf)  # B ch10

    def visual_state(self, uni: bytes) -> VisualState:
        dim = uni[self.offset + 5] / 255
        r, g, b, w = (uni[self.offset + k] for k in range(7, 11))
        rr, gg, bb = min(255, r + w), min(255, g + w), min(255, b + w)
        pan = _read16(uni, self.offset + 0)
        tilt = _read16(uni, self.offset + 2)
        return VisualState(self.label, self.role, self.side, rr, gg, bb, dim, pan, tilt, dim > 0)

class Laser(Fixture):
    """SHEHDS 3W Laser — 12CH, SAFETY-CRITICAL (docs/04 §3.2).

    CH1 mode: 0-41 = OFF (boot here). LiveReact only ever uses static manual
    patterns (42-83) in explicit drop scenes, never the sound-active ranges
    (168-251). All channels are forced off unless laser-enable is set at finalize.

    Channel map VERIFIED ON HARDWARE (2026-09-06) by isolated probing, cross-
    checked against the real QLC+ fixture file bundled with the app (`Shehds-
    3000mW-RGB-Laser.qxf`, author Bram Janssen — a genuine match by model name,
    NOT a web guess) — but that file's exact shape NAMES and a couple of its
    channel descriptions turned out wrong for this physical unit; only what's
    listed below was actually confirmed live, same "verify empirically, don't
    trust the manual" rule as everywhere else in this project:
      CH1  mode (0-41 off, 42-83 manual — confirmed, matches the file)
      CH2  "Pattern A 1": pattern group, changes roughly every 32 values.
           PATTERN_A1_DOTTED_LINE=118 (group ~96-127) is the only value
           mapped so far — flattens the default circle into a line. The
           file's own shape names for other groups (e.g. "Circle Part Fan")
           did NOT match what actually appeared on this unit — treat any
           other CH2 value as unmapped, not as the file's stated name.
      CH3  "Pattern A 2": sub-pattern within whichever CH2 group is selected.
           Held at 0 throughout — not otherwise explored for CH2's dotted-
           line group.
      CH7  horizontal sweep — 0-127 static position (UNTESTED as of
           2026-09-18; `set_static_line` writes it, probe it from the
           console's Venue tab and record the result here), 128-255 =
           sweep speed (confirmed). Speed now driven by audio intensity.
      CH9  "scale" per the file (claimed a plain static 0-255 zoom level) —
           WRONG. Confirmed on hardware: any nonzero value makes the pattern
           breathe/cycle size on its own (128 grows steadily, 64 shrinks
           steadily, 96 sawtooths — shrink then snap back, repeating; not a
           simple speed-around-a-center-point model either). SCALE_STATIC=0
           is the ONLY value found that holds a fixed size. Never set this
           to anything else without re-verifying live.
      CH11 "Pattern A Scan" — 0-127 scan speed (untested), 128-255 =
           "Pattern Lattice" per the file, CONFIRMED on hardware: turns the
           shape's outline into dots. SCAN_DOTS=190 verified good.
      CH12 color — confirmed, matches the file's named bands exactly
           (spot-checked: Green 32-39 in the file matched a real green output).

    The unit's 12-channel mode is the first 12 channels of that QLC+ file's
    25-channel definition (2026-10-01, read from the file): CH4 rotation
    (0-127 static angle), CH5/CH6 horizontal/vertical flip, CH8 vertical
    move (0-127 position), CH10 "painting" speed, and CH12's upper bands
    (64-127 colour auto-change speed, 128-191 line rainbow). All UNVERIFIED
    -- see PROBE_CANDIDATES below and docs/laser_findings.md. 2026-10-29: the
    manufacturer's own 12CH table was found (docs/laser_findings.md, "The
    manufacturer's manual"; every range is in fixtures/builtin/shehds_3w_laser.json
    with a confidence per range). It agrees with every SEEN row and says CH9 0-63
    is a static size (64/96/128 were all in the speed bands) -- still unseen.

    Two engines drive this fixture, picked by the rig file's `laser_profile`
    (fixtures/rig.py): engines/laser.py (accent, the home rig: one sweep per
    8-bar block in PEAK) and engines/laser_feature.py (feature, the bar: lit
    for the whole set). Both write only set_dotted_line_sweep; both sit
    behind apply_safety below (the operator's arm switch).
    """

    type_name = "shehds_3w_laser"
    channel_count = 12
    MANUAL_PATTERN_MODE = 60        # within the 42-83 manual range
    PATTERN_A1_DOTTED_LINE = 118    # CH2 — flattens the circle into a line (confirmed)
    PATTERN_A2_DEFAULT = 0          # CH3 — sub-pattern within the group; unexplored otherwise
    SCALE_STATIC = 0                # CH9 — MUST stay 0, see class docstring
    SCAN_DOTS = 190                 # CH11 — "Pattern Lattice", outline -> dots (confirmed)
    SWEEP_SPEED_MIN = 128           # CH7 — bottom of the confirmed speed sub-range
    SWEEP_SPEED_MAX = 255           # CH7 — top of the confirmed speed sub-range

    # CH12 color macros — middle of each named band in the QLC+ file, confirmed
    # for Green; the rest inferred from the same file's explicit ranges (not yet
    # individually spot-checked on hardware beyond Green and White).
    COLOR_WHITE = 11
    COLOR_RED = 19
    COLOR_YELLOW = 27
    COLOR_GREEN = 35
    COLOR_CYAN = 43
    COLOR_BLUE = 51
    COLOR_PURPLE = 59

    # Bench-probe candidates (2026-10-01), NONE verified: what the QLC+
    # 25-channel definition claims the other channels of the 12-channel mode
    # do. The feature profile wants a solid line, a curvy line, a still line
    # and colours that drift on their own; `tools/laser_probe.py --candidate
    # NAME` holds each on the bench (TESTING.md Test 6). name -> (channel,
    # value, the file's claim). A value graduates to a real constant above
    # only with a docs/laser_findings.md row saying what it actually did --
    # the file's names were wrong before ("Circle Part Fan" drew a star).
    PROBE_CANDIDATES: dict[str, tuple[int, int, str]] = {
        "solid_line":   (11, 0,   "CH11 0-127 'scan speed': the outline without the dot lattice?"),
        "curve_sub":    (3, 16,   "CH3 sub-pattern 2 of 16 inside the CH2=118 line group (sweep 0..240 step 16)"),
        "still_line":   (7, 64,   "CH7 0-127 'horizontal move position': the line held still, centred?"),
        "rotate_line":  (4, 32,   "CH4 0-127 'rotate angle': a tilted line?"),
        "raise_line":   (8, 96,   "CH8 0-127 'vertical move position': the line higher in the frame -- BEAM HEIGHT, real mount only"),
        "colour_drift": (12, 72,  "CH12 64-127 'colour auto change speed': colours cycling on their own, slowly?"),
        "rainbow_line": (12, 140, "CH12 128-191 'line rainbow colour speed': a rainbow along the line?"),
        "painting":     (10, 128, "CH10 'painting speed': the line drawn progressively?"),
    }

    def __init__(self, address: int, label: str, role: str, side: str) -> None:
        super().__init__(address, label, role, side)
        # Physical beam geometry for the floor plan (2026-09-18): this unit
        # has no pan/tilt, so where its fan goes is set by how it's mounted.
        # `beam_tilt_deg` (elevation of the fan's centre, +up), the fan's
        # half-angle, and `h_offset01` -- the CH7 0-127 static horizontal
        # position, UNVERIFIED on hardware (only the 128-255 sweep-speed half
        # is confirmed; see the class docstring). 0.5 = centred.
        self.beam_tilt_deg: float = 0.0
        self.fan_half_angle_deg: float = 30.0
        self.h_offset01: float = 0.5

    def channel_kinds(self) -> list[ChannelKind]:
        return [ChannelKind.LASER] * self.channel_count

    def boot_defaults(self, buf: Buffer) -> None:
        for ch in range(1, self.channel_count + 1):
            self._set(buf, ch, 0)  # everything off; CH1=0 is LASER OFF

    def set_dotted_line_sweep(self, buf: Buffer, color: int, sweep_intensity: float,
                              lit: bool = True) -> None:
        """The one fully-verified pattern (2026-09-06): a flattened line, its
        outline turned to dots, sweeping horizontally at a speed driven by
        real audio intensity (0..1) — "translate the audio directly," same
        principle as wash/par. `color` should be one of the COLOR_* constants.
        Only meaningful inside a drop scene / while the laser is armed.

        ``lit=False`` PRIMES the pattern: every shape/speed/color channel is
        written but CH1 stays 0 (OFF, the documented 0-41 band), so the unit
        already holds the dotted line when CH1 later goes to manual. Added
        2026-09-16 after the third live laser run: 0.24 s flashes that came
        up from an all-zero universe showed the unit's default CIRCLE (with
        dots), not the line -- the 2026-09-06 verification had only ever
        been sustained. Whether priming fixes that is itself unverified;
        the 1-bar sweep length is the fallback if the unit simply needs
        settle time after CH1 turns on."""
        self._set(buf, 1, self.MANUAL_PATTERN_MODE if lit else 0)
        self._set(buf, 2, self.PATTERN_A1_DOTTED_LINE)
        self._set(buf, 3, self.PATTERN_A2_DEFAULT)
        self._set(buf, 7, self._sweep_speed(sweep_intensity))
        self._set(buf, 9, self.SCALE_STATIC)
        self._set(buf, 11, self.SCAN_DOTS)
        self._set(buf, 12, _clamp8(color))

    def _sweep_speed(self, intensity: float) -> int:
        intensity = max(0.0, min(1.0, intensity))
        span = self.SWEEP_SPEED_MAX - self.SWEEP_SPEED_MIN
        return int(self.SWEEP_SPEED_MIN + intensity * span)

    def set_scene(self, buf: Buffer, values: "list[int] | tuple[int, ...]", lit: bool = True) -> None:
        """Write a whole 12-channel look verbatim (2026-10-08, the SHOW
        profile / engines/laser_show.py). ``values`` are the CH1..CH12 bytes
        exactly as read off a QLC+ scene; nothing is reinterpreted. With
        ``lit=False`` every channel but CH1 is written and CH1 stays 0 (the
        documented OFF band) -- the same priming trick as
        set_dotted_line_sweep. Still behind apply_safety (the arm switch)
        and apply_laser_gate like every other laser write."""
        if len(values) != self.channel_count:
            raise ValueError(f"laser scene needs {self.channel_count} values, got {len(values)}")
        for ch, value in enumerate(values, start=1):
            if ch == 1 and not lit:
                self._set(buf, 1, 0)
            else:
                self._set(buf, ch, _clamp8(int(value)))

    STATIC_POS_MAX = 127            # CH7 — top of the (UNVERIFIED) static-position sub-range

    def set_static_line(self, buf: Buffer, color: int, h_pos01: float) -> None:
        """The dotted line held STILL at a horizontal position (2026-09-18).

        UNVERIFIED ON HARDWARE: writes CH7 in its 0-127 half, which the QLC+
        fixture file calls a static horizontal position -- never probed on
        this unit (only the 128-255 sweep-speed half is confirmed; see the
        class docstring). CH7 is one byte, so a fan cannot be both statically
        positioned and sweeping: while engines/laser.py sweeps, CH7 is the
        speed and `h_offset01` is NOT applied. Used by the Venue tab's bench
        probe (needs the laser armed);
        static-line scenes could use it once the probe says what it does.
        Never writes CH7 >= 128, so a bad guess can't turn into a sweep."""
        self._set(buf, 1, self.MANUAL_PATTERN_MODE)
        self._set(buf, 2, self.PATTERN_A1_DOTTED_LINE)
        self._set(buf, 3, self.PATTERN_A2_DEFAULT)
        self._set(buf, 7, min(self.STATIC_POS_MAX, int(round(max(0.0, min(1.0, h_pos01)) * self.STATIC_POS_MAX))))
        self._set(buf, 9, self.SCALE_STATIC)
        self._set(buf, 11, self.SCAN_DOTS)
        self._set(buf, 12, _clamp8(color))

    def apply_safety(self, uni: bytearray, laser_enabled: bool) -> None:
        # Fail-safe: unless explicitly enabled this run, the entire fixture is zeroed
        # (CH1=0 => laser off). Drop-scene gating is layered on top in Phase 4.
        # (The beam-height rule that used to sit here was removed on
        # 2026-10-03 at Josh's request -- he is replacing it by hand. The arm
        # switch is the only software gate on this fixture now.)
        if not laser_enabled:
            for i in range(self.offset, self.offset + self.channel_count):
                uni[i] = 0

    def apply_laser_gate(self, uni: bytearray, laser_on: bool) -> None:
        """Additive to (not a replacement for) apply_safety above: the laser
        only shows through while engines/laser.py's LaserEngine says it is
        on (DROP/PEAK gate + fresh audio and PDL) -- so a stray laser write
        from any other layer can never light it outside a laser moment.
        Deliberately a SEPARATE method rather than folded into apply_safety,
        so the already-verified-on-hardware safety-critical arm/disarm path
        stays untouched. (Was `apply_laser_gate`, fed by the retired one-beat
        kick-onset flash, until 2026-09-15.)

        Since 2026-09-16 the gate forces ONLY CH1 (mode) to 0 -- the unit's
        documented OFF band -- and leaves the pattern channels as written,
        so the laser layer can keep the dotted line primed between sweeps
        (see set_dotted_line_sweep's ``lit``). Off is still off: CH1 in
        0-41 is the same state boot_defaults and apply_safety rely on. The
        all-channels zeroing stays where it matters most, in apply_safety
        (disarmed / unsafe geometry)."""
        if not laser_on:
            uni[self.offset] = 0

    def visual_state(self, uni: bytes) -> VisualState:
        mode = uni[self.offset + 0]
        active = mode > 41
        # CH12 is color; map coarsely through the same wheel for display.
        r, g, b = color_wheel_to_rgb(uni[self.offset + 11]) if active else (0, 0, 0)
        return VisualState(self.label, self.role, self.side, r, g, b, 1.0 if active else 0.0, 0.5, 0.5, active)


class Par(Fixture):
    """RGB Par — 4CH, VERIFIED ON HARDWARE (real fixture, 2026-08-31) by isolated
    channel probing: 1 Dimmer · 2 Red · 3 Green · 4 Blue. No White channel — a
    5th channel was tested and produced no additional effect. This replaces an
    earlier written-spec guess (R/G/B/W, no dimmer channel at all) that didn't
    match this fixture: color-only with the dimmer at 0 produced nothing, and
    dimmer-only with color at 0 also produced nothing (confirmed both ways) —
    it's a genuine two-part gate like the WashBar's, not a scale-in-software
    model."""

    type_name = "rgb_par"
    channel_count = 4

    def channel_kinds(self) -> list[ChannelKind]:
        return [ChannelKind.DIMMER, ChannelKind.COLOR, ChannelKind.COLOR, ChannelKind.COLOR]

    def boot_defaults(self, buf: Buffer) -> None:
        for ch in range(1, 5):
            self._set(buf, ch, 0)

    def set_color(self, buf: Buffer, r: float, g: float, b: float, w: float = 0.0) -> None:
        # No white channel on this fixture; fold any white intent into R/G/B.
        self._set(buf, 2, _to8(min(1.0, r + w)))
        self._set(buf, 3, _to8(min(1.0, g + w)))
        self._set(buf, 4, _to8(min(1.0, b + w)))

    def set_intensity(self, buf: Buffer, level01: float) -> None:
        self._set(buf, 1, _intensity_to8(level01))  # ch1 dimmer

    def apply_master_brightness(self, uni: bytearray, factor01: float) -> None:
        i = self.offset  # ch1 dimmer
        uni[i] = _clamp8(uni[i] * factor01)

    def apply_color_bias(self, uni: bytearray, bias: float) -> None:
        if bias == 0.0:
            return
        rf, bf = _bias_factors(bias)
        uni[self.offset + 1] = _clamp8(uni[self.offset + 1] * rf)  # R ch2
        uni[self.offset + 3] = _clamp8(uni[self.offset + 3] * bf)  # B ch4

    def visual_state(self, uni: bytes) -> VisualState:
        dim = uni[self.offset + 0] / 255
        r, g, b = (uni[self.offset + k] for k in range(1, 4))
        return VisualState(self.label, self.role, self.side, r, g, b, dim, 0.5, 0.5, dim > 0 and max(r, g, b) > 0)


class WashBar(Fixture):
    """OK-103-288WL Wash Bar — 6CH (docs/04 §3.4).
    1 Master Dimmer · 2 Strobe · 3 Red · 4 Green · 5 Blue · 6 White.

    VERIFIED ON HARDWARE (real fixture, 2026-08-31): this channel layout is
    correct, but note a real physical quirk — R/G/B light a different LED zone
    ("outer") than White ("center"). This is not a true RGBW mixing pixel;
    it's discrete single-color LED clusters along the bar. Mixed colors won't
    blend into one smooth hue the way a Par's RGBW pixel does — worth knowing
    when designing scenes/color logic for this fixture specifically."""

    type_name = "ok103_wash_bar"
    channel_count = 6

    STROBE_BRIGHTNESS_CAP_DMX = 180  # Josh's explicit call (2026-09-14, live on
        # real hardware): "around 130-150" -> 140; raised to 180 2026-09-15
        # after the fourth live test ("it can reach a higher peak ... I
        # think we can raise it to 180"). A full-white, full-brightness
        # strobe burst can blind people, so while ch2 (strobe) is actively
        # nonzero, ch1 (dimmer) is clamped to this max regardless of what the
        # engine/scene layer wanted. Purely a brightness clamp -- unrelated to
        # the strobe-RATE/Hz safety question (STROBE_MAX in engines/
        # reactive.py); this only ever lowers ch1, never touches ch2.

    def channel_kinds(self) -> list[ChannelKind]:
        K = ChannelKind
        return [K.DIMMER, K.STROBE, K.COLOR, K.COLOR, K.COLOR, K.COLOR]

    def boot_defaults(self, buf: Buffer) -> None:
        for ch in range(1, 7):
            self._set(buf, ch, 0)

    def set_intensity(self, buf: Buffer, level01: float) -> None:
        self._set(buf, 1, _intensity_to8(level01))

    def set_color(self, buf: Buffer, r: float, g: float, b: float, w: float = 0.0) -> None:
        self._set(buf, 3, _to8(r))
        self._set(buf, 4, _to8(g))
        self._set(buf, 5, _to8(b))
        self._set(buf, 6, _to8(w))

    def set_strobe(self, buf: Buffer, rate01: float) -> None:
        self._set(buf, 2, _to8(rate01))

    def apply_master_brightness(self, uni: bytearray, factor01: float) -> None:
        i = self.offset  # ch1 dimmer
        uni[i] = _clamp8(uni[i] * factor01)

    def cap_strobe(self, uni: bytearray, cap01: float) -> None:
        i = self.offset + 1  # ch2 strobe
        uni[i] = min(uni[i], _to8(cap01))

    def cap_brightness_while_strobing(self, uni: bytearray) -> None:
        if uni[self.offset + 1] > 0:  # ch2 strobe currently nonzero
            i = self.offset  # ch1 dimmer
            uni[i] = min(uni[i], self.STROBE_BRIGHTNESS_CAP_DMX)

    def apply_strobe_burst(self, uni: bytearray, cap01: float) -> None:
        uni[self.offset + 1] = _to8(cap01)  # ch2 strobe at the capped rate

    def apply_color_bias(self, uni: bytearray, bias: float) -> None:
        if bias == 0.0:
            return
        rf, bf = _bias_factors(bias)
        uni[self.offset + 2] = _clamp8(uni[self.offset + 2] * rf)  # R ch3
        uni[self.offset + 4] = _clamp8(uni[self.offset + 4] * bf)  # B ch5

    def visual_state(self, uni: bytes) -> VisualState:
        dim = uni[self.offset + 0] / 255
        r, g, b, w = (uni[self.offset + k] for k in range(2, 6))
        rr, gg, bb = min(255, r + w), min(255, g + w), min(255, b + w)
        strobe = uni[self.offset + 1] / 255  # ch2, the real strobe-rate byte
        return VisualState(self.label, self.role, self.side, rr, gg, bb, dim, 0.5, 0.5, dim > 0,
                           strobe=strobe)


# Registry mapping rig-file ``fixture_type`` -> profile class.
FIXTURE_TYPES: dict[str, type[Fixture]] = {
    cls.type_name: cls for cls in (MovingHead, RgbwBeamHead, Laser, Par, WashBar)
}
