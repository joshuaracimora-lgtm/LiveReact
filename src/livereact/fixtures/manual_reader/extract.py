"""Manual page finder (roadmap item 26): which pages of a fixture manual hold the DMX chart.

Per-page text with ``pypdf``, scored for DMX-chart signals ("DMX", "CH1", "channel", value
ranges like ``000-255``, "mode"). Pages with almost no text are flagged as scanned (their chart
is an image the text score cannot see). The best pages, at most 15, are written as a trimmed PDF
to ``manuals/<sha256>/`` with a page map, so the reader sends only those pages and can cite the
original page numbers.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_PAGES = 15
SCANNED_CHARS = 40          # fewer extracted characters than this: treat the page as an image

_SIGNALS = (
    (re.compile(r"\bDMX\b", re.I), 3.0),
    (re.compile(r"\bCH\s?\d{1,2}\b", re.I), 2.0),
    (re.compile(r"\bchannels?\b", re.I), 1.0),
    (re.compile(r"\b(?:mode|personality)\b", re.I), 1.0),
    (re.compile(r"\b\d{1,2}\s?CH\b", re.I), 2.0),
)
_RANGE = re.compile(r"\b(\d{1,3})\s?[-~–]\s?(\d{1,3})\b")


@dataclass(frozen=True)
class PageScore:
    number: int          # 1-based page in the original PDF
    score: float
    chars: int
    scanned: bool


@dataclass(frozen=True)
class ManualPages:
    sha256: str
    pages: list[PageScore]
    best: list[int]                      # original page numbers chosen, in order
    scanned: list[int]
    trimmed_pdf: Path | None = None
    page_map: dict[int, int] = field(default_factory=dict)   # trimmed page (1-based) -> original


def score_text(text: str) -> float:
    """How much a page looks like a DMX channel chart."""
    s = 0.0
    for rx, w in _SIGNALS:
        s += w * min(len(rx.findall(text)), 10)
    ranges = sum(1 for a, b in _RANGE.findall(text) if int(a) <= int(b) <= 255)
    return s + min(ranges, 20) * 1.5


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def score_pages(pdf_path: str | Path) -> list[PageScore]:
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    out = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - a page pypdf cannot read is treated as scanned
            text = ""
        chars = len(text.strip())
        out.append(PageScore(i + 1, score_text(text), chars, chars < SCANNED_CHARS))
    return out


def choose_pages(pages: list[PageScore], max_pages: int = MAX_PAGES, min_score: float = 4.0) -> list[int]:
    """The best-scoring text pages; scanned pages fill in when the text finds too little."""
    text_hits = sorted((p for p in pages if not p.scanned and p.score >= min_score),
                       key=lambda p: (-p.score, p.number))[:max_pages]
    chosen = {p.number for p in text_hits}
    if len(chosen) < 2:
        for p in pages:
            if p.scanned and len(chosen) < max_pages:
                chosen.add(p.number)
    return sorted(chosen)


def find_dmx_pages(pdf_path: str | Path, manuals_dir: str | Path | None = None,
                   max_pages: int = MAX_PAGES) -> ManualPages:
    """Score, choose, and (when ``manuals_dir`` is given or defaulted) write the trimmed PDF."""
    from pypdf import PdfReader, PdfWriter
    pdf_path = Path(pdf_path)
    digest = sha256_of(pdf_path)
    pages = score_pages(pdf_path)
    best = choose_pages(pages, max_pages)
    scanned = [p.number for p in pages if p.scanned]
    if manuals_dir is None:
        from ... import userdata
        manuals_dir = userdata.paths().manuals
    out_dir = Path(manuals_dir) / digest
    out_dir.mkdir(parents=True, exist_ok=True)
    trimmed = None
    page_map: dict[int, int] = {}
    if best:
        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()
        for n in best:
            writer.add_page(reader.pages[n - 1])
            page_map[len(page_map) + 1] = n
        trimmed = out_dir / "trimmed.pdf"
        with open(trimmed, "wb") as fh:
            writer.write(fh)
    result = ManualPages(digest, pages, best, scanned, trimmed, page_map)
    (out_dir / "pages.json").write_text(json.dumps({
        "sha256": digest, "source_name": pdf_path.name, "best": best, "scanned": scanned,
        "page_map": {str(k): v for k, v in page_map.items()},
        "pages": [asdict(p) for p in pages]}, indent=1), encoding="utf-8")
    return result
