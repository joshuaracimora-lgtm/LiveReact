"""What the manual reader asks Claude to fill (roadmap item 27), and how its answer becomes a draft
``livereact-fixture/1`` definition.

The schema is a subset of the definition (modes, channels, capabilities) plus a per-capability
``confidence`` and ``source_page``, so the review screen can highlight what to double-check and
show the page it came from. Structured outputs guarantee the shape; ``check()`` enforces the
ranges the schema keywords cannot (0-255, 0-1). The result is always ``manual_ai`` and unconfirmed.
"""

from __future__ import annotations

from typing import Any

from ..definition import CAPABILITY_TYPES, CATEGORIES, FUNCTIONS, FixtureDefinition, load_definition


class ManualSchemaError(ValueError):
    """The reader's answer does not fit the schema."""


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_INT = {"type": "integer"}
_STR = {"type": "string"}

CAPABILITY = _obj({
    "range_start": _INT, "range_end": _INT,
    "type": {"type": "string", "enum": list(CAPABILITY_TYPES)},
    "label": _STR,
    "color": {"type": ["string", "null"]},
    "confidence": {"type": "number"},
    "source_page": _INT,
})
CHANNEL = _obj({
    "offset": _INT,
    "function": {"type": "string", "enum": list(FUNCTIONS)},
    "default": _INT,
    "fine_of": {"type": ["integer", "null"]},
    "confidence": {"type": "number"},
    "source_page": _INT,
    "capabilities": {"type": "array", "items": CAPABILITY},
})
MANUAL_SCHEMA = _obj({
    "manufacturer": _STR,
    "model": _STR,
    "category": {"type": "string", "enum": list(CATEGORIES)},
    "modes": {"type": "array", "items": _obj({"name": _STR, "channels": {"type": "array", "items": CHANNEL}})},
    "notes": _STR,
})


def _need(d: Any, keys: list[str], where: str) -> None:
    if not isinstance(d, dict):
        raise ManualSchemaError(f"{where}: expected an object")
    missing = [k for k in keys if k not in d]
    if missing:
        raise ManualSchemaError(f"{where}: missing {missing}")


def check(data: Any) -> None:
    """Raise ManualSchemaError unless ``data`` fits MANUAL_SCHEMA and its numeric ranges."""
    _need(data, MANUAL_SCHEMA["required"], "answer")
    if data["category"] not in CATEGORIES:
        raise ManualSchemaError(f"unknown category {data['category']!r}")
    if not isinstance(data["modes"], list) or not data["modes"]:
        raise ManualSchemaError("no modes")
    for m in data["modes"]:
        _need(m, ["name", "channels"], "mode")
        for c in m["channels"]:
            _need(c, CHANNEL["required"], f"mode {m['name']!r} channel")
            if c["function"] not in FUNCTIONS:
                raise ManualSchemaError(f"unknown function {c['function']!r}")
            if not (isinstance(c["default"], int) and 0 <= c["default"] <= 255):
                raise ManualSchemaError(f"channel {c['offset']}: default {c['default']!r} outside 0-255")
            if not 0 <= float(c["confidence"]) <= 1:
                raise ManualSchemaError(f"channel {c['offset']}: confidence outside 0-1")
            for cap in c["capabilities"]:
                _need(cap, CAPABILITY["required"], f"channel {c['offset']} capability")
                if cap["type"] not in CAPABILITY_TYPES:
                    raise ManualSchemaError(f"unknown capability type {cap['type']!r}")
                if not 0 <= float(cap["confidence"]) <= 1:
                    raise ManualSchemaError(f"channel {c['offset']}: capability confidence outside 0-1")


def to_definition(data: dict, pdf_sha256: str, page_map: dict[int, int] | None = None,
                  fixture_id: str | None = None) -> FixtureDefinition:
    """A draft definition (``manual_ai``, unconfirmed). ``page_map`` maps the trimmed PDF's pages
    (what the model saw) back to the original manual's pages."""
    import re
    check(data)
    page_map = page_map or {}

    def page(p: int) -> int:
        return page_map.get(int(p), int(p))

    modes = []
    for m in data["modes"]:
        chans = []
        for c in m["channels"]:
            ch: dict[str, Any] = {"offset": c["offset"], "function": c["function"], "default": c["default"],
                                  "capabilities": [{
                                      "range": [cap["range_start"], cap["range_end"]], "type": cap["type"],
                                      "label": cap["label"], "color": cap["color"],
                                      "confidence": float(cap["confidence"]), "source_page": page(cap["source_page"]),
                                  } for cap in c["capabilities"]]}
            if c["fine_of"] is not None:
                ch["fine_of"] = c["fine_of"]
            if not ch["capabilities"]:
                # keep the channel-level confidence where the review screen can see it
                ch["capabilities"] = [{"range": [0, 255], "type": c["function"], "label": c["function"],
                                       "confidence": float(c["confidence"]), "source_page": page(c["source_page"])}]
            chans.append(ch)
        modes.append({"name": m["name"], "channels": chans})
    fid = fixture_id or re.sub(r"[^a-z0-9]+", "_", f"{data['manufacturer']} {data['model']}".lower()).strip("_")[:80]
    return load_definition({
        "id": fid or f"manual_{pdf_sha256[:12]}", "manufacturer": data["manufacturer"], "model": data["model"],
        "category": data["category"],
        "source": {"kind": "manual_ai", "pdf_sha256": pdf_sha256, "confirmed": False},
        "modes": modes,
    })
