"""Manual reader review (roadmap item 28): the reader's answer becomes a saved, unconfirmed
``manual_ai`` draft, plus the list of cells the user should double-check on the channel-test
screen (low confidence, with the manual page each came from)."""

from __future__ import annotations

from dataclasses import dataclass

from ..definition import FixtureDefinition, validate
from .schema import to_definition
from .worker import ManualReadResult

LOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Doubt:
    offset: int          # channel offset in the mode
    label: str
    confidence: float
    source_page: int | None


def draft(result: ManualReadResult) -> FixtureDefinition:
    """The reader's answer as a draft (unconfirmed). ValueError lists what is wrong with it."""
    d = to_definition(result.data, result.pages.sha256, result.pages.page_map)
    errs = validate(d)
    if errs:
        raise ValueError("The manual reader's answer is not a valid fixture: " + "; ".join(errs))
    return d


def save_draft(d: FixtureDefinition, save=None):
    """Save the draft in the user library (still unconfirmed: a laser from it stays dark)."""
    if d.source.confirmed:
        raise ValueError("a manual reader draft must not be saved as confirmed")
    if save is None:
        from ..library import save_user_definition as save
    return save(d)


def doubts(d: FixtureDefinition, mode: str | None = None, threshold: float = LOW_CONFIDENCE) -> list[Doubt]:
    out = []
    for c in d.mode(mode).channels:
        for cap in c.capabilities:
            if cap.confidence is not None and cap.confidence < threshold:
                out.append(Doubt(c.offset, cap.label or cap.type, cap.confidence, cap.source_page))
    return out
