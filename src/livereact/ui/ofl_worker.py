"""Open Fixture Library search page and its worker thread (roadmap item 25).

Network calls (the register, one fixture) run in a ``QThread`` so the UI never blocks; the
service (fixtures/ofl.py ``OflService``) has its own timeouts. Offline, the page says "Open
Fixture Library unavailable" and emits ``unavailable`` so the wizard can offer the other routes.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..fixtures.ofl import OflEntry, OflUnavailable, search

UNAVAILABLE = "Open Fixture Library unavailable"


class OflWorker(QtCore.QThread):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, fn, *args, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.fn, self.args = fn, args

    def run(self) -> None:
        try:
            result = self.fn(*self.args)
        except (OflUnavailable, ValueError, OSError) as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(result)


class OflSearchPage(QtWidgets.QWidget):
    imported = QtCore.Signal(object)     # FixtureDefinition (unconfirmed, source "ofl")
    unavailable = QtCore.Signal(str)

    def __init__(self, service_factory, threaded: bool = True,
                 parent: QtWidgets.QWidget | None = None) -> None:
        """``service_factory() -> OflService``, called on first use (it makes the cache folder);
        ``threaded=False`` runs calls inline (tests)."""
        super().__init__(parent)
        self._factory = service_factory
        self.service = None
        self.threaded = threaded
        self.entries: list[OflEntry] = []
        self._workers: list[OflWorker] = []
        v = QtWidgets.QVBoxLayout(self)
        self.e_query = QtWidgets.QLineEdit()
        self.e_query.setPlaceholderText("Manufacturer or model, e.g. “chauvet slimpar”")
        v.addWidget(self.e_query)
        self.results = QtWidgets.QListWidget()
        v.addWidget(self.results, 1)
        row = QtWidgets.QHBoxLayout()
        self.b_refresh = QtWidgets.QPushButton("Update list")
        self.b_import = QtWidgets.QPushButton("Use this fixture")
        row.addWidget(self.b_refresh)
        row.addStretch(1)
        row.addWidget(self.b_import)
        v.addLayout(row)
        self.l_status = QtWidgets.QLabel("")
        self.l_status.setWordWrap(True)
        v.addWidget(self.l_status)
        self.l_credit = QtWidgets.QLabel("Fixture data: Open Fixture Library contributors (open-fixture-library.org)")
        self.l_credit.setWordWrap(True)
        v.addWidget(self.l_credit)
        self.e_query.textChanged.connect(self.update_results)
        self.b_refresh.clicked.connect(lambda: self.load(refresh=True))
        self.b_import.clicked.connect(self.import_selected)
        self.results.itemDoubleClicked.connect(lambda _i: self.import_selected())
        self.b_import.setEnabled(False)

    def _call(self, fn, args, on_done, on_fail) -> None:
        if not self.threaded:
            try:
                on_done(fn(*args))
            except (OflUnavailable, ValueError, OSError) as exc:
                on_fail(str(exc))
            return
        w = OflWorker(fn, *args, parent=self)
        w.done.connect(on_done)
        w.failed.connect(on_fail)
        w.finished.connect(lambda w=w: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def wait(self, ms: int = 5000) -> None:
        """Block until running workers finish (tests / closing)."""
        for w in list(self._workers):
            w.wait(ms)

    def load(self, refresh: bool = False) -> None:
        if self.service is None:
            self.service = self._factory()
        self.l_status.setText("Loading the Open Fixture Library list…")
        self._call(self.service.index, (refresh,), self._loaded, self._failed)

    def _loaded(self, entries: list[OflEntry]) -> None:
        self.entries = entries
        self.l_status.setText(f"{len(entries)} fixtures. Type to search.")
        self.update_results()

    def _failed(self, msg: str) -> None:
        self.l_status.setText(f"{UNAVAILABLE}. Upload the manual or enter the channels by hand instead. ({msg})")
        self.unavailable.emit(msg)

    def update_results(self) -> None:
        self.results.clear()
        for e in search(self.entries, self.e_query.text()):
            item = QtWidgets.QListWidgetItem(f"{e.manufacturer} — {e.name}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, e)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        self.b_import.setEnabled(self.results.count() > 0)

    def import_selected(self) -> None:
        item = self.results.currentItem()
        if item is None or self.service is None:
            return
        entry: OflEntry = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self.l_status.setText(f"Fetching {entry.manufacturer} {entry.name}…")
        self._call(self.service.import_fixture, (entry,), self._imported, self._failed)

    def _imported(self, definition) -> None:
        self.l_status.setText(f"Imported {definition.manufacturer} {definition.model}: test it next.")
        self.imported.emit(definition)
