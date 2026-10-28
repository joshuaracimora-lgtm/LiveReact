"""Fixture definitions: a fixture described as data (schema ``livereact-fixture/1``).

A definition is what a user adds (from Open Fixture Library, an AI read of the manual, or by
hand) and confirms on the channel-test screen. It sits beside the native classes in
``profiles.py``, which stay what the show runs; ``kind_of()`` maps each channel onto the
``ChannelKind`` the merge layer already understands.

Offsets are 0-based within a mode (offset 0 is the fixture's start address). Function and
capability names follow Open Fixture Library's vocabulary, in snake_case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .profiles import ChannelKind

SCHEMA = "livereact-fixture/1"

CATEGORIES = ("par", "wash_bar", "moving_head", "strobe", "dimmer", "laser", "other")
SOURCE_KINDS = ("builtin", "ofl", "manual_ai", "user")

_K = ChannelKind
_FUNCTION_KINDS: dict[str, ChannelKind] = {
    "dimmer": _K.DIMMER,
    "red": _K.COLOR, "green": _K.COLOR, "blue": _K.COLOR, "white": _K.COLOR,
    "amber": _K.COLOR, "uv": _K.COLOR, "color_wheel": _K.COLOR,
    "pan": _K.MOVEMENT, "pan_fine": _K.MOVEMENT, "tilt": _K.MOVEMENT, "tilt_fine": _K.MOVEMENT,
    # the native SHEHDS speed channel is OTHER (profiles.MovingHead.channel_kinds)
    "pan_tilt_speed": _K.OTHER,
    "shutter": _K.STROBE, "strobe": _K.STROBE,
    "gobo": _K.OTHER, "prism": _K.OTHER, "focus": _K.OTHER, "zoom": _K.OTHER,
    "macro": _K.OTHER, "mode": _K.OTHER, "reset": _K.OTHER,
    "laser_pattern": _K.LASER, "laser_x": _K.LASER, "laser_y": _K.LASER,
    "laser_rotate": _K.LASER, "laser_color": _K.LASER,
    "generic": _K.OTHER,
}
FUNCTIONS = tuple(_FUNCTION_KINDS)
# Ranges a fixture must never sit in at power-on or by default.
UNSAFE_CAPABILITIES = ("reset", "maintenance")
CAPABILITY_TYPES = FUNCTIONS + ("maintenance", "no_function")
LASER_INTENTS = ("blank", "line_static", "fan")


class DefinitionError(ValueError):
    """The data is not a readable ``livereact-fixture/1`` definition."""


def kind_of(function: str, category: str | None = None) -> ChannelKind:
    """The merge kind for a channel function. Every channel of a laser is LASER, so the
    finalize-stage laser gate holds the whole head dark, not just its pattern channels."""
    kind = _FUNCTION_KINDS[function]  # KeyError on an unknown function
    return ChannelKind.LASER if category == "laser" else kind


@dataclass(frozen=True)
class Capability:
    range: tuple[int, int]
    type: str
    color: str | None = None
    speed_hz: tuple[float, float] | None = None
    label: str = ""
    confidence: float | None = None   # manual reader only: 0..1
    source_page: int | None = None    # manual reader only: 1-based PDF page


@dataclass(frozen=True)
class Channel:
    offset: int
    function: str
    default: int = 0
    fine_of: int | None = None        # offset of the coarse channel this is the fine byte of
    capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class Mode:
    name: str
    channels: tuple[Channel, ...]


@dataclass(frozen=True)
class Source:
    kind: str
    ofl_key: str | None = None
    license: str | None = None
    attribution: str | None = None
    pdf_sha256: str | None = None
    confirmed: bool = False
    confirmed_at: str | None = None


@dataclass(frozen=True)
class LaserPhysical:
    laser_class: str | None = None
    power_mw: float | None = None


@dataclass(frozen=True)
class Physical:
    pan_deg: float | None = None
    tilt_deg: float | None = None
    pan_max_deg_s: float | None = None
    laser: LaserPhysical | None = None


@dataclass(frozen=True)
class LaserIntents:
    """Per intent, the DMX values (by channel offset) that produce it on this laser."""

    blank: dict[int, int] = field(default_factory=dict)
    line_static: dict[int, int] = field(default_factory=dict)
    fan: dict[int, int] = field(default_factory=dict)
    chop_channel: int | None = None


@dataclass(frozen=True)
class FixtureDefinition:
    id: str
    manufacturer: str
    model: str
    category: str
    source: Source
    modes: tuple[Mode, ...]
    physical: Physical = Physical()
    laser_intents: LaserIntents | None = None

    def mode(self, name: str | None = None) -> Mode:
        """The named mode, or the first one."""
        if name is None and self.modes:
            return self.modes[0]
        for m in self.modes:
            if m.name == name:
                return m
        raise KeyError(f"{self.id}: no mode {name!r}")

    def channel_kinds(self, mode: str | None = None) -> list[ChannelKind]:
        return [kind_of(c.function, self.category) for c in self.mode(mode).channels]

    def to_dict(self) -> dict[str, Any]:
        """The JSON form ``load_definition`` reads back to an equal definition."""
        out: dict[str, Any] = {
            "schema": SCHEMA, "id": self.id, "manufacturer": self.manufacturer,
            "model": self.model, "category": self.category,
            "source": _drop_none({
                "kind": self.source.kind, "ofl_key": self.source.ofl_key,
                "license": self.source.license, "attribution": self.source.attribution,
                "pdf_sha256": self.source.pdf_sha256, "confirmed": self.source.confirmed,
                "confirmed_at": self.source.confirmed_at}),
        }
        ph = self.physical
        phys = _drop_none({"pan_deg": ph.pan_deg, "tilt_deg": ph.tilt_deg, "pan_max_deg_s": ph.pan_max_deg_s})
        if ph.laser is not None:
            phys["laser"] = _drop_none({"class": ph.laser.laser_class, "power_mw": ph.laser.power_mw})
        if phys:
            out["physical"] = phys
        out["modes"] = [{"name": m.name, "channels": [_channel_dict(c) for c in m.channels]}
                        for m in self.modes]
        if self.laser_intents is not None:
            li = self.laser_intents
            out["laser_intents"] = {
                **{k: {str(o): v for o, v in getattr(li, k).items()} for k in LASER_INTENTS},
                "chop_channel": li.chop_channel}
        return out


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _channel_dict(c: Channel) -> dict[str, Any]:
    out: dict[str, Any] = {"offset": c.offset, "function": c.function, "default": c.default}
    if c.fine_of is not None:
        out["fine_of"] = c.fine_of
    if c.capabilities:
        out["capabilities"] = [_drop_none({
            "range": list(cap.range), "type": cap.type, "color": cap.color,
            "speed_hz": list(cap.speed_hz) if cap.speed_hz is not None else None,
            "label": cap.label or None, "confidence": cap.confidence,
            "source_page": cap.source_page}) for cap in c.capabilities]
    return out


# --- loading ---

def _req(d: dict, key: str, where: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise DefinitionError(f"{where}: missing {key!r}")
    return d[key]


def _int(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise DefinitionError(f"{where}: expected an integer, got {v!r}")
    return v


def _opt_num(v: Any, where: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise DefinitionError(f"{where}: expected a number, got {v!r}")
    return v


def _str(v: Any, where: str) -> str:
    if not isinstance(v, str):
        raise DefinitionError(f"{where}: expected a string, got {v!r}")
    return v


def _capability(d: dict, where: str) -> Capability:
    rng = _req(d, "range", where)
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        raise DefinitionError(f"{where}: range must be [lo, hi]")
    speed = d.get("speed_hz")
    if speed is not None:
        if isinstance(speed, (list, tuple)) and len(speed) == 2:
            speed = (float(_opt_num(speed[0], where)), float(_opt_num(speed[1], where)))
        else:
            s = float(_opt_num(speed, where))
            speed = (s, s)
    page = d.get("source_page")
    return Capability(
        range=(_int(rng[0], where), _int(rng[1], where)),
        type=_str(_req(d, "type", where), where),
        color=d.get("color"),
        speed_hz=speed,
        label=d.get("label") or "",
        confidence=_opt_num(d.get("confidence"), where),
        source_page=None if page is None else _int(page, where),
    )


def _channel(d: dict, where: str) -> Channel:
    fine = d.get("fine_of")
    caps = d.get("capabilities") or []
    if not isinstance(caps, list):
        raise DefinitionError(f"{where}: capabilities must be a list")
    return Channel(
        offset=_int(_req(d, "offset", where), where),
        function=_str(_req(d, "function", where), where),
        default=_int(d.get("default", 0), where),
        fine_of=None if fine is None else _int(fine, where),
        capabilities=tuple(_capability(c, f"{where} capability {i}") for i, c in enumerate(caps)),
    )


def _intent_values(d: Any, where: str) -> dict[int, int]:
    if not isinstance(d, dict):
        raise DefinitionError(f"{where}: expected {{offset: value}}")
    try:
        return {int(k): _int(v, where) for k, v in d.items()}
    except ValueError as e:
        raise DefinitionError(f"{where}: {e}") from None


def _from_dict(raw: dict) -> FixtureDefinition:
    if not isinstance(raw, dict):
        raise DefinitionError("a definition is a JSON object")
    if raw.get("schema", SCHEMA) != SCHEMA:
        raise DefinitionError(f"unsupported schema {raw.get('schema')!r} (expected {SCHEMA})")
    fid = _str(_req(raw, "id", "definition"), "id")
    src = _req(raw, "source", fid)
    if not isinstance(src, dict):
        raise DefinitionError(f"{fid}: source must be an object")
    source = Source(
        kind=_str(_req(src, "kind", f"{fid} source"), f"{fid} source"),
        ofl_key=src.get("ofl_key"), license=src.get("license"), attribution=src.get("attribution"),
        pdf_sha256=src.get("pdf_sha256"), confirmed=bool(src.get("confirmed", False)),
        confirmed_at=src.get("confirmed_at"),
    )
    ph = raw.get("physical") or {}
    lz = ph.get("laser")
    physical = Physical(
        pan_deg=_opt_num(ph.get("pan_deg"), f"{fid} physical"),
        tilt_deg=_opt_num(ph.get("tilt_deg"), f"{fid} physical"),
        pan_max_deg_s=_opt_num(ph.get("pan_max_deg_s"), f"{fid} physical"),
        laser=None if lz is None else LaserPhysical(
            laser_class=None if lz.get("class") is None else str(lz["class"]),
            power_mw=_opt_num(lz.get("power_mw"), f"{fid} physical.laser")),
    )
    modes_raw = _req(raw, "modes", fid)
    if not isinstance(modes_raw, list):
        raise DefinitionError(f"{fid}: modes must be a list")
    modes = []
    for mi, m in enumerate(modes_raw):
        name = _str(_req(m, "name", f"{fid} mode {mi}"), f"{fid} mode {mi}")
        chans = _req(m, "channels", f"{fid} mode {name}")
        if not isinstance(chans, list):
            raise DefinitionError(f"{fid} mode {name}: channels must be a list")
        modes.append(Mode(name, tuple(_channel(c, f"{fid} mode {name} channel {ci}")
                                      for ci, c in enumerate(chans))))
    li = raw.get("laser_intents")
    intents = None
    if li is not None:
        chop = li.get("chop_channel")
        intents = LaserIntents(
            **{k: _intent_values(li.get(k, {}), f"{fid} laser_intents.{k}") for k in LASER_INTENTS},
            chop_channel=None if chop is None else _int(chop, f"{fid} laser_intents.chop_channel"))
    return FixtureDefinition(
        id=fid,
        manufacturer=_str(_req(raw, "manufacturer", fid), "manufacturer"),
        model=_str(_req(raw, "model", fid), "model"),
        category=_str(_req(raw, "category", fid), "category"),
        source=source, modes=tuple(modes), physical=physical, laser_intents=intents,
    )


def load_definition(src: dict | str | Path) -> FixtureDefinition:
    """Read a definition from a dict or a JSON file. Raises ``DefinitionError`` when the
    structure is unreadable; call ``validate()`` for the semantic checks."""
    if isinstance(src, (str, Path)):
        try:
            src = json.loads(Path(src).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise DefinitionError(f"{src}: not JSON ({e})") from None
    return _from_dict(src)


# --- validation ---

def _validate_channel(c: Channel, by_offset: dict[int, Channel], where: str) -> list[str]:
    errs: list[str] = []
    if c.function not in _FUNCTION_KINDS:
        errs.append(f"{where}: unknown function {c.function!r}")
    if not 0 <= c.default <= 255:
        errs.append(f"{where}: default {c.default} outside 0-255")
    spans: list[tuple[int, int]] = []
    for cap in c.capabilities:
        lo, hi = cap.range
        if cap.type not in CAPABILITY_TYPES:
            errs.append(f"{where}: unknown capability type {cap.type!r}")
        if not (0 <= lo <= hi <= 255):
            errs.append(f"{where}: capability range {lo}-{hi} outside 0-255 or reversed")
            continue
        for slo, shi in spans:
            if lo <= shi and slo <= hi:
                errs.append(f"{where}: capability ranges {slo}-{shi} and {lo}-{hi} overlap")
        spans.append((lo, hi))
        if cap.type in UNSAFE_CAPABILITIES and lo <= c.default <= hi:
            errs.append(f"{where}: default {c.default} is inside a {cap.type} range ({lo}-{hi}); "
                        "the fixture would reset at power-on")
    is_fine = c.function.endswith("_fine")
    if is_fine and c.fine_of is None:
        errs.append(f"{where}: fine channel {c.function!r} has no fine_of")
    if c.fine_of is not None:
        coarse = by_offset.get(c.fine_of)
        if coarse is None or coarse is c:
            errs.append(f"{where}: fine_of {c.fine_of} is not another channel of this mode")
        elif coarse.fine_of is not None:
            errs.append(f"{where}: fine_of {c.fine_of} points at another fine channel")
        elif is_fine and coarse.function != c.function[:-len("_fine")]:
            errs.append(f"{where}: fine {c.function!r} paired with {coarse.function!r}")
    return errs


def validate(d: FixtureDefinition) -> list[str]:
    """Every problem with a loaded definition, as readable strings; empty means valid."""
    errs: list[str] = []
    if not d.id:
        errs.append("id is empty")
    if d.category not in CATEGORIES:
        errs.append(f"unknown category {d.category!r}")
    if d.source.kind not in SOURCE_KINDS:
        errs.append(f"unknown source kind {d.source.kind!r}")
    if not d.modes:
        errs.append("no modes")
    names = [m.name for m in d.modes]
    for n in sorted({n for n in names if names.count(n) > 1}):
        errs.append(f"duplicate mode name {n!r}")
    for m in d.modes:
        where = f"mode {m.name!r}"
        if not m.channels:
            errs.append(f"{where}: no channels")
            continue
        offsets = sorted(c.offset for c in m.channels)
        if offsets != list(range(len(m.channels))):
            errs.append(f"{where}: channel offsets must be 0..{len(m.channels) - 1} with no gaps "
                        f"or duplicates, got {offsets}")
        by_offset = {c.offset: c for c in m.channels}
        fines: dict[int, int] = {}
        for c in m.channels:
            errs += _validate_channel(c, by_offset, f"{where} channel {c.offset}")
            if c.fine_of is not None:
                fines[c.fine_of] = fines.get(c.fine_of, 0) + 1
        for coarse, n in sorted(fines.items()):
            if n > 1:
                errs.append(f"{where}: channel {coarse} has {n} fine channels")
        if d.laser_intents is not None:
            li = d.laser_intents
            for k in LASER_INTENTS:
                for off, v in getattr(li, k).items():
                    if off not in by_offset:
                        errs.append(f"{where}: laser_intents.{k} names channel {off}, which does not exist")
                    if not 0 <= v <= 255:
                        errs.append(f"{where}: laser_intents.{k} value {v} outside 0-255")
            if li.chop_channel is not None and li.chop_channel not in by_offset:
                errs.append(f"{where}: laser_intents.chop_channel {li.chop_channel} does not exist")
    return errs
