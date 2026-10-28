"""Fixture library browser: every definition the patch view can use (roadmap item 23).

Built-in models, the user's own and imported ones (Open Fixture Library, the manual reader),
with whether each is confirmed. "Add fixture…" opens the wizard; "Test / confirm…" opens the
channel-test screen for the selected one.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..fixtures.library import list_definitions
from .channel_test import ChannelTestPanel
from .fixture_wizard import FixtureWizard

_COLUMNS = ["Manufacturer", "Model", "Category", "Modes", "Source", "Confirmed"]


def origin_label(origin: str, source_kind: str) -> str:
    if origin == "builtin":
        return "built-in"
    return {"ofl": "imported (Open Fixture Library)", "manual_ai": "imported (manual reader)"}.get(source_kind, "yours")


class FixtureLibraryBrowser(QtWidgets.QWidget):
    library_changed = QtCore.Signal()
    def __init__(self, parent: QtWidgets.QWidget | None = None, save=None, laser_armed=None) -> None:
        """``laser_armed()``: the console's arm switch, passed on to every channel-test screen."""
        super().__init__(parent)
        self._save = save
        self._laser_armed = laser_armed
        self.wizard: FixtureWizard | None = None
        self.test_panel: ChannelTestPanel | None = None
        v = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        self.b_add = QtWidgets.QPushButton("Add fixture…")
        self.b_test = QtWidgets.QPushButton("Test / confirm…")
        self.b_reload = QtWidgets.QPushButton("Refresh")
        row.addWidget(self.b_add)
        row.addWidget(self.b_test)
        row.addStretch(1)
        row.addWidget(self.b_reload)
        v.addLayout(row)
        self.b_add.clicked.connect(self.open_wizard)
        self.b_test.clicked.connect(self.test_selected)
        self.b_reload.clicked.connect(self.reload)
        self.entries = []
        self.reload()

    def reload(self) -> None:
        self.entries = list_definitions()
        self.library_changed.emit()
        self.table.setRowCount(0)
        for e in self.entries:
            d = e.definition
            r = self.table.rowCount()
            self.table.insertRow(r)
            cells = [d.manufacturer, d.model, d.category, ", ".join(m.name for m in d.modes),
                     origin_label(e.origin, d.source.kind), "yes" if d.source.confirmed else "no"]
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, d.id)
                self.table.setItem(r, c, item)
        self.b_test.setEnabled(bool(self.entries))

    def open_wizard(self) -> FixtureWizard:
        self.wizard = FixtureWizard(self, save=self._save, laser_armed=self._laser_armed)
        self.wizard.added.connect(lambda _d: self.reload())
        self.wizard.show()
        return self.wizard

    def test_selected(self) -> ChannelTestPanel | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return None
        e = self.entries[min(rows)]
        self.test_panel = ChannelTestPanel(e.definition, save=self._save, laser_armed=self._laser_armed)
        self.test_panel.confirmed.connect(lambda _d: self.reload())
        self.test_panel.setWindowTitle(f"Test {e.definition.manufacturer} {e.definition.model}")
        self.test_panel.show()
        return self.test_panel
