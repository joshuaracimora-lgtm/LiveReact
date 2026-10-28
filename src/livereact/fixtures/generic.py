"""GenericFixture: a ``profiles.Fixture`` whose channels come from a fixture definition.

This is how a user-added light (fixtures/definition.py) speaks the same semantic setters as
the four native classes. Not used by the show yet: the rig loader builds these from v2 rig
files (roadmap item 14) and scenes find them by trait (item 15).
"""

from __future__ import annotations

from .definition import LASER_INTENTS, Capability, DefinitionError, FixtureDefinition, validate
from .profiles import (
    Buffer, ChannelKind, Fixture, VisualState, _bias_factors, _clamp8, _intensity_to8, _to8, _to16_int,
)

_MIX = ("red", "green", "blue", "white", "amber", "uv")


def _hex_rgb(color: str | None) -> tuple[int, int, int] | None:
    if not color or not color.startswith("#") or len(color) != 7:
        return None
    try:
        return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    except ValueError:
        return None


class GenericFixture(Fixture):
    """Dimmer, RGB(+W/A/UV) or colour wheel, and strobe, from a definition's channel functions."""

    def __init__(self, definition: FixtureDefinition, mode: str | None = None, address: int = 1,
                 label: str = "", role: str = "wash", side: str = "left") -> None:
        errs = validate(definition)
        if errs:
            raise DefinitionError(f"{definition.id}: " + "; ".join(errs))
        m = definition.mode(mode)  # KeyError on an unknown mode
        super().__init__(address, label or definition.model, role, side)
        self.definition = definition
        self.mode_name = m.name
        self.type_name = f"{definition.id}@{m.name}"
        self.chans = tuple(sorted(m.channels, key=lambda c: c.offset))
        self.channel_count = len(self.chans)
        # first channel (0-based local offset) per function
        self._at: dict[str, int] = {}
        for c in self.chans:
            if c.fine_of is None:
                self._at.setdefault(c.function, c.offset)
        # No-dimmer fixtures scale their colour by the last intensity (and vice versa),
        # so both setters remember the other's last value.
        self._level = 1.0
        self._rgbw = (0.0, 0.0, 0.0, 0.0)

    # --- helpers ---
    def _caps(self, function: str) -> tuple[Capability, ...]:
        i = self._at.get(function)
        return () if i is None else self.chans[i].capabilities

    def _strobe_slot(self) -> tuple[int, tuple[int, int] | None] | None:
        """(local offset, strobe range or None for a plain 0-255 rate channel)."""
        for fn in ("strobe", "shutter"):
            i = self._at.get(fn)
            if i is None:
                continue
            rng = next((c.range for c in self.chans[i].capabilities if c.type == "strobe"), None)
            if rng is None and fn == "shutter":
                continue  # a shutter with no strobe range cannot strobe
            return i, rng
        return None

    def _open_value(self, i: int) -> int:
        c = self.chans[i]
        if c.function == "shutter":
            for cap in c.capabilities:
                if cap.type == "shutter" and "open" in cap.label.lower():
                    return cap.range[0]
        return c.default if c.function != "strobe" else 0

    def channel_kinds(self) -> list[ChannelKind]:
        return self.definition.channel_kinds(self.mode_name)

    # --- boot ---
    def boot_defaults(self, buf: Buffer) -> None:
        for c in self.chans:
            if c.function == "dimmer":
                v = 0
            elif c.function == "shutter":
                v = self._open_value(c.offset)
            else:
                v = c.default
            buf.set(self.address + c.offset, v)

    # --- semantic setters ---
    def set_intensity(self, buf: Buffer, level01: float) -> None:
        if "dimmer" in self._at:
            buf.set(self.address + self._at["dimmer"], _intensity_to8(level01))
        else:
            self._level = max(0.0, min(1.0, level01))
            self._write_mix(buf)

    def set_color(self, buf: Buffer, r: float, g: float, b: float, w: float = 0.0) -> None:
        self._rgbw = (r, g, b, w)
        if {"red", "green", "blue"} <= self._at.keys():
            self._write_mix(buf)
        elif "color_wheel" in self._at:
            self._write_wheel(buf, r, g, b, w)

    def _write_mix(self, buf: Buffer) -> None:
        r, g, b, w = self._rgbw
        if "white" not in self._at:
            r, g, b, w = min(1.0, r + w), min(1.0, g + w), min(1.0, b + w), 0.0
        lvl = 1.0 if "dimmer" in self._at else self._level
        # amber/UV have no intent in set_color: hold them dark rather than leave stale values
        values = {"red": r, "green": g, "blue": b, "white": w, "amber": 0.0, "uv": 0.0}
        for fn, v in values.items():
            if fn in self._at:
                buf.set(self.address + self._at[fn], _to8(v * lvl))

    def _write_wheel(self, buf: Buffer, r: float, g: float, b: float, w: float) -> None:
        target = (_to8(min(1.0, r + w)), _to8(min(1.0, g + w)), _to8(min(1.0, b + w)))
        best, best_d = None, 1 << 30
        for cap in self._caps("color_wheel"):
            rgb = _hex_rgb(cap.color)
            if rgb is None:
                continue
            d = sum((x - y) ** 2 for x, y in zip(rgb, target))
            if d < best_d:
                best, best_d = cap, d
        if best is not None:
            buf.set(self.address + self._at["color_wheel"], (best.range[0] + best.range[1]) // 2)

    def set_strobe(self, buf: Buffer, rate01: float) -> None:
        slot = self._strobe_slot()
        if slot is None:
            return
        i, rng = slot
        rate01 = max(0.0, min(1.0, rate01))
        if rate01 <= 0.0:
            v = self._open_value(i)
        elif rng is None:
            v = _to8(rate01)
        else:
            v = round(rng[0] + rate01 * (rng[1] - rng[0]))
        buf.set(self.address + i, v)

    # --- finalize-stage clamps ---
    def apply_master_brightness(self, uni: bytearray, factor01: float) -> None:
        fns = ("dimmer",) if "dimmer" in self._at else _MIX
        for fn in fns:
            if fn in self._at:
                i = self.offset + self._at[fn]
                uni[i] = _clamp8(uni[i] * factor01)

    def cap_strobe(self, uni: bytearray, cap01: float) -> None:
        slot = self._strobe_slot()
        if slot is None:
            return
        i, rng = slot
        j = self.offset + i
        if rng is None:
            uni[j] = min(uni[j], _to8(cap01))
        elif rng[0] <= uni[j] <= rng[1]:
            uni[j] = min(uni[j], round(rng[0] + max(0.0, min(1.0, cap01)) * (rng[1] - rng[0])))

    def apply_color_bias(self, uni: bytearray, bias: float) -> None:
        if bias == 0.0 or not {"red", "blue"} <= self._at.keys():
            return
        rf, bf = _bias_factors(bias)
        uni[self.offset + self._at["red"]] = _clamp8(uni[self.offset + self._at["red"]] * rf)
        uni[self.offset + self._at["blue"]] = _clamp8(uni[self.offset + self._at["blue"]] * bf)

    # --- visualization ---
    def visual_state(self, uni: bytes) -> VisualState:
        def at(fn: str) -> int:
            return uni[self.offset + self._at[fn]] if fn in self._at else 0

        if {"red", "green", "blue"} <= self._at.keys():
            w = at("white")
            r, g, b = min(255, at("red") + w), min(255, at("green") + w), min(255, at("blue") + w)
        elif "color_wheel" in self._at:
            v = at("color_wheel")
            cap = next((c for c in self._caps("color_wheel") if c.range[0] <= v <= c.range[1]), None)
            r, g, b = (_hex_rgb(cap.color) if cap else None) or (255, 255, 255)
        else:
            r = g = b = 255
        if "dimmer" in self._at:
            dim = at("dimmer") / 255
        else:
            dim = 1.0 if max(r, g, b) > 0 else 0.0
        slot = self._strobe_slot()
        strobe = 0.0
        if slot is not None:
            i, rng = slot
            v = uni[self.offset + i]
            if rng is None:
                strobe = v / 255
            elif rng[0] <= v <= rng[1] and rng[1] > rng[0]:
                strobe = (v - rng[0]) / (rng[1] - rng[0])
        return VisualState(self.label, self.role, self.side, r, g, b, dim, 0.5, 0.5,
                           dim > 0 and max(r, g, b) > 0, strobe=strobe)


# Conservative defaults when a definition has no physical block: the native SHEHDS head's
# ranges, and a slower pan than its (unmeasured) 260 deg/s placeholder, so an unknown motor
# is never slammed (LIGHTING_BIBLE.md §6.6).
DEFAULT_PAN_DEG = 540.0
DEFAULT_TILT_DEG = 270.0
DEFAULT_PAN_MAX_DEG_S = 180.0


class GenericMovingHead(GenericFixture):
    """Adds pan/tilt (16-bit when the definition pairs fine channels), a slew limit and the
    calibrated home-position remap, mirroring ``profiles.MovingHead``."""

    def __init__(self, definition: FixtureDefinition, mode: str | None = None, address: int = 1,
                 label: str = "", role: str = "spot", side: str = "left") -> None:
        super().__init__(definition, mode, address, label, role, side)
        if not {"pan", "tilt"} <= self._at.keys():
            raise DefinitionError(f"{definition.id}: a moving head needs pan and tilt channels")
        fine = {self.chans[c.fine_of].function: c.offset for c in self.chans if c.fine_of is not None}
        self._pan = (self._at["pan"], fine.get("pan"))
        self._tilt = (self._at["tilt"], fine.get("tilt"))
        ph = definition.physical
        self.pan_range_deg = float(ph.pan_deg or DEFAULT_PAN_DEG)
        self.tilt_range_deg = float(ph.tilt_deg or DEFAULT_TILT_DEG)
        self.pan_max_deg_s = float(ph.pan_max_deg_s or DEFAULT_PAN_MAX_DEG_S)
        # the format carries one speed: tilt gets the same deg/s over its own range
        self.tilt_max_deg_s = self.pan_max_deg_s
        self._prev: dict[str, int | None] = {"pan": None, "tilt": None}

    def _write(self, uni_or_buf, slot: tuple[int, int | None], v16: int, via_buf: bool) -> None:
        coarse, fine = slot
        if via_buf:
            uni_or_buf.set(self.address + coarse, v16 >> 8)
            if fine is not None:
                uni_or_buf.set(self.address + fine, v16 & 0xFF)
        else:
            uni_or_buf[self.offset + coarse] = v16 >> 8
            if fine is not None:
                uni_or_buf[self.offset + fine] = v16 & 0xFF

    def _read(self, uni, slot: tuple[int, int | None]) -> int:
        coarse, fine = slot
        lo = uni[self.offset + fine] if fine is not None else 0
        return (uni[self.offset + coarse] << 8) | lo

    def set_position(self, buf: Buffer, pan01: float, tilt01: float) -> None:
        self._write(buf, self._pan, _to16_int(pan01), True)
        self._write(buf, self._tilt, _to16_int(tilt01), True)

    def set_aim(self, buf: Buffer, pan01: float, tilt01: float) -> None:
        """A final aim: marks the movement channels so finalize skips the home remap."""
        self.set_position(buf, pan01, tilt01)
        marks = getattr(buf, "absolute_movement", None)
        if marks is not None:
            marks.update(self.offset + i for slot in (self._pan, self._tilt) for i in slot if i is not None)

    def apply_home_position(self, uni: bytearray, bound: float = 0.20, absolute: bool = False) -> None:
        if absolute or self.home_pan is None or self.home_tilt is None:
            return
        for slot, home in ((self._pan, self.home_pan), (self._tilt, self.home_tilt)):
            raw = self._read(uni, slot) / 65535
            self._write(uni, slot, _to16_int(max(0.0, min(1.0, home + (raw - 0.5) * bound))), False)

    def apply_slew_limit(self, uni: bytearray, dt: float) -> None:
        for name, slot, speed, rng in (("pan", self._pan, self.pan_max_deg_s, self.pan_range_deg),
                                       ("tilt", self._tilt, self.tilt_max_deg_s, self.tilt_range_deg)):
            commanded = self._read(uni, slot)
            prev = self._prev[name]
            if prev is None:
                self._prev[name] = commanded
                continue
            max_delta = speed * dt / rng * 65535.0
            new = max(0, min(65535, round(prev + max(-max_delta, min(max_delta, commanded - prev)))))
            self._write(uni, slot, new, False)
            self._prev[name] = new

    def visual_state(self, uni: bytes) -> VisualState:
        vs = super().visual_state(uni)
        return VisualState(vs.label, vs.role, vs.side, vs.r, vs.g, vs.b, vs.brightness,
                           self._read(uni, self._pan) / 65535, self._read(uni, self._tilt) / 65535,
                           vs.active, strobe=vs.strobe)


class GenericLaser(Fixture):
    """A laser from a definition, driven ONLY through its ``laser_intents`` (blank, line_static,
    fan). Not a GenericFixture, so no wash/dimmer/strobe intent can light it.

    Fail-safe on top of the native laser's two gates: until the definition is confirmed on the
    channel-test screen with all three intents (``ready``), every write and every finalize pass
    leaves the whole head at zero, even with ``laser_enable`` on."""

    def __init__(self, definition: FixtureDefinition, mode: str | None = None, address: int = 1,
                 label: str = "", role: str = "laser", side: str = "left") -> None:
        errs = validate(definition)
        if errs:
            raise DefinitionError(f"{definition.id}: " + "; ".join(errs))
        m = definition.mode(mode)
        super().__init__(address, label or definition.model, role, side)
        self.definition = definition
        self.mode_name = m.name
        self.type_name = f"{definition.id}@{m.name}"
        self.channel_count = len(m.channels)
        li = definition.laser_intents
        self.ready = bool(definition.source.confirmed and li is not None
                          and all(getattr(li, k) for k in LASER_INTENTS))

    def channel_kinds(self) -> list[ChannelKind]:
        return [ChannelKind.LASER] * self.channel_count

    def _values(self, intent: str) -> bytes:
        if intent not in LASER_INTENTS:
            raise KeyError(intent)
        if not self.ready:
            return bytes(self.channel_count)
        vals = getattr(self.definition.laser_intents, intent)
        return bytes(vals.get(i, 0) for i in range(self.channel_count))

    def set_intent(self, buf: Buffer, intent: str) -> None:
        """Write one intent across the whole head (channels it does not name go to 0)."""
        for i, v in enumerate(self._values(intent)):
            buf.set(self.address + i, v)

    def boot_defaults(self, buf: Buffer) -> None:
        self.set_intent(buf, "blank")

    def apply_safety(self, uni: bytearray, laser_enabled: bool) -> None:
        if not (laser_enabled and self.ready):
            uni[self.offset:self.offset + self.channel_count] = bytes(self.channel_count)

    def apply_laser_gate(self, uni: bytearray, laser_on: bool) -> None:
        if laser_on:
            return
        blank = self._values("blank")
        chop = self.definition.laser_intents.chop_channel if self.ready else None
        if chop is not None:
            uni[self.offset + chop] = blank[chop]
        else:
            uni[self.offset:self.offset + self.channel_count] = blank

    def visual_state(self, uni: bytes) -> VisualState:
        cur = bytes(uni[self.offset:self.offset + self.channel_count])
        active = self.ready and any(cur) and cur != self._values("blank")
        return VisualState(self.label, self.role, self.side, *((0, 255, 0) if active else (0, 0, 0)),
                           1.0 if active else 0.0, 0.5, 0.5, active)


def make_fixture(definition: FixtureDefinition, mode: str | None = None, address: int = 1,
                 label: str = "", role: str | None = None, side: str = "left") -> Fixture:
    """The Generic* class a definition needs: a laser by category, a moving head when it has
    pan and tilt, else a plain GenericFixture."""
    if definition.category == "laser":
        return GenericLaser(definition, mode, address, label, role or "laser", side)
    functions = {c.function for c in definition.mode(mode).channels}
    if {"pan", "tilt"} <= functions:
        return GenericMovingHead(definition, mode, address, label, role or "spot", side)
    return GenericFixture(definition, mode, address, label, role or "wash", side)
