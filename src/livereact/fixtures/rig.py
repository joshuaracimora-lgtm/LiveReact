"""Rig loader: instantiate fixture profiles from a JSON rig file and expose them
by role/side, so scenes can say "all wash, both sides" instead of raw DMX indices
(docs/04 §3.5). Validates that no two fixtures overlap and all fit in one universe.

Also the ONE serializer back to JSON (``rig_to_dict``/``save_rig``, 2026-09-18):
the console's layout save, the Rig Config tab and the fixture-test panel's
home-position save used to each build their own slot dicts and each silently
dropped the fields it didn't know about (a layout save could silently drop
a slot's hand-written notes).
Every save path goes through here now, and keys the loader doesn't model are
kept on ``Fixture.extra`` and re-emitted, so nothing is lost on a round trip.

Rig JSON schema (all of ``venue`` and the per-slot geometry are optional; every
pre-2026-09-18 rig file loads unchanged):

    {
      "name": "...", "description": "...",
      "laser_profile": "accent",                 # or "feature" (2026-10-01) / "trigger" (2026-10-06): which laser
                                                 # engine app.py builds -- see engines/laser.py
                                                 # (accent, the home rig) and
                                                 # engines/laser_feature.py (the bar)
      "venue": {"width_m": 9.1, "length_m": 12.2, "height_m": 3.7,
                "targets": {"dj_booth": {"x_m": 4.6, "y_m": 0.9, "z_m": 1.2}, ...}},
      "slots": [
        {"slot_id": "mh", "fixture_type": "shehds_180w_mh", "dmx_address": 36,
         "label": "...", "role": "spot", "side": "left",
         "x": 0.3, "y": 0.2,                       # legacy canvas position (0..1)
         "home_pan": 0.55, "home_tilt": 0.42,      # calibrated home (0..1)
         "position": {"x_m": 1.5, "y_m": 0.6, "z_m": 0.3}, "facing_deg": 0.0,
         "calibration": {"target": "dj_booth", "pan01": .., "tilt01": ..,
                         "pan_offset_deg": .., "tilt_offset_deg": ..,
                         "invert_pan": false, "invert_tilt": false},
         "dj_pan_tilt": {"pan": 0.55, "tilt": 0.40},   # optional (2026-10-21): the raw 0..1
                                                       # pan/tilt that faces the DJ (STRUCT-04)
         "beam": {"tilt_deg": 10.0, "fan_half_angle_deg": 30.0, "h_offset01": 0.5}}
      ]
    }

``dj_pan_tilt`` is read by the scorer only (sim/score.py STRUCT-04 "heads to
the DJ"); no engine path looks at it. ``Rig.dj_pose(fx)`` resolves the pose:
the explicit field first, else derived from a ``dj_booth`` venue target when
the head is placed AND aim-calibrated. Nothing configured -> None, and the
scorer marks that sub-check NOT_SCORED rather than inventing a position.

See fixtures/geometry.py for the coordinate convention (meters; origin at the
booth wall's stage-left corner; x to stage-right; y into the crowd; z up).
"""

from __future__ import annotations

import json
import logging
import os
from importlib import resources

from ..core.frame_buffer import UNIVERSE_SIZE
from .geometry import HeadCalibration, Point3, Venue, x_norm
from .profiles import FIXTURE_TYPES, ChannelKind, Fixture, Laser, MotionBounds, MovingHead

log = logging.getLogger(__name__)

# Which laser engine a rig asks for (2026-10-01, Josh's bar residency).
# "accent": engines/laser.py -- one sweep per 8-bar block in PEAK, the home
# rig's verified behaviour. "feature": engines/laser_feature.py -- the lasers
# are the main light, lit for (almost) the whole set. A rig file picks this
# because the choice is a property of the venue and its mount, not of a
# session; app.py reads it, nothing else does.
LASER_PROFILES = ("accent", "feature", "trigger", "show", "manual", "fan")  # "fan" (2026-10-28): engines/laser_fan.py;  # "show" (2026-10-08): engines/laser_show.py; "manual" (2026-10-10): engines/laser_manual.py
DEFAULT_LASER_PROFILE = "accent"

# Slot keys the loader models; anything else rides along in Fixture.extra.
_KNOWN_SLOT_KEYS = frozenset({
    "slot_id", "label", "fixture_type", "dmx_address", "role", "side",
    "x", "y", "home_pan", "home_tilt",
    "position", "facing_deg", "calibration", "beam", "bounds",
    "dj_pan_tilt",
})


