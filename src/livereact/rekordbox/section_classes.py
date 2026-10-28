"""Section classes: named kinds of section (a snare-roll drop, a clap drop ...) and what each looks like.

``<repo>/section_classes.json`` (tracked; no track names) holds the class definitions the Track
Classifier agent refines:

    {"schema": 1,
     "classes": [
       {"name": "drop_full", "kind": "drop", "rule": {"bars": [8, 999]},
        "scene": null, "approved": false, "notes": "..."}],
     "palette_by_profile": {"approved": false, "high": [...], "mid": [...], "low": [...]}}

A class matches a planned section when the kinds agree and every ``rule`` feature
(rekordbox/sections.py FEATURE_NAMES) lies in its [min, max]; the first match wins, a
``"rule": null`` never matches (a class named but not yet defined). The planner tags every
section with its class (``PlannedSection.cls``) but only an **approved** class changes the show:
its ``scene`` replaces the bucket's pick, and the scene joins the show catalog
(scenes/catalog.py ``show_catalog``) so the live show and the simulator play the same look.
``approved`` and ``scene`` are Josh's to set -- the nightly orchestrator rejects an agent diff
that changes them. ``palette_by_profile`` (decision D-23) picks the drop palette from the track's
frequency profile once approved; a look note's ``hook_palette`` still wins.

Loading is lenient: a malformed file or class logs a warning and is ignored, so a typo can never
take the live show down. tests/test_section_classes.py holds the shipped file to the strict schema.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .sections import FEATURE_NAMES, SectionFeatures

log = logging.getLogger(__name__)

CLASSES_PATH = Path(__file__).resolve().parents[3] / "section_classes.json"
SCHEMA = 1
PROFILES = ("high", "mid", "low")


@dataclass(frozen=True)
class SectionClass:
    name: str
    kind: str
    rule: dict | None                 # feature -> (min, max); None = not defined yet, never matches
    scene: str | None = None
    approved: bool = False
    notes: str = ""

    def matches(self, f: SectionFeatures) -> bool:
        if self.rule is None or f.kind != self.kind:
            return False
        return all(lo <= f.get(k) <= hi for k, (lo, hi) in self.rule.items())


@dataclass(frozen=True)
class ClassBook:
    classes: tuple[SectionClass, ...] = ()
    palette_approved: bool = False
    palette_by_profile: dict = field(default_factory=dict)   # profile -> tuple of palette names

    def classify(self, f: SectionFeatures) -> SectionClass | None:
        return next((c for c in self.classes if c.matches(f)), None)

    def approved_scenes(self) -> list[str]:
        return [c.scene for c in self.classes if c.approved and c.scene]

    def palette_for(self, profile: str, seed: int, position: float | None = None) -> str:
        """The profile's list runs darkest to brightest; ``position`` (0..1, sections.profile_position)
        picks along it, so the bassiest track in a profile gets its deepest colour. No position: by seed."""
        names = self.palette_by_profile.get(profile) or ()
        if not (self.palette_approved and names):
            return ""
        if position is None:
            return names[seed % len(names)]
        return names[min(len(names) - 1, int(position * len(names)))]


EMPTY = ClassBook()


def validate(d: dict) -> list[str]:
    """Problems with a section_classes.json document (empty list = valid)."""
    from ..engines.palette import PALETTE_RGB
    from ..scenes.library import ALL_SCENES
    from .mapping import SECTION_TO_BUCKET
    scenes = {cls.name: cls.bucket.value for cls in ALL_SCENES}
    errs: list[str] = []
    if not isinstance(d, dict) or d.get("schema") != SCHEMA:
        return [f"schema must be {SCHEMA}"]
    unknown_top = set(d) - {"schema", "classes", "palette_by_profile", "_doc"}
    if unknown_top:
        errs.append(f"unknown keys {sorted(unknown_top)}")
    seen = set()
    for i, c in enumerate(d.get("classes") or []):
        where = f"classes[{i}] {c.get('name')!r}"
        extra = set(c) - {"name", "kind", "rule", "scene", "approved", "notes"}
        if extra:
            errs.append(f"{where}: unknown keys {sorted(extra)}")
        if not c.get("name") or c["name"] in seen:
            errs.append(f"{where}: name missing or duplicated")
        seen.add(c.get("name"))
        if c.get("kind") not in SECTION_TO_BUCKET:
            errs.append(f"{where}: kind must be one of {sorted(SECTION_TO_BUCKET)}")
        rule = c.get("rule")
        if rule is not None:
            if not isinstance(rule, dict):
                errs.append(f"{where}: rule must be an object or null")
            else:
                for k, v in rule.items():
                    if k not in FEATURE_NAMES:
                        errs.append(f"{where}: unknown feature {k!r} (features: {', '.join(FEATURE_NAMES)})")
                    elif not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)
                              and v[0] <= v[1]):
                        errs.append(f"{where}: {k} must be [min, max]")
        sc = c.get("scene")
        if sc is not None:
            if sc not in scenes:
                errs.append(f"{where}: scene {sc!r} is not in scenes/library.py")
            elif scenes[sc] != SECTION_TO_BUCKET.get(c.get("kind"), ""):
                errs.append(f"{where}: scene {sc!r} is a {scenes[sc]} look, the class is a "
                            f"{SECTION_TO_BUCKET.get(c.get('kind'))} section")
        if not isinstance(c.get("approved", False), bool):
            errs.append(f"{where}: approved must be true/false")
        if c.get("approved") and not sc:
            errs.append(f"{where}: approved without a scene")
    pb = d.get("palette_by_profile")
    if pb is not None:
        if set(pb) - {"approved", *PROFILES}:
            errs.append(f"palette_by_profile: unknown keys {sorted(set(pb) - {'approved', *PROFILES})}")
        for prof in PROFILES:
            for name in pb.get(prof) or []:
                if name not in PALETTE_RGB:
                    errs.append(f"palette_by_profile.{prof}: {name!r} is not a palette name")
    return errs


def from_dict(d: dict) -> ClassBook:
    errs = validate(d)
    if errs:
        raise ValueError("; ".join(errs))
    classes = tuple(SectionClass(name=c["name"], kind=c["kind"],
                                 rule=None if c.get("rule") is None else {k: (float(v[0]), float(v[1]))
                                                                          for k, v in c["rule"].items()},
                                 scene=c.get("scene"), approved=bool(c.get("approved", False)),
                                 notes=str(c.get("notes", "")))
                    for c in d.get("classes") or [])
    pb = d.get("palette_by_profile") or {}
    return ClassBook(classes=classes, palette_approved=bool(pb.get("approved", False)),
                     palette_by_profile={p: tuple(pb.get(p) or ()) for p in PROFILES})


def load(path: str | Path | None = None) -> ClassBook:
    """The class book; EMPTY (no classes) when the file is missing or invalid -- never raises."""
    p = Path(path) if path else CLASSES_PATH
    if not p.exists():
        return EMPTY
    try:
        return from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        log.warning("section_classes.json ignored (%s); planning without section classes", exc)
        return EMPTY
