"""Fixture library browser: every definition the patch view can use (roadmap item 23).

Built-in models, the user's own and imported ones (Open Fixture Library, the manual reader),
with whether each is confirmed. "Add fixture…" opens the wizard; "Test / confirm…" opens the
channel-test screen for the selected one. "Delete…" (roadmap item 45) removes a user or
imported definition behind a confirm box that names the user rigs still using it; built-ins
cannot be deleted.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..fixtures.library import delete_user_definition, list_definitions, rigs_using
from .channel_test import ChannelTestPanel
from .fixture_wizard import FixtureWizard

_COLUMNS = ["Manufacturer", "Model", "Category", "Modes", "Source", "Confirmed"]


def origin_label(origin: str, source_kind: str) -> str:
    if origin == "builtin":
        return "built-in"
    return {"ofl": "imported (Open Fixture Library)", "manual_ai": "imported (manual reader)"}.get(source_kind, "yours")


class FixtureLibraryBrowser(QtWidgets.QWidget):
    library_changed = QtCore.Signal()
    def __init__(self, parent: QtWidgets.QWidget | None = None, save=None, laser_armed=None,
                 confirm=None) -> None:
        """``laser_armed()``: the console's arm switch, passed on to every channel-test screen.
        ``confirm(title, text) -> bool``: the delete confirm box (default: a QMessageBox)."""
        super().__init__(parent)
        self._save = save
        self._laser_armed = laser_armed
        self._confirm = confirm if confirm is not None else self._ask
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
        self.b_delete = QtWidgets.QPushButton("Delete…")
        self.b_reload = QtWidgets.QPushButton("Refresh")
        row.addWidget(self.b_add)
        row.addWidget(self.b_test)
        row.addWidget(self.b_delete)
        row.addStretch(1)
        row.addWidget(self.b_reload)
        v.addLayout(row)
        self.b_add.clicked.connect(self.open_wizard)
        self.b_test.clicked.connect(self.test_selected)
        self.b_delete.clicked.connect(self.delete_selected)
        self.b_reload.clicked.connect(self.reload)
        self.table.itemSelectionChanged.connect(self._update_buttons)
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
        self._update_buttons()

    def _selected_entry(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.entries[min(rows)] if rows else None

    def _update_buttons(self) -> None:
        e = self._selected_entry()
        self.b_delete.setEnabled(e is not None and e.origin != "builtin")
        self.b_delete.setToolTip("Built-in models cannot be deleted" if e is not None and e.origin == "builtin" else "")

    def _ask(self, title: str, text: str) -> bool:
        box = QtWidgets.QMessageBox
        return box.question(self, title, text, box.StandardButton.Yes | box.StandardButton.No,
                            box.StandardButton.No) == box.StandardButton.Yes

    def open_wizard(self) -> FixtureWizard:
        self.wizard = FixtureWizard(self, save=self._save, laser_armed=self._laser_armed)
        self.wizard.added.connect(lambda _d: self.reload())
        self.wizard.show()
        return self.wizard

    def delete_selected(self) -> bool:
        """Delete the selected user/imported definition after a confirm box that names
        the user rigs still using it. Built-ins are refused. True when deleted."""
        e = self._selected_entry()
        if e is None or e.origin == "builtin":
            return False
        d = e.definition
        text = f"Delete {d.manufacturer} {d.model} ({d.id}) from your library?"
        used = rigs_using(d.id)
        if used:
            text += ("\n\nStill used by: " + ", ".join(used) + ".\n"
                     "A rig saved with its own copy of the definition keeps working; "
                     "any other rig using it will no longer load.")
        if not self._confirm("Delete fixture", text):
            return False
        delete_user_definition(d.id)
        self.reload()
        return True

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
