"""Venue geometry: real-world fixture positions and moving-head aiming math.

Pure Python, no Qt -- the floor-plan UI (ui/floorplan.py) draws from it and
scenes aim through it (scenes/base.py's aim_spots), but everything here is
unit-testable with hand-computed cases.

Coordinate convention (2026-09-18, Josh's venue floor-plan request; see
docs/14_AUDIO_REACTIVE_SCENE_LOGIC.md §4 "Option B"):

  * Units are METERS in code and in rig JSON. The operator types FEET in the
    UI; ``ft_to_m``/``m_to_ft`` convert at that boundary and nowhere else.
  * Origin: the stage-left corner of the wall behind the DJ booth, on the
    floor.
  * ``x``: along the booth wall, increasing to stage-RIGHT (the DJ's right
    when facing the crowd). ``Venue.width_m`` is the x extent.
    LIGHTING_BIBLE.md §1.2's ``x_norm`` is ``2*x/width - 1``.
  * ``y``: depth, increasing AWAY from the booth into the crowd.
    ``Venue.length_m`` is the y extent.
  * ``z``: height above the floor (a fixture's lens/emitter height).
  * ``facing_deg``: the yaw of a fixture body's "pan = 0.5" direction.
    0 = pointing along +y (into the crowd); positive = clockwise viewed from
    above (toward +x). Azimuths below use the same convention.
  * Elevation: 0 = horizontal, +90 = straight up, -90 = straight down.

Moving-head model (one captured target solves the two offsets):

    az_rel = (pan01  - 0.5) * pan_range_deg  * (-1 if invert_pan  else 1) + pan_offset_deg
    el     = (tilt01 - 0.5) * tilt_range_deg * (-1 if invert_tilt else 1) + tilt_offset_deg
    az_abs = facing_deg + az_rel

``pan_range_deg``/``tilt_range_deg`` come from the fixture profile
(``MovingHead.PAN_RANGE_DEG``/``TILT_RANGE_DEG`` -- PLACEHOLDERS, not measured
on this unit). A one-point capture fixes the OFFSET but not the SCALE; if a
verified aim lands consistently short/long, those constants are wrong (a
two-point capture would solve scale; deliberately not built in this pass).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FT_PER_M = 3.280839895


def ft_to_m(ft: float) -> float:
    return ft / FT_PER_M


def m_to_ft(m: float) -> float:
    return m * FT_PER_M


@dataclass(frozen=True)
class Point3:
    x: float
    y: float
    z: float = 0.0


@dataclass
class Venue:
    """The room, in meters. ``targets`` are the named focus points scenes aim
    at (LIGHTING_BIBLE.md §1.2's ``position_palette``, expressed as real
    points instead of DMX pairs so one rig file works from any mount)."""

    width_m: float
    length_m: float
    height_m: float | None = None
    targets: dict[str, Point3] = field(default_factory=dict)

    @staticmethod
    def default_targets(width_m: float, length_m: float, height_m: float | None) -> dict[str, Point3]:
        """Sensible starting targets for a new venue: booth just off the back
        wall, floor centre, back wall, and a ceiling bounce over the floor."""
        cx = width_m / 2
        ceiling = height_m if height_m is not None else 3.6
        return {
            "dj_booth": Point3(cx, min(0.9, length_m / 4), 1.2),
            "dancefloor_centre": Point3(cx, length_m / 2, 1.0),
            "back_wall": Point3(cx, length_m - 0.2, 2.0),
            "ceiling_bounce": Point3(cx, length_m / 2, ceiling),
        }


@dataclass
class HeadCalibration:
    """What a one-point aim capture learned about a moving head."""

    pan_offset_deg: float = 0.0
    tilt_offset_deg: float = 0.0
    invert_pan: bool = False
    invert_tilt: bool = False
    target: str | None = None      # the named target it was captured on
    pan01: float | None = None     # the raw pan/tilt that hit that target
    tilt01: float | None = None
    # Two-point capture (2026-10-10, Junior Crib): the SOLVED pan/tilt travel
    # of this unit as mounted, replacing the profile's placeholder constants
    # whenever set. None = one-point capture, trust the profile.
    pan_range_deg: float | None = None
    tilt_range_deg: float | None = None
    target2: str | None = None     # the second capture, kept so a re-solve can show it
    pan01_2: float | None = None
    tilt01_2: float | None = None
    # Where capture A (SET AIM) was taken (2026-10-31): the head's position
    # and facing, and the target's point at that moment. ADD 2ND POINT refuses
    # when any of them has since changed -- two captures taken against
    # different geometries solve a nonsense direction/travel. None = a file
    # from before this was recorded; nothing can be checked then.
    captured_origin: Point3 | None = None
    captured_facing_deg: float | None = None
    captured_target_point: Point3 | None = None

    def ranges(self, pan_range_deg: float, tilt_range_deg: float) -> tuple[float, float]:
        """The travel to use: the solved values when a two-point capture
        exists, else the profile's ``pan_range_deg``/``tilt_range_deg``."""
        return (self.pan_range_deg if self.pan_range_deg else pan_range_deg,
                self.tilt_range_deg if self.tilt_range_deg else tilt_range_deg)

    @property
    def captured(self) -> bool:
        return self.target is not None


