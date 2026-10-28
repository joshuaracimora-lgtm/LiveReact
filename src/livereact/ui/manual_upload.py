"""Wizard page: upload a fixture's PDF manual and let Claude read its DMX chart (roadmap item 28).

Greyed out without an Anthropic API key or when offline. The first use asks for consent ("the
manual's DMX pages are sent to Anthropic"), remembered in settings.py. Before anything is sent it
shows the page finder's result and a cost estimate; a manual already read is free (cached by its
sha256). The answer is saved as an unconfirmed ``manual_ai`` draft and handed to the channel-test
screen, where low-confidence cells are flagged with their manual page.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6 import QtCore, QtWidgets

CONSENT_KEY = "manual_reader_consent"
CONSENT_TEXT = ("I agree that this manual's DMX chart pages are sent to Anthropic to be read "
                "(once per manual; billed to my API key).")


def _online() -> bool:
    return os.environ.get("LIVEREACT_OFFLINE") != "1"


class UploadManualPage(QtWidgets.QWidget):
    drafted = QtCore.Signal(object)       # the saved draft FixtureDefinition

    def __init__(self, reader_factory=None, key_provider=None, online=_online, settings=None,
                 pick_pdf=None, manuals_dir=None, save=None, threaded: bool = True,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        from ..fixtures.manual_reader.client import ManualReader, get_api_key
        self._key_provider = key_provider or get_api_key
        self._reader_factory = reader_factory or (lambda: ManualReader(key_provider=self._key_provider))
        self._online = online
        self._settings = settings
        self._pick = pick_pdf or self._file_dialog
        self._manuals_dir = manuals_dir
        self._save = save
        self.threaded = threaded
        self.reader = None
        self.pdf_path: Path | None = None
        self.worker = None
        self.doubts = []

        v = QtWidgets.QVBoxLayout(self)
        self.l_avail = QtWidgets.QLabel("")
        self.l_avail.setWordWrap(True)
        v.addWidget(self.l_avail)
        self.cb_consent = QtWidgets.QCheckBox(CONSENT_TEXT)
        v.addWidget(self.cb_consent)
        row = QtWidgets.QHBoxLayout()
        self.b_pick = QtWidgets.QPushButton("Choose PDF…")
        self.l_file = QtWidgets.QLabel("")
        row.addWidget(self.b_pick)
        row.addWidget(self.l_file, 1)
        v.addLayout(row)
        self.l_cost = QtWidgets.QLabel("")
        self.l_cost.setWordWrap(True)
        v.addWidget(self.l_cost)
        row2 = QtWidgets.QHBoxLayout()
        self.b_read = QtWidgets.QPushButton("Read the manual")
        self.b_cancel = QtWidgets.QPushButton("Cancel")
        row2.addStretch(1)
        row2.addWidget(self.b_cancel)
        row2.addWidget(self.b_read)
        v.addLayout(row2)
        self.l_status = QtWidgets.QLabel("")
        self.l_status.setWordWrap(True)
        v.addWidget(self.l_status)
        v.addStretch(1)

        self.cb_consent.setChecked(bool(self._settings_obj().extra.get(CONSENT_KEY)))
        self.cb_consent.toggled.connect(self._consent_changed)
        self.b_pick.clicked.connect(self.pick)
        self.b_read.clicked.connect(self.read)
        self.b_cancel.clicked.connect(self.cancel)
        # no key / network check until the page is shown (refresh()): building a wizard never
        # touches the keychain
        for w in (self.cb_consent, self.b_pick, self.b_read, self.b_cancel):
            w.setEnabled(False)

    # --- state ---
    def _settings_obj(self):
        if self._settings is None:
            from ..settings import Settings
            self._settings = Settings.load()
        return self._settings

    def availability(self) -> str:
        """'' when usable, else why not (shown, and everything is greyed out)."""
        if not self._online():
            return "Offline: the manual reader needs the internet. Enter the channels by hand instead."
        if not self._key_provider():
            return "Add an Anthropic API key in Settings to use the manual reader."
        return ""

    def refresh(self) -> None:
        why = self.availability()
        self.l_avail.setText(why or "Claude reads the DMX chart pages of the manual and drafts the fixture; "
                                    "you then test and confirm every channel.")
        usable = not why
        busy = self.worker is not None and self.worker.isRunning()
        self.cb_consent.setEnabled(usable)
        self.b_pick.setEnabled(usable and not busy)
        self.b_read.setEnabled(usable and not busy and self.pdf_path is not None and self.cb_consent.isChecked())
        self.b_cancel.setEnabled(busy)

    def _consent_changed(self, on: bool) -> None:
        s = self._settings_obj()
        if on:
            s.extra[CONSENT_KEY] = True
        else:
            s.extra.pop(CONSENT_KEY, None)
        s.save()
        self.refresh()

    # --- actions ---
    def _file_dialog(self) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Fixture manual", "", "PDF (*.pdf)")
        return path

    def pick(self) -> None:
        path = self._pick()
        if not path:
            return
        self.pdf_path = Path(path)
        self.l_file.setText(self.pdf_path.name)
        self.estimate()
        self.refresh()

    def estimate(self) -> None:
        """Free, local page finding, then the token count (no generation, nothing billed)."""
        from ..fixtures.manual_reader.client import ManualReaderError
        from ..fixtures.manual_reader.extract import find_dmx_pages
        from ..fixtures.manual_reader.worker import cached_answer
        manuals = self._manuals_dir
        if manuals is None:
            from .. import userdata
            manuals = userdata.paths().manuals
        try:
            pages = find_dmx_pages(self.pdf_path, manuals)
        except Exception as exc:  # noqa: BLE001 - an unreadable PDF
            self.l_cost.setText(f"Could not read this PDF: {exc}")
            return
        if cached_answer(manuals, pages.sha256) is not None:
            self.l_cost.setText("This manual was read before: no charge, the saved answer is reused.")
            return
        if pages.trimmed_pdf is None:
            self.l_cost.setText("No page looks like a DMX chart. Enter the channels by hand instead.")
            return
        scanned = f" ({len(pages.scanned)} scanned page(s) included)" if set(pages.scanned) & set(pages.best) else ""
        try:
            self.reader = self.reader or self._reader_factory()
            est = self.reader.estimate(Path(pages.trimmed_pdf).read_bytes())
        except ManualReaderError as exc:
            self.l_cost.setText(f"Pages {pages.best}{scanned}. Cost estimate unavailable: {exc}")
            return
        self.l_cost.setText(f"Pages {pages.best}{scanned}. {est.text()}")

    def read(self) -> None:
        if self.availability() or self.pdf_path is None or not self.cb_consent.isChecked():
            self.refresh()
            return
        from ..fixtures.manual_reader.worker import ManualReaderWorker, read_manual
        self.reader = self.reader or self._reader_factory()
        if not self.threaded:
            from ..fixtures.manual_reader.client import ManualReaderError
            try:
                self._done(read_manual(self.pdf_path, self.reader, self._manuals_dir,
                                       progress=self.l_status.setText))
            except (ManualReaderError, ValueError, OSError) as exc:
                self._failed(str(exc))
            return
        self.worker = ManualReaderWorker(self.pdf_path, self.reader, self._manuals_dir, parent=self)
        self.worker.progress.connect(self.l_status.setText)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(lambda: self.l_status.setText("Cancelled."))
        self.worker.finished.connect(self.refresh)
        self.worker.start()
        self.refresh()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.l_status.setText("Cancelling…")

    def _failed(self, msg: str) -> None:
        self.l_status.setText(msg)
        self.refresh()

    def _done(self, result) -> None:
        from ..fixtures.manual_reader.review import doubts, draft, save_draft
        try:
            d = draft(result)
            save_draft(d, self._save)
        except (ValueError, OSError) as exc:
            self._failed(str(exc))
            return
        self.doubts = doubts(d)
        cached = " (from the saved answer, no charge)" if result.from_cache else ""
        self.l_status.setText(f"Drafted {d.manufacturer} {d.model}{cached}. "
                              f"{len(self.doubts)} value(s) to double-check against the manual.")
        self.drafted.emit(d)
        self.refresh()
