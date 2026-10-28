"""Manual reader worker (roadmap item 28): find the DMX pages, read them once, cache the answer.

``read_manual()`` is the whole pipeline as a plain function: page finder (extract.py), then the
client (client.py) on the trimmed PDF only. The answer is cached in ``manuals/<sha256>/`` so a
manual is never paid for twice. ``ManualReaderWorker`` runs it in a ``QThread``; cancel stops it
before the paid call, or discards the answer of a call already in flight (it is still cached).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore

from .extract import ManualPages, find_dmx_pages

ANSWER_FILE = "answer.json"


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class ManualReadResult:
    data: dict
    pages: ManualPages
    from_cache: bool


def cached_answer(manuals_dir: str | Path, sha256: str) -> dict | None:
    p = Path(manuals_dir) / sha256 / ANSWER_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_manual(pdf_path: str | Path, reader, manuals_dir: str | Path | None = None,
                cancel: threading.Event | None = None, progress=lambda msg: None) -> ManualReadResult:
    """Pages -> (cache or one paid call) -> answer. Raises Cancelled, ManualReaderError, ValueError."""
    from .schema import check
    if manuals_dir is None:
        from ... import userdata
        manuals_dir = userdata.paths().manuals
    cancel = cancel or threading.Event()
    progress("Finding the DMX chart pages…")
    pages = find_dmx_pages(pdf_path, manuals_dir)
    cached = cached_answer(manuals_dir, pages.sha256)
    if cached is not None:
        check(cached)
        return ManualReadResult(cached, pages, True)
    if pages.trimmed_pdf is None:
        raise ValueError("No pages in this PDF look like a DMX chart. Enter the channels by hand instead.")
    if cancel.is_set():
        raise Cancelled()
    progress(f"Reading {len(pages.best)} page(s) with Claude…")
    result = reader.read(Path(pages.trimmed_pdf).read_bytes())
    out = Path(manuals_dir) / pages.sha256 / ANSWER_FILE
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(result.data, indent=1), encoding="utf-8")
    tmp.replace(out)
    if cancel.is_set():
        raise Cancelled()
    return ManualReadResult(result.data, pages, False)


class ManualReaderWorker(QtCore.QThread):
    done = QtCore.Signal(object)      # ManualReadResult
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(self, pdf_path, reader, manuals_dir=None, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.pdf_path, self.reader, self.manuals_dir = pdf_path, reader, manuals_dir
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        from .client import ManualReaderError
        try:
            res = read_manual(self.pdf_path, self.reader, self.manuals_dir, self._cancel, self.progress.emit)
        except Cancelled:
            self.cancelled.emit()
            return
        except (ManualReaderError, ValueError, OSError) as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(res)