# ---------------------------------------------------------------- angles

def wrap_deg(a: float) -> float:
    """Wrap an angle into (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def bearing(origin: Point3, target: Point3) -> tuple[float, float]:
    """(azimuth_deg, elevation_deg) from ``origin`` to ``target`` in the
    module's convention (az 0 = +y, clockwise positive; el +up)."""
    dx, dy, dz = target.x - origin.x, target.y - origin.y, target.z - origin.z
    horiz = math.hypot(dx, dy)
    az = math.degrees(math.atan2(dx, dy)) if horiz > 1e-12 else 0.0
    el = math.degrees(math.atan2(dz, horiz))
    return wrap_deg(az), el


def _sign(invert: bool) -> float:
    return -1.0 if invert else 1.0


def pan_tilt_to_angles(pan01: float, tilt01: float, facing_deg: float, cal: HeadCalibration,
                       pan_range_deg: float, tilt_range_deg: float) -> tuple[float, float]:
    """Where a raw pan/tilt actually points: (az_abs_deg, el_deg)."""
    pan_range_deg, tilt_range_deg = cal.ranges(pan_range_deg, tilt_range_deg)
    az_rel = (pan01 - 0.5) * pan_range_deg * _sign(cal.invert_pan) + cal.pan_offset_deg
    el = (tilt01 - 0.5) * tilt_range_deg * _sign(cal.invert_tilt) + cal.tilt_offset_deg
    return wrap_deg(facing_deg + az_rel), el


def calibrate_from_capture(origin: Point3, facing_deg: float, target: Point3,
                           pan01: float, tilt01: float,
                           pan_range_deg: float, tilt_range_deg: float,
                           invert_pan: bool = False, invert_tilt: bool = False,
                           target_name: str | None = None) -> HeadCalibration:
    """The operator jogged the head onto ``target`` and the raw values were
    ``pan01``/``tilt01``: solve the pan/tilt offsets that make the model agree."""
    az_abs, el = bearing(origin, target)
    az_rel_needed = wrap_deg(az_abs - facing_deg)
    raw_az = (pan01 - 0.5) * pan_range_deg * _sign(invert_pan)
    raw_el = (tilt01 - 0.5) * tilt_range_deg * _sign(invert_tilt)
    return HeadCalibration(
        pan_offset_deg=wrap_deg(az_rel_needed - raw_az),
        tilt_offset_deg=el - raw_el,
        invert_pan=invert_pan,
        invert_tilt=invert_tilt,
        target=target_name or "captured",
        pan01=pan01,
        tilt01=tilt01,
        captured_origin=origin,
        captured_facing_deg=facing_deg,
        captured_target_point=target,
    )


MIN_SEPARATION_01 = 0.02   # raw pan/tilt difference below which an axis can't be solved from two points


def calibrate_from_two_captures(origin: Point3, facing_deg: float,
                                target_a: Point3, pan_a: float, tilt_a: float,
                                target_b: Point3, pan_b: float, tilt_b: float,
                                pan_range_deg: float, tilt_range_deg: float,
                                target_a_name: str | None = None,
                                target_b_name: str | None = None) -> HeadCalibration:
    """Two captures solve each axis's DIRECTION and TRAVEL as well as its
    offset (2026-10-10, Junior Crib: heads hung at 18 ft verified the wrong
    way / the wrong distance after a one-point capture because the profile's
    range constants are placeholders and a hung head pans mirrored).

    Per axis, with k = signed degrees per unit of raw travel:
        angle_a = k*(raw_a - 0.5) + offset,  angle_b = k*(raw_b - 0.5) + offset
        -> k = (angle_b - angle_a) / (raw_b - raw_a);  invert = k < 0;  range = |k|
    Pan differences are wrapped into (-180, 180] (the short way round). An axis
    whose two raw values are closer than ``MIN_SEPARATION_01`` keeps the
    profile's direction/range and only takes the offset from capture A."""
    az_a, el_a = bearing(origin, target_a)
    az_b, el_b = bearing(origin, target_b)
    rel_a = wrap_deg(az_a - facing_deg)
    rel_b = wrap_deg(az_b - facing_deg)

    if abs(pan_b - pan_a) >= MIN_SEPARATION_01:
        k_pan = wrap_deg(rel_b - rel_a) / (pan_b - pan_a)
        invert_pan, pan_range = k_pan < 0, abs(k_pan)
    else:
        k_pan, invert_pan, pan_range = pan_range_deg, False, None
    if abs(tilt_b - tilt_a) >= MIN_SEPARATION_01:
        k_tilt = (el_b - el_a) / (tilt_b - tilt_a)
        invert_tilt, tilt_range = k_tilt < 0, abs(k_tilt)
    else:
        k_tilt, invert_tilt, tilt_range = tilt_range_deg, False, None

    return HeadCalibration(
        pan_offset_deg=wrap_deg(rel_a - k_pan * (pan_a - 0.5)),
        tilt_offset_deg=el_a - k_tilt * (tilt_a - 0.5),
        invert_pan=invert_pan,
        invert_tilt=invert_tilt,
        target=target_a_name or "captured",
        pan01=pan_a, tilt01=tilt_a,
        pan_range_deg=pan_range, tilt_range_deg=tilt_range,
        target2=target_b_name or "captured-2",
        pan01_2=pan_b, tilt01_2=tilt_b,
        captured_origin=origin,
        captured_facing_deg=facing_deg,
        captured_target_point=target_a,
    )