class RigError(Exception):
    """Raised when a rig file is malformed or fixtures overlap / overflow."""


class Rig:
    """A loaded fixture layout: ordered fixtures + a precomputed channel-kind map."""

    def __init__(self, name: str, fixtures: list[Fixture], venue: Venue | None = None,
                 description: str = "", laser_profile: str = DEFAULT_LASER_PROFILE) -> None:
        self.name = name
        self.description = description
        self.fixtures = fixtures
        # See LASER_PROFILES above; validated by the loader, defaulted here so
        # a Rig built in code (tests, the visualizer demo) is the home profile.
        self.laser_profile = laser_profile
        # The room (fixtures/geometry.py). None -> no floor plan declared;
        # everything geometry-aware falls back to pre-2026-09-18 behavior.
        self.venue = venue
        self._by_slot: dict[str, Fixture] = {}
        self._kinds = bytearray(UNIVERSE_SIZE)  # ChannelKind per channel (default OTHER=0)
        # Atmosphere density (LIGHTING_BIBLE.md §4.4, `min_atmosphere_density`
        # gating -- Bible-integration Phase 1). A real, typed field rather than
        # a special-cased "no fog exists" branch scattered through look
        # selection: always 0.0 on this rig (no haze/fogger fixture exists),
        # so `requires{atm: X}` is a single honest comparison everywhere.
        # Revisit only if/when haze hardware is actually added.
        self.atm: float = 0.0
        self._validate_and_index()
        self.refresh_geometry()

    def _validate_and_index(self) -> None:
        occupied: dict[int, str] = {}
        for fx in self.fixtures:
            if fx.address < 1 or fx.address + fx.channel_count - 1 > UNIVERSE_SIZE:
                raise RigError(
                    f"{fx.label} at {fx.address} (+{fx.channel_count}) overflows the 512-channel universe"
                )
            kinds = fx.channel_kinds()
            if len(kinds) != fx.channel_count:
                raise RigError(f"{fx.label}: channel_kinds() length != channel_count")
            for local, ch in enumerate(fx.channels()):
                if ch in occupied:
                    raise RigError(
                        f"DMX address overlap at channel {ch}: {occupied[ch]} and {fx.label}"
                    )
                occupied[ch] = fx.label
                self._kinds[ch - 1] = int(kinds[local])
            self._by_slot[getattr(fx, "slot_id", fx.label)] = fx

    def refresh_geometry(self) -> None:
        """Derive each placed fixture's ``x_norm`` -- and from it its
        ``side`` -- from its real position in the declared venue
        (LIGHTING_BIBLE.md §1.2/§6.2: mirrored effects order fixtures by
        ``x_norm``, never by a hand-typed label). Without a venue nothing
        changes: ``side`` stays whatever the rig file declared, so rigs
        with no floor plan behave exactly as before. Called at load and by
        the Venue tab after every edit.

        MOVE-03: if two or more fixtures of one role sit on the same side of
        centre, a mirrored effect can't mirror -- warn, don't guess."""
        if self.venue is None or self.venue.width_m <= 0:
            for fx in self.fixtures:
                fx.x_norm = None
            return
        for fx in self.fixtures:
            if fx.position is None:
                fx.x_norm = None
                continue
            fx.x_norm = x_norm(fx.position.x, self.venue.width_m)
            if fx.x_norm < 0:
                fx.side = "left"
            elif fx.x_norm > 0:
                fx.side = "right"
        by_role: dict[str, list[Fixture]] = {}
        for fx in self.fixtures:
            if fx.x_norm is not None:
                by_role.setdefault(fx.role, []).append(fx)
        for role, fxs in by_role.items():
            if len(fxs) < 2:
                continue
            signs = {(fx.x_norm > 0) - (fx.x_norm < 0) for fx in fxs}  # type: ignore[operator]
            if len(signs) == 1 and 0 not in signs:
                log.warning(
                    "MOVE-03: all %d '%s' fixtures sit on the same side of centre (%s); "
                    "mirrored effects will run in unison",
                    len(fxs), role, ", ".join(fx.label for fx in fxs),
                )

    # --- lookups ---
    def fixture(self, slot_id: str) -> Fixture:
        return self._by_slot[slot_id]

    def by_role(self, role: str) -> list[Fixture]:
        return [f for f in self.fixtures if f.role == role]

    def by_side(self, side: str) -> list[Fixture]:
        return [f for f in self.fixtures if f.side == side]

    def by_role_side(self, role: str, side: str) -> list[Fixture]:
        return [f for f in self.fixtures if f.role == role and f.side == side]

    def kind_at(self, channel_0based: int) -> ChannelKind:
        return ChannelKind(self._kinds[channel_0based])

    def capabilities(self) -> set[str]:
        """Union of every fixture's `capabilities()` -- what `Scene.requires`
        gets checked against (Bible-integration Phase 1). Plus `role` tokens
        themselves (e.g. "wash", "spot", "laser") so a look can require a
        role directly, matching LIGHTING_BIBLE.md's own `requires{wash,
        atm 0.0}`-style shorthand."""
        caps: set[str] = set()
        for fx in self.fixtures:
            caps |= fx.capabilities()
            caps.add(fx.role)
        return caps

    def channel_count(self) -> int:
        return sum(f.channel_count for f in self.fixtures)

    def dj_pose(self, fx: Fixture) -> tuple[float, float] | None:
        """The raw (pan01, tilt01) at which moving head ``fx`` faces the DJ, or
        None when the rig doesn't say (scorer STRUCT-04; never an engine
        input). Resolution order:

        1. the slot's explicit ``dj_pan_tilt`` (set by the loader);
        2. derived with fixtures/geometry.py ``aim()`` from a ``dj_booth``
           venue target -- only when the head has a real ``position`` AND a
           captured aim calibration, because an uncalibrated trig aim is a
           guess, and a guessed "DJ pose" would grade the engine against a
           position nobody verified. Clamped to the motion box when one is
           set, like a scene's computed aim would be.
        """
        explicit = getattr(fx, "dj_pan_tilt", None)
        if explicit is not None:
            return (float(explicit[0]), float(explicit[1]))
        if not isinstance(fx, MovingHead) or self.venue is None:
            return None
        target = self.venue.targets.get("dj_booth")
        if target is None or fx.position is None or not fx.calibration.captured:
            return None
        from .geometry import aim
        pan01, tilt01, reachable = aim(fx.position, fx.facing_deg, target, fx.calibration,
                                       fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
        if not reachable:
            return None
        if fx.bounds is not None:
            pan01, tilt01 = fx.bounds.clamp(pan01, tilt01)
        return (pan01, tilt01)

    # --- boot ---
    def boot_universe(self) -> bytearray:
        """A 512-byte universe with every fixture in its safe power-on state."""
        from .profiles import Buffer

        class _UniBuffer(Buffer):
            def __init__(self, uni: bytearray) -> None:
                self.uni = uni

            def set(self, channel_1based: int, value: int) -> None:
                self.uni[channel_1based - 1] = 0 if value < 0 else 255 if value > 255 else value

        uni = bytearray(UNIVERSE_SIZE)
        buf = _UniBuffer(uni)
        for fx in self.fixtures:
            fx.boot_defaults(buf)
        return uni


def load_rig(path: str | None = None) -> Rig:
    """Load a rig from ``path``, or the packaged default rig when ``path`` is None."""
    if path is None:
        text = resources.files("livereact.fixtures").joinpath("default_rig.json").read_text()
    else:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    return _rig_from_dict(json.loads(text))


def _point(d: dict) -> Point3:
    return Point3(float(d.get("x_m", 0.0)), float(d.get("y_m", 0.0)), float(d.get("z_m", 0.0)))


def _dj_pan_tilt(raw, label: str) -> tuple[float, float]:
    """Parse a slot's ``dj_pan_tilt``: ``{"pan": p, "tilt": t}`` or ``[p, t]``,
    both normalized 0..1 raw DMX positions."""
    try:
        if isinstance(raw, dict):
            pan, tilt = float(raw["pan"]), float(raw["tilt"])
        else:
            pan, tilt = float(raw[0]), float(raw[1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RigError(f"{label}: dj_pan_tilt must be {{'pan': 0..1, 'tilt': 0..1}} or [pan, tilt]") from exc
    if not (0.0 <= pan <= 1.0 and 0.0 <= tilt <= 1.0):
        raise RigError(f"{label}: dj_pan_tilt values must be normalized 0..1 (got {pan}, {tilt})")
    return pan, tilt


def _venue_from_dict(d: dict | None) -> Venue | None:
    if not d:
        return None
    try:
        width, length = float(d["width_m"]), float(d["length_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RigError("venue needs numeric width_m and length_m") from exc
    height = float(d["height_m"]) if d.get("height_m") is not None else None
    targets = {str(k): _point(v) for k, v in (d.get("targets") or {}).items()}
    return Venue(width_m=width, length_m=length, height_m=height, targets=targets)


def _rig_from_dict(data: dict) -> Rig:
    try:
        slots = data["slots"]
    except (KeyError, TypeError) as exc:
        raise RigError("rig file must be an object with a 'slots' array") from exc

    fixtures: list[Fixture] = []
    for slot in slots:
        ftype = slot.get("fixture_type")
        cls = FIXTURE_TYPES.get(ftype)
        if cls is None:
            raise RigError(f"unknown fixture_type {ftype!r} (known: {sorted(FIXTURE_TYPES)})")
        fx = cls(
            address=int(slot["dmx_address"]),
            label=slot.get("label", slot.get("slot_id", ftype)),
            role=slot.get("role", "wash"),
            side=slot.get("side", "left"),
        )
        # Stash slot_id for fixture() lookups.
        fx.slot_id = slot.get("slot_id", fx.label)  # type: ignore[attr-defined]
        # Optional custom canvas position (normalized 0..1) — see Fixture.layout_x/y.
        if "x" in slot and "y" in slot:
            fx.layout_x = float(slot["x"])
            fx.layout_y = float(slot["y"])
        # Optional calibrated "facing the stage" position — see Fixture.home_pan/tilt.
        if "home_pan" in slot and "home_tilt" in slot:
            fx.home_pan = float(slot["home_pan"])
            fx.home_tilt = float(slot["home_tilt"])
        # Real-world placement (2026-09-18) — see Fixture.position/facing_deg.
        if isinstance(slot.get("position"), dict):
            fx.position = _point(slot["position"])
        fx.facing_deg = float(slot.get("facing_deg", 0.0))
        if isinstance(fx, MovingHead) and isinstance(slot.get("bounds"), dict):
            b = slot["bounds"]   # raw motion box, 2026-10-10
            fx.bounds = MotionBounds(float(b["pan_min"]), float(b["pan_max"]),
                                     float(b["tilt_min"]), float(b["tilt_max"]))
        # Optional DJ-facing pose (2026-10-21, scorer STRUCT-04). Stashed on the
        # fixture like slot_id; absent -> Rig.dj_pose() may still derive one.
        fx.dj_pan_tilt = None  # type: ignore[attr-defined]
        if isinstance(fx, MovingHead) and slot.get("dj_pan_tilt") is not None:
            fx.dj_pan_tilt = _dj_pan_tilt(slot["dj_pan_tilt"], fx.label)  # type: ignore[attr-defined]
        if isinstance(fx, MovingHead) and isinstance(slot.get("calibration"), dict):
            c = slot["calibration"]
            fx.calibration = HeadCalibration(
                pan_offset_deg=float(c.get("pan_offset_deg", 0.0)),
                tilt_offset_deg=float(c.get("tilt_offset_deg", 0.0)),
                invert_pan=bool(c.get("invert_pan", False)),
                invert_tilt=bool(c.get("invert_tilt", False)),
                target=c.get("target"),
                pan01=float(c["pan01"]) if c.get("pan01") is not None else None,
                tilt01=float(c["tilt01"]) if c.get("tilt01") is not None else None,
                pan_range_deg=float(c["pan_range_deg"]) if c.get("pan_range_deg") else None,
                tilt_range_deg=float(c["tilt_range_deg"]) if c.get("tilt_range_deg") else None,
                target2=c.get("target2"),
                pan01_2=float(c["pan01_2"]) if c.get("pan01_2") is not None else None,
                tilt01_2=float(c["tilt01_2"]) if c.get("tilt01_2") is not None else None,
            )
        if isinstance(fx, Laser):
            if isinstance(slot.get("beam"), dict):
                b = slot["beam"]
                fx.beam_tilt_deg = float(b.get("tilt_deg", fx.beam_tilt_deg))
                fx.fan_half_angle_deg = float(b.get("fan_half_angle_deg", fx.fan_half_angle_deg))
                fx.h_offset01 = float(b.get("h_offset01", fx.h_offset01))
        fx.extra = {k: v for k, v in slot.items() if k not in _KNOWN_SLOT_KEYS}
        fixtures.append(fx)
    profile = str(data.get("laser_profile", DEFAULT_LASER_PROFILE))
    if profile not in LASER_PROFILES:
        raise RigError(f"unknown laser_profile {profile!r} (known: {list(LASER_PROFILES)})")
    return Rig(
        name=data.get("name", "rig"),
        fixtures=fixtures,
        venue=_venue_from_dict(data.get("venue")),
        description=str(data.get("description", "")),
        laser_profile=profile,
    )


# ------------------------------------------------------------------ serialize

def _point_dict(p: Point3) -> dict:
    return {"x_m": round(p.x, 4), "y_m": round(p.y, 4), "z_m": round(p.z, 4)}


def slot_to_dict(fx: Fixture) -> dict:
    """One rig-file slot for ``fx``, every modeled field included when set,
    plus whatever unknown keys it was loaded with."""
    slot: dict = {
        "slot_id": getattr(fx, "slot_id", fx.label),
        "label": fx.label,
        "fixture_type": fx.type_name,
        "dmx_address": fx.address,
        "role": fx.role,
        "side": fx.side,
    }
    if fx.layout_x is not None and fx.layout_y is not None:
        slot["x"], slot["y"] = round(fx.layout_x, 4), round(fx.layout_y, 4)
    if fx.home_pan is not None and fx.home_tilt is not None:
        slot["home_pan"], slot["home_tilt"] = round(fx.home_pan, 4), round(fx.home_tilt, 4)
    if fx.position is not None:
        slot["position"] = _point_dict(fx.position)
    if fx.facing_deg:
        slot["facing_deg"] = round(fx.facing_deg, 2)
    if isinstance(fx, MovingHead) and fx.bounds is not None:
        b = fx.bounds
        slot["bounds"] = {"pan_min": round(b.pan_min, 4), "pan_max": round(b.pan_max, 4),
                          "tilt_min": round(b.tilt_min, 4), "tilt_max": round(b.tilt_max, 4)}
    if isinstance(fx, MovingHead) and getattr(fx, "dj_pan_tilt", None) is not None:
        pan, tilt = fx.dj_pan_tilt  # type: ignore[attr-defined]
        slot["dj_pan_tilt"] = {"pan": round(pan, 4), "tilt": round(tilt, 4)}
    if isinstance(fx, MovingHead) and fx.calibration.captured:
        c = fx.calibration
        slot["calibration"] = {
            "target": c.target, "pan01": c.pan01, "tilt01": c.tilt01,
            "pan_offset_deg": round(c.pan_offset_deg, 3), "tilt_offset_deg": round(c.tilt_offset_deg, 3),
            "invert_pan": c.invert_pan, "invert_tilt": c.invert_tilt,
        }
        if c.pan_range_deg or c.tilt_range_deg:   # two-point capture (2026-10-10)
            slot["calibration"].update({
                "pan_range_deg": round(c.pan_range_deg, 2) if c.pan_range_deg else None,
                "tilt_range_deg": round(c.tilt_range_deg, 2) if c.tilt_range_deg else None,
                "target2": c.target2, "pan01_2": c.pan01_2, "tilt01_2": c.tilt01_2,
            })
    if isinstance(fx, Laser):
        slot["beam"] = {
            "tilt_deg": fx.beam_tilt_deg,
            "fan_half_angle_deg": fx.fan_half_angle_deg,
            "h_offset01": fx.h_offset01,
        }
    slot.update(fx.extra)
    return slot


def rig_to_dict(rig: Rig, description: str | None = None) -> dict:
    """The whole rig as a JSON-ready dict -- the inverse of ``_rig_from_dict``."""
    data: dict = {
        "name": rig.name,
        "description": description if description is not None else rig.description,
        "laser_profile": rig.laser_profile,
    }
    if rig.venue is not None:
        v = rig.venue
        data["venue"] = {
            "width_m": round(v.width_m, 4),
            "length_m": round(v.length_m, 4),
            "height_m": round(v.height_m, 4) if v.height_m is not None else None,
            "targets": {name: _point_dict(p) for name, p in v.targets.items()},
        }
    data["slots"] = [slot_to_dict(fx) for fx in rig.fixtures]
    return data


def save_rig(rig: Rig, path: str, description: str | None = None) -> dict:
    """Validate (through the loader itself) and write ``rig`` to ``path``.
    Returns the dict written. Raises RigError rather than writing a file the
    loader would reject."""
    data = rig_to_dict(rig, description)
    _rig_from_dict(data)  # same overlap/overflow/unknown-type validation the loader uses
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    log.info("Saved rig to %s", path)
    return data
