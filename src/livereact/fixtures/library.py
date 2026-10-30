"""The fixture definition library: find a definition by ``"<id>@<mode>"``, list what is
available, save a user's definition.

Lookup order: the rig file's embedded ``definitions`` snapshot (so a shared rig carries its
own fixtures), then the user folder (``userdata.paths().fixtures``), then the packaged
``builtin/`` models -- except that a builtin id always resolves to the packaged model, so
neither a local copy nor a rig snapshot can redefine a native model (e.g. mark the
unconfirmed builtin laser confirmed).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .. import userdata
from .definition import DefinitionError, FixtureDefinition, load_definition, validate

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


@dataclass(frozen=True)
class LibraryEntry:
    definition: FixtureDefinition
    origin: str          # "builtin" | "user"
    path: Path


def parse_ref(ref: str) -> tuple[str, str | None]:
    """``"id@mode"`` -> (id, mode); a bare id means its first mode."""
    fid, _, mode = ref.partition("@")
    if not fid:
        raise ValueError(f"bad fixture reference {ref!r}")
    return fid, (mode or None)


def refs(d: FixtureDefinition) -> list[str]:
    return [f"{d.id}@{m.name}" for m in d.modes]


def _checked(d: FixtureDefinition, where: str) -> FixtureDefinition:
    errs = validate(d)
    if errs:
        raise DefinitionError(f"{where}: " + "; ".join(errs))
    return d


def _builtin_ids() -> set[str]:
    return {p.stem for p in BUILTIN_DIR.glob("*.json")}


def _find(fid: str, embedded: dict | None) -> FixtureDefinition:
    # builtin ids first, even over a rig's snapshot: a shared rig must not redefine a native model
    if fid in _builtin_ids():
        return _checked(load_definition(BUILTIN_DIR / f"{fid}.json"), f"builtin {fid}")
    if embedded and fid in embedded:
        d = load_definition(embedded[fid])
        if d.id != fid:
            raise DefinitionError(f"rig definitions: key {fid!r} holds definition {d.id!r}")
        return _checked(d, f"rig definition {fid}")
    if _SAFE_ID.match(fid):
        p = userdata.paths(create=False).fixtures / f"{fid}.json"
        if p.is_file():
            d = load_definition(p)
            if d.id != fid:
                raise DefinitionError(f"{p}: holds definition {d.id!r}")
            return _checked(d, str(p))
    raise KeyError(f"no fixture definition {fid!r}")


def resolve(ref: str, embedded: dict | None = None) -> tuple[FixtureDefinition, str]:
    """(definition, mode name) for ``ref``. KeyError when the id or mode is unknown,
    DefinitionError when the stored definition is unreadable or invalid."""
    fid, mode = parse_ref(ref)
    d = _find(fid, embedded)
    return d, d.mode(mode).name


def list_definitions() -> list[LibraryEntry]:
    """Builtin models first, then the user's; unreadable user files are skipped."""
    out = [LibraryEntry(load_definition(p), "builtin", p) for p in sorted(BUILTIN_DIR.glob("*.json"))]
    builtin = {e.definition.id for e in out}
    for p in sorted(userdata.paths(create=False).fixtures.glob("*.json")):
        try:
            d = load_definition(p)
        except (DefinitionError, OSError):
            continue
        if d.id == p.stem and d.id not in builtin and not validate(d):
            out.append(LibraryEntry(d, "user", p))
    return out


def rigs_using(fid: str) -> list[str]:
    """Names of the rigs in the user rigs folder whose slots reference definition ``fid``
    (the delete confirm box names them first). Unreadable files are skipped. A rig saved
    with its own ``definitions`` snapshot still counts: it keeps loading after a delete,
    but points at a definition the library no longer offers."""
    out: list[str] = []
    for p in sorted(userdata.paths(create=False).rigs.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            slots = data.get("slots") or []
        except (OSError, ValueError, AttributeError):
            continue
        ids: set[str] = set()
        for slot in slots:
            if isinstance(slot, dict) and slot.get("fixture"):
                try:
                    ids.add(parse_ref(str(slot["fixture"]))[0])
                except ValueError:
                    continue
        if fid in ids:
            out.append(str(data.get("name") or p.stem))
    return out


def delete_user_definition(fid: str) -> Path:
    """Delete ``<fid>.json`` from the user folder; returns the deleted path.
    ValueError for a built-in id (those ship with the app), KeyError when no user
    definition exists. Check ``rigs_using(fid)`` first and warn: a rig without its
    own embedded snapshot stops loading once the definition is gone."""
    if fid in _builtin_ids():
        raise ValueError(f"fixture {fid!r} is a built-in model; built-ins cannot be deleted")
    p = userdata.paths(create=False).fixtures / f"{fid}.json"
    if not _SAFE_ID.match(fid) or not p.is_file():
        raise KeyError(f"no user fixture definition {fid!r}")
    p.unlink()
    return p


def save_user_definition(d: FixtureDefinition) -> Path:
    """Validate and write ``d`` to the user folder as ``<id>.json`` (atomically)."""
    if not _SAFE_ID.match(d.id) or d.id in _RESERVED:
        raise ValueError(f"fixture id {d.id!r}: use lowercase letters, digits, '_', '-', '.'")
    if d.id in _builtin_ids():
        raise ValueError(f"fixture id {d.id!r} is a built-in model; save it under another id")
    _checked(d, d.id)
    p = userdata.paths().fixtures / f"{d.id}.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d.to_dict(), indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p
