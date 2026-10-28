"""Open Fixture Library -> ``livereact-fixture/1``: a pure converter (roadmap item 24).

Reads one OFL fixture JSON (``availableChannels``, ``modes``, fine channel aliases, capability
``dmxRange`` / ``type`` / colour / speed, wheels, ``physical``) and returns a definition with
``source.kind = "ofl"``, unconfirmed: the user still tests it on the channel-test screen.
Capability types with no counterpart in the LiveReact vocabulary become ``generic``; matrix
and template channels are not supported and become ``generic`` too.

Search + fetch (item 25): ``OflService`` keeps OFL's register in the user folder and fetches one
fixture on demand through an injectable transport (tests use recorded data; the app's worker
thread in ui/ofl_worker.py runs it off the UI thread).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .definition import FixtureDefinition, load_definition

OFL_ATTRIBUTION = "Open Fixture Library (open-fixture-library.org)"

_COLOR_FUNCTIONS = {"red": "red", "green": "green", "blue": "blue", "white": "white",
                    "amber": "amber", "uv": "uv"}
_SIMPLE = {
    "Intensity": "dimmer", "Pan": "pan", "PanContinuous": "pan", "Tilt": "tilt", "TiltContinuous": "tilt",
    "PanTiltSpeed": "pan_tilt_speed", "StrobeSpeed": "strobe", "StrobeDuration": "strobe",
    "Zoom": "zoom", "Focus": "focus", "Prism": "prism", "PrismRotation": "prism",
    "Effect": "macro", "EffectSpeed": "macro", "EffectDuration": "macro", "EffectParameter": "macro",
    "Maintenance": "reset", "Generic": "generic", "NoFunction": "generic",
}
_CATEGORY_ORDER = (("Laser", "laser"), ("Moving Head", "moving_head"), ("Scanner", "moving_head"),
                   ("Strobe", "strobe"), ("Pixel Bar", "wash_bar"), ("Linear", "wash_bar"),
                   ("Color Changer", "par"), ("Dimmer", "dimmer"))


def _category(categories: list[str]) -> str:
    for ofl_name, ours in _CATEGORY_ORDER:
        if ofl_name in categories:
            return ours
    return "other"


def _caps(channel: dict) -> list[dict]:
    if "capabilities" in channel:
        return list(channel["capabilities"])
    if "capability" in channel:
        return [{"dmxRange": [0, 255], **channel["capability"]}]
    return []


def _wheel_of(name: str, cap: dict) -> str:
    return str(cap.get("wheel") or name)


def _cap_function(name: str, cap: dict) -> str:
    t = cap.get("type", "")
    if t == "ColorIntensity":
        return _COLOR_FUNCTIONS.get(str(cap.get("color", "")).lower(), "generic")
    if t in ("WheelSlot", "WheelShake", "WheelSlotRotation", "WheelRotation"):
        return "gobo" if "gobo" in _wheel_of(name, cap).lower() else "color_wheel"
    if t == "ColorPreset":
        return "color_wheel"
    if t == "ShutterStrobe":
        return "shutter" if cap.get("shutterEffect") in ("Open", "Closed") else "strobe"
    return _SIMPLE.get(t, "generic")


def _channel_function(name: str, channel: dict) -> str:
    caps = _caps(channel)
    funcs = [_cap_function(name, c) for c in caps if c.get("type") != "NoFunction"]
    if not funcs:
        return "generic"
    if "shutter" in funcs:
        return "shutter"
    return max(set(funcs), key=funcs.count) if len(set(funcs)) > 1 else funcs[0]


def _hz(v: Any) -> float | None:
    m = re.fullmatch(r"\s*([0-9.]+)\s*Hz\s*", str(v)) if v is not None else None
    return float(m.group(1)) if m else None


def _slot_color(fixture: dict, name: str, cap: dict) -> str | None:
    if cap.get("colors"):
        return str(cap["colors"][0]).lower()
    if cap.get("type") == "ColorIntensity":
        return None
    if cap.get("type") == "WheelSlot":
        wheel = (fixture.get("wheels") or {}).get(_wheel_of(name, cap)) or {}
        slots = wheel.get("slots") or []
        n = cap.get("slotNumber")
        if isinstance(n, (int, float)) and float(n).is_integer() and 1 <= n <= len(slots):
            slot = slots[int(n) - 1]
            if slot.get("type") == "Open":
                return "#ffffff"
            if slot.get("colors"):
                return str(slot["colors"][0]).lower()
    return None


def _cap_type(fn: str, cap: dict) -> str:
    t = cap.get("type", "")
    if t == "Maintenance":
        return "maintenance"
    if t == "NoFunction":
        return "no_function"
    return _cap_function("", cap) if fn not in ("color_wheel", "gobo") else fn


def _capability(fixture: dict, name: str, fn: str, cap: dict) -> dict:
    lo, hi = cap.get("dmxRange", [0, 255])
    out: dict[str, Any] = {"range": [int(lo), int(hi)], "type": _cap_type(fn, cap)}
    color = _slot_color(fixture, name, cap)
    if color:
        out["color"] = color
    s0, s1 = _hz(cap.get("speedStart", cap.get("speed"))), _hz(cap.get("speedEnd", cap.get("speed")))
    if s0 is not None and s1 is not None:
        out["speed_hz"] = [s0, s1]
    label = cap.get("comment") or " ".join(str(x) for x in (cap.get("type"), cap.get("shutterEffect"),
                                                            cap.get("color")) if x)
    out["label"] = str(label)
    return out


def _default(channel: dict) -> int:
    v = channel.get("defaultValue", 0)
    if isinstance(v, str) and v.endswith("%"):
        return max(0, min(255, round(float(v[:-1]) / 100 * 255)))
    try:
        return max(0, min(255, int(v)))
    except (TypeError, ValueError):
        return 0


def _manufacturer_from_key(key: str) -> str:
    return key.partition("/")[0].replace("-", " ").title()


def ofl_to_definition(ofl: dict, key: str, manufacturer: str | None = None,
                      license: str | None = None) -> FixtureDefinition:
    """Convert one OFL fixture (``key`` = ``"<manufacturer>/<fixture>"``, OFL's own path)."""
    available: dict[str, dict] = dict(ofl.get("availableChannels") or {})
    # fine aliases: "Pan fine" -> ("Pan", 1); deeper bytes (^2) are kept as generic channels
    fine_alias: dict[str, tuple[str, int]] = {}
    for name, ch in available.items():
        for i, alias in enumerate(ch.get("fineChannelAliases") or []):
            fine_alias[alias] = (name, i + 1)
    functions = {name: _channel_function(name, ch) for name, ch in available.items()}

    modes = []
    for m in ofl.get("modes") or []:
        names = list(m.get("channels") or [])
        channels = []
        for off, cname in enumerate(names):
            if isinstance(cname, str) and cname in available:
                fn = functions[cname]
                ch = available[cname]
                caps = [_capability(ofl, cname, fn, c) for c in _caps(ch)]
                channels.append({"offset": off, "function": fn, "default": _default(ch), "capabilities": caps})
            elif isinstance(cname, str) and cname in fine_alias and fine_alias[cname][1] == 1 \
                    and fine_alias[cname][0] in names:
                coarse = fine_alias[cname][0]
                cfn = functions[coarse]
                fn = f"{cfn}_fine" if cfn in ("pan", "tilt") else cfn
                channels.append({"offset": off, "function": fn, "fine_of": names.index(coarse)})
            else:
                # null (unused), matrix/template entries, a deeper fine byte: no known function
                channels.append({"offset": off, "function": "generic",
                                 "capabilities": [{"range": [0, 255], "type": "generic",
                                                   "label": str(cname) if cname else "unused"}]})
        modes.append({"name": str(m.get("shortName") or m.get("name") or f"{len(names)}ch"),
                      "channels": channels})

    focus = (ofl.get("physical") or {}).get("focus") or {}

    def _deg(v):
        return float(v) if isinstance(v, (int, float)) else None

    physical = {k: v for k, v in (("pan_deg", _deg(focus.get("panMax"))),
                                  ("tilt_deg", _deg(focus.get("tiltMax")))) if v is not None}
    authors = ", ".join((ofl.get("meta") or {}).get("authors") or [])
    category = _category(list(ofl.get("categories") or []))
    fixture_slug = key.partition("/")[2] or key
    raw = {
        "id": re.sub(r"[^a-z0-9]+", "_", f"ofl_{key}".lower()).strip("_")[:80],
        "manufacturer": manufacturer or _manufacturer_from_key(key),
        "model": str(ofl.get("name") or fixture_slug),
        "category": category,
        "source": {"kind": "ofl", "ofl_key": key, "license": license, "confirmed": False,
                   "attribution": f"{OFL_ATTRIBUTION}, fixture {key}" + (f", by {authors}" if authors else "")},
        "physical": physical,
        "modes": modes,
    }
    return load_definition(raw)


# ------------------------------------------------------------------ search + fetch (item 25)
# UNVERIFIED by hand: OFL's register / manufacturer list / per-fixture JSON in its GitHub repo.
# Check these three before release (docs/APP_ROADMAP.md item 25); nothing in the tests uses them.
REGISTER_URL = "https://raw.githubusercontent.com/OpenLightingProject/open-fixture-library/master/fixtures/register.json"
MANUFACTURERS_URL = "https://raw.githubusercontent.com/OpenLightingProject/open-fixture-library/master/fixtures/manufacturers.json"
FIXTURE_URL = "https://raw.githubusercontent.com/OpenLightingProject/open-fixture-library/master/fixtures/{key}.json"
TIMEOUT_S = 10.0
_KEY = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")


class OflUnavailable(Exception):
    """Open Fixture Library could not be reached (offline, timeout, bad reply) and nothing is cached."""


def urllib_transport(url: str, timeout: float) -> bytes:
    """The real transport (the app only; tests pass a recorded one)."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed https constants
        return r.read()


@dataclass(frozen=True)
class OflEntry:
    key: str             # "<manufacturer>/<fixture>"
    manufacturer: str
    name: str


def index_from_register(register: dict, manufacturers: dict | None = None) -> list[OflEntry]:
    """Entries from OFL's register (``filesystem`` map, or the ``manufacturers`` lists)."""
    mnames = {k: (v.get("name") if isinstance(v, dict) else None) for k, v in (manufacturers or {}).items()}
    out: dict[str, OflEntry] = {}
    for key, info in (register.get("filesystem") or {}).items():
        if not _KEY.match(key) or (isinstance(info, dict) and "redirectTo" in info):
            continue
        man = key.partition("/")[0]
        name = info.get("name") if isinstance(info, dict) else None
        out[key] = OflEntry(key, mnames.get(man) or _manufacturer_from_key(key),
                            name or key.partition("/")[2].replace("-", " ").title())
    for man, fixtures in (register.get("manufacturers") or {}).items():
        for fx in fixtures if isinstance(fixtures, list) else []:
            key = f"{man}/{fx}"
            if _KEY.match(key) and key not in out:
                out[key] = OflEntry(key, mnames.get(man) or _manufacturer_from_key(key),
                                    fx.replace("-", " ").title())
    return sorted(out.values(), key=lambda e: (e.manufacturer.lower(), e.name.lower()))


def search(entries: list[OflEntry], query: str, limit: int = 50) -> list[OflEntry]:
    """Every query word must appear (manufacturer, model or key); close spellings rank after."""
    import difflib
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    if not words:
        return []
    exact, fuzzy = [], []
    for e in entries:
        hay = f"{e.manufacturer} {e.name} {e.key}".lower()
        if all(w in hay for w in words):
            exact.append((hay.find(words[0]), e))
        else:
            tokens = re.split(r"[\s/-]+", hay)
            if all(difflib.get_close_matches(w, tokens, n=1, cutoff=0.8) for w in words):
                fuzzy.append(e)
    return [e for _pos, e in sorted(exact, key=lambda t: (t[0], t[1].manufacturer.lower(), t[1].name.lower()))
            ][:limit] + fuzzy[: max(0, limit - len(exact))]


class OflService:
    """OFL's register cached in the user folder, plus on-demand fixture fetches (also cached).
    Fixture data is fetched per user, never bundled with the app."""

    def __init__(self, cache_dir, transport=None, timeout: float = TIMEOUT_S) -> None:
        from pathlib import Path
        self.cache_dir = Path(cache_dir)
        self.transport = transport or urllib_transport
        self.timeout = timeout

    def _get_json(self, url: str):
        import json
        try:
            return json.loads(self.transport(url, self.timeout))
        except Exception as exc:  # noqa: BLE001 - any network / parse failure is "unavailable"
            raise OflUnavailable(f"Open Fixture Library unavailable ({type(exc).__name__}: {exc})") from exc

    def _write(self, path, data) -> None:
        import json
        import os
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def index(self, refresh: bool = False) -> list[OflEntry]:
        """The register (cached copy unless ``refresh``; a failed refresh falls back to the cache)."""
        import json
        reg_p, man_p = self.cache_dir / "register.json", self.cache_dir / "manufacturers.json"
        if refresh or not reg_p.is_file():
            try:
                register = self._get_json(REGISTER_URL)
                try:
                    manufacturers = self._get_json(MANUFACTURERS_URL)
                except OflUnavailable:
                    manufacturers = {}
                self._write(reg_p, register)
                self._write(man_p, manufacturers)
            except OflUnavailable:
                if not reg_p.is_file():
                    raise
        register = json.loads(reg_p.read_text(encoding="utf-8"))
        manufacturers = json.loads(man_p.read_text(encoding="utf-8")) if man_p.is_file() else {}
        return index_from_register(register, manufacturers)

    def fetch(self, key: str) -> dict:
        """One fixture's OFL JSON (cached after the first fetch)."""
        import json
        if not _KEY.match(key):
            raise ValueError(f"not an Open Fixture Library key: {key!r}")
        p = self.cache_dir / "fixtures" / f"{key.replace('/', '__')}.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        data = self._get_json(FIXTURE_URL.format(key=key))
        if not isinstance(data, dict) or "modes" not in data:
            raise OflUnavailable(f"Open Fixture Library returned no fixture for {key}")
        self._write(p, data)
        return data

    def import_fixture(self, entry: OflEntry, license: str | None = None) -> FixtureDefinition:
        return ofl_to_definition(self.fetch(entry.key), entry.key, manufacturer=entry.manufacturer, license=license)


def default_cache_dir():
    from .. import userdata
    return userdata.paths().fixtures / "ofl_cache"
