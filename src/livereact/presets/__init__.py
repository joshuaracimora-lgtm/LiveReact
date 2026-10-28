"""Presets: an event's colour scheme, genre and event type, resolved to what the show takes.

``resolve(event)`` is pure: it returns ``ManualParams`` field values, ``show_catalog()``
keyword arguments and a palette name, so live and sim apply a preset the same way (D-26).
Wiring it into the app (``App.apply_preset``) is Josh's (roadmap item 4); the catalog and
palette hooks are item 31. "Auto" everywhere is exactly today's show: no overrides at all.

Precedence: event-type ceilings, then genre, then scheme. Ceilings are limits, never raises:
``PresetEffect.apply_to`` only lowers brightness / strobe cap and disarms lasers.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import userdata

AUTO = "auto"
_DATA = Path(__file__).resolve().parent / "presets.json"


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, dict]]:
    """The packaged preset data: ``{"schemes"|"genres"|"event_types": {key: {...}}}``."""
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    return {k: raw[k] for k in ("schemes", "genres", "event_types")}


def choices(kind: str) -> list[tuple[str, str]]:
    """(key, label) pairs for a combo box, Auto first. ``kind``: schemes|genres|event_types."""
    return [(k, v["label"]) for k, v in catalog()[kind].items()]


@dataclass(frozen=True)
class PresetEvent:
    """A named event the operator can save and reload."""

    name: str = ""
    scheme: str = AUTO
    genre: str = AUTO
    event_type: str = AUTO


@dataclass(frozen=True)
class PresetEffect:
    manual: dict[str, Any] = field(default_factory=dict)       # ManualParams field -> value
    catalog_kwargs: dict[str, Any] = field(default_factory=dict)
    palette: str | None = None
    # event-type ceilings, enforced by apply_to (and by the UI: no arming when lasers are forbidden)
    master_brightness_max: float = 1.0
    strobe_cap_max: float = 1.0
    lasers_allowed: bool = True
    movement: bool = True   # no ManualParams field yet; read by the catalog hook (item 31)

    def apply_to(self, params):
        """A copy of ``params`` (a ManualParams) with the preset applied and ceilings enforced."""
        names = {f.name for f in fields(params)}
        p = replace(params, **{k: v for k, v in self.manual.items() if k in names})
        changes: dict[str, Any] = {}
        if p.master_brightness > self.master_brightness_max:
            changes["master_brightness"] = self.master_brightness_max
        if p.strobe_cap > self.strobe_cap_max:
            changes["strobe_cap"] = self.strobe_cap_max
        if not self.lasers_allowed and p.laser_enable:
            changes["laser_enable"] = False
        return replace(p, **changes) if changes else p


def _get(kind: str, key: str) -> dict:
    table = catalog()[kind]
    if key not in table:
        raise ValueError(f"unknown {kind[:-1].replace('_', ' ')} {key!r} (known: {list(table)})")
    return table[key]


def resolve(event: PresetEvent) -> PresetEffect:
    scheme = _get("schemes", event.scheme)
    genre = _get("genres", event.genre)
    ev = _get("event_types", event.event_type)
    manual = {**scheme["manual"], **genre["manual"]}   # genre over scheme
    ceil_b, ceil_s = float(ev["master_brightness_max"]), float(ev["strobe_cap_max"])
    if "master_brightness" in manual:
        manual["master_brightness"] = min(manual["master_brightness"], ceil_b)
    if "strobe_cap" in manual:
        manual["strobe_cap"] = min(manual["strobe_cap"], ceil_s)
    # a ceiling below today's default is itself a value to set
    if ceil_b < 1.0:
        manual.setdefault("master_brightness", ceil_b)
    if ceil_s < 1.0:
        manual.setdefault("strobe_cap", ceil_s)
    if not ev["lasers_allowed"]:
        manual["laser_enable"] = False
    return PresetEffect(manual=manual, catalog_kwargs=dict(genre["catalog"]), palette=scheme["palette"],
                        master_brightness_max=ceil_b, strobe_cap_max=ceil_s,
                        lasers_allowed=bool(ev["lasers_allowed"]), movement=bool(ev["movement"]))


# --- named events in the user folder ---

def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")[:60]
    if not s:
        raise ValueError("an event needs a name")
    return s


def save_event(event: PresetEvent) -> Path:
    resolve(event)  # refuse unknown keys before writing
    p = userdata.paths().events / f"{_slug(event.name)}.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(asdict(event), indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_event(name: str) -> PresetEvent:
    p = userdata.paths().events / f"{_slug(name)}.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    ev = PresetEvent(**{f.name: str(raw.get(f.name, getattr(PresetEvent, f.name, AUTO)))
                        for f in fields(PresetEvent)})
    resolve(ev)
    return ev


def list_events() -> list[str]:
    """Names of the saved events (unreadable files skipped)."""
    out = []
    for p in sorted(userdata.paths(create=False).events.glob("*.json")):
        try:
            out.append(str(json.loads(p.read_text(encoding="utf-8"))["name"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out
