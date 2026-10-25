"""PSSI phrase kinds -> LiveReact sections, buckets and sim timeline kinds. Data, not code.

rekordbox labels phrases per *mood*: high-mood tracks get intro/up/down/chorus/
outro, mid/low-mood tracks get intro/verse/bridge/chorus/outro. LiveReact thinks
in sections (what the lights should be doing) and the metronome's energy buckets
(``scenes/base.py`` EnergyBucket: break/build/drop/peak/outro). This table is the
first guess at the correspondence; Josh tunes it from real sets (Gate 1/2 data).
An optional ``rekordbox_map.json`` at the repo root overrides any cell, e.g.
``{"sections": {"mid": {"verse1": "drop"}}, "buckets": {"verse": "drop"}}``.

Open question recorded in the plan: house tracks often carry the main groove in
what rekordbox calls a mid-mood *verse*; if that reads as BREAK the lights will
sit too low. The shadow-mode agree/disagree log is what answers it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .pssi import MOOD_HIGH, MOOD_LOW, MOOD_MID, MOOD_NAMES, PhraseTable

log = logging.getLogger(__name__)

# section kinds LiveReact understands (strings; the snapshot carries them verbatim)
SECTION_INTRO, SECTION_BUILD, SECTION_BREAKDOWN, SECTION_DROP = "intro", "build", "breakdown", "drop"
SECTION_VERSE, SECTION_OUTRO = "verse", "outro"

_LABEL_TO_SECTION: dict[str, dict[str, str]] = {
    "high": {"intro": SECTION_INTRO, "up": SECTION_BUILD, "down": SECTION_BREAKDOWN,
             "chorus": SECTION_DROP, "outro": SECTION_OUTRO},
    "mid": {"intro": SECTION_INTRO, "bridge": SECTION_BUILD, "chorus": SECTION_DROP,
            "outro": SECTION_OUTRO},
    "low": {"intro": SECTION_INTRO, "bridge": SECTION_BUILD, "chorus": SECTION_DROP,
            "outro": SECTION_OUTRO},
}
# every verse variant (verse1..verse6, verse1b, ...) -> verse
_VERSE_DEFAULT = SECTION_VERSE

SECTION_TO_BUCKET: dict[str, str] = {
    SECTION_INTRO: "break",
    SECTION_BUILD: "build",
    SECTION_BREAKDOWN: "break",
    SECTION_DROP: "drop",
    SECTION_VERSE: "break",
    SECTION_OUTRO: "outro",
}

# sim/timeline.py Marker kinds (drop, breakdown, groove, build, fill, outro, whiteout)
SECTION_TO_TIMELINE_KIND: dict[str, str] = {
    SECTION_INTRO: "breakdown",
    SECTION_BUILD: "build",
    SECTION_BREAKDOWN: "breakdown",
    SECTION_DROP: "drop",
    SECTION_VERSE: "groove",
    SECTION_OUTRO: "outro",
}

_OVERRIDES_LOADED = False
OVERRIDES_PATH = Path(__file__).resolve().parents[3] / "rekordbox_map.json"


def load_overrides(path: str | Path | None = None) -> None:
    """Merge a JSON override file into the tables (idempotent, missing file = no-op)."""
    global _OVERRIDES_LOADED
    p = Path(path) if path else OVERRIDES_PATH
    if not p.exists():
        _OVERRIDES_LOADED = True
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("rekordbox_map.json unreadable (%s); using built-in mapping", exc)
        _OVERRIDES_LOADED = True
        return
    for mood_name, table in (data.get("sections") or {}).items():
        _LABEL_TO_SECTION.setdefault(mood_name, {}).update({str(k): str(v) for k, v in table.items()})
    SECTION_TO_BUCKET.update({str(k): str(v) for k, v in (data.get("buckets") or {}).items()})
    SECTION_TO_TIMELINE_KIND.update({str(k): str(v) for k, v in (data.get("timeline") or {}).items()})
    _OVERRIDES_LOADED = True
    log.info("rekordbox mapping overrides loaded from %s", p)


def section_for(mood: int, label: str) -> str:
    """Section kind for a phrase label under a mood ("high"/"mid"/"low" by number)."""
    if not _OVERRIDES_LOADED:
        load_overrides()
    mood_name = MOOD_NAMES.get(mood, "mid")
    table = _LABEL_TO_SECTION.get(mood_name, _LABEL_TO_SECTION["mid"])
    if label in table:
        return table[label]
    if label.startswith("verse"):
        return table.get("verse", _VERSE_DEFAULT)
    return SECTION_VERSE if mood in (MOOD_MID, MOOD_LOW) else SECTION_BREAKDOWN


def bucket_for_section(section: str) -> str:
    return SECTION_TO_BUCKET.get(section, "break")


def timeline_kind_for_section(section: str) -> str:
    return SECTION_TO_TIMELINE_KIND.get(section, "groove")


def sections_of(table: PhraseTable) -> list[tuple[int, int, str, str]]:
    """[(beat, end_beat, label, section_kind)] for a table, in order."""
    return [(p.beat, p.end_beat, p.label, section_for(table.mood, p.label)) for p in table.phrases]


__all__ = [
    "MOOD_HIGH", "MOOD_MID", "MOOD_LOW",
    "SECTION_INTRO", "SECTION_BUILD", "SECTION_BREAKDOWN", "SECTION_DROP", "SECTION_VERSE", "SECTION_OUTRO",
    "SECTION_TO_BUCKET", "SECTION_TO_TIMELINE_KIND",
    "section_for", "bucket_for_section", "timeline_kind_for_section", "sections_of", "load_overrides",
]