def aim(origin: Point3, facing_deg: float, target: Point3, cal: HeadCalibration,
        pan_range_deg: float, tilt_range_deg: float) -> tuple[float, float, bool]:
    """Raw (pan01, tilt01, reachable) that points the head at ``target``.

    Pan has more than 360 deg of travel, so a bearing can be reached at more
    than one pan value: pick the one nearest the captured pan (or centre if
    nothing was captured) so the head takes the short way round. Values are
    clamped to 0..1; ``reachable`` is False when either axis had to clamp."""
    pan_range_deg, tilt_range_deg = cal.ranges(pan_range_deg, tilt_range_deg)
    az_abs, el = bearing(origin, target)
    az_rel = az_abs - facing_deg - cal.pan_offset_deg
    ps = _sign(cal.invert_pan)
    prefer = cal.pan01 if cal.pan01 is not None else 0.5
    best: float | None = None
    for k in (-2, -1, 0, 1, 2):
        cand = 0.5 + (az_rel + 360.0 * k) * ps / pan_range_deg
        if 0.0 <= cand <= 1.0 and (best is None or abs(cand - prefer) < abs(best - prefer)):
            best = cand
    reachable = best is not None
    if best is None:  # nothing in range: clamp the wrap nearest the range
        cand = 0.5 + az_rel * ps / pan_range_deg
        best = min(1.0, max(0.0, cand))
    tilt = 0.5 + (el - cal.tilt_offset_deg) * _sign(cal.invert_tilt) / tilt_range_deg
    if not 0.0 <= tilt <= 1.0:
        reachable = False
        tilt = min(1.0, max(0.0, tilt))
    return best, tilt, reachable


# ---------------------------------------------------------------- rays

def _direction(az_deg: float) -> tuple[float, float]:
    a = math.radians(az_deg)
    return math.sin(a), math.cos(a)


def beam_floor_hit(origin: Point3, az_abs_deg: float, el_deg: float) -> Point3 | None:
    """Where a beam from ``origin`` meets the floor (z=0), or None if it's
    aimed level or upward."""
    if el_deg >= 0.0 or origin.z <= 0.0:
        return None
    dist = origin.z / math.tan(math.radians(-el_deg))
    dx, dy = _direction(az_abs_deg)
    return Point3(origin.x + dx * dist, origin.y + dy * dist, 0.0)


def ray_to_rect_edge(origin_xy: tuple[float, float], az_abs_deg: float,
                     width_m: float, length_m: float) -> float:
    """Horizontal distance from ``origin_xy`` along ``az`` to the venue
    rectangle's boundary (0 if the ray never leaves through an edge ahead)."""
    ox, oy = origin_xy
    dx, dy = _direction(az_abs_deg)
    ts: list[float] = []
    if abs(dx) > 1e-12:
        ts += [(0.0 - ox) / dx, (width_m - ox) / dx]
    if abs(dy) > 1e-12:
        ts += [(0.0 - oy) / dy, (length_m - oy) / dy]
    ahead = [t for t in ts if t > 1e-9]
    return min(ahead) if ahead else 0.0


def x_norm(x_m: float, width_m: float) -> float:
    """LIGHTING_BIBLE.md §1.2: -1 = extreme stage-left, +1 = extreme stage-right."""
    if width_m <= 0:
        return 0.0
    return 2.0 * x_m / width_m - 1.0
