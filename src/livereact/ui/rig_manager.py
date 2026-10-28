"""Rig manager: pick the rig to play instead of passing ``--rig <path>``.

New (an empty rig in the user's rigs folder), Open (a file dialog there), Recent (from
settings.py) and the Demo rig (the packaged ``default_rig.json``, read-only: "Save as" copies
it to the user folder first). Emits ``rig_chosen(path)``; the console decides what to do with
it (switching live needs ``App.switch_rig``, roadmap item 4; until then: restart to apply).
"""

from __future__ import annotations

import json
import shutil
from importlib import resources
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .. import userdata
from ..settings import Settings


def demo_rig_path() -> Path:
    """The packaged demo rig (read-only: it ships inside the app)."""
    return Path(str(resources.files("livereact.fixtures").joinpath("default_rig.json")))


def is_read_only(path: str | Path) -> bool:
    return Path(path).resolve() == demo_rig_path().resolve()


def _unique(folder: Path, stem: str) -> Path:
    p = folder / f"{stem}.json"
    n = 2
    while p.exists():
        p = folder / f"{stem} {n}.json"
        n += 1
    return p


def new_rig_file(name: str = "New rig") -> Path:
    """Write an empty rig (no fixtures yet) to the user's rigs folder."""
    p = _unique(userdata.paths().rigs, name)
    data = {"name": p.stem, "description": "", "laser_profile": "accent", "slots": []}
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def copy_demo_rig() -> Path:
    """'Save as' for the demo rig: an editable copy in the user's rigs folder."""
    p = _unique(userdata.paths().rigs, "Demo rig")
    shutil.copyfile(demo_rig_path(), p)
    return p


class RigManagerDialog(QtWidgets.QDialog):
    rig_chosen = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None, settings: Settings | None = None,
                 open_file=None) -> None:
        """``open_file(start_dir) -> path or ""``: the file picker (a QFileDialog by default;
        tests pass a stub)."""
        super().__init__(parent)
        self.setWindowTitle("Rigs")
        self.settings = settings if settings is not None else Settings.load()
        self._open_file = open_file or self._file_dialog
        v = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.b_new = QtWidgets.QPushButton("New rig")
        self.b_open = QtWidgets.QPushButton("Open…")
        self.b_demo = QtWidgets.QPushButton("Demo rig")
        self.b_demo_copy = QtWidgets.QPushButton("Save demo rig as…")
        for b in (self.b_new, self.b_open, self.b_demo, self.b_demo_copy):
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(QtWidgets.QLabel("Recent rigs"))
        self.recent = QtWidgets.QListWidget()
        v.addWidget(self.recent, 1)
        self.b_use_recent = QtWidgets.QPushButton("Use selected")
        v.addWidget(self.b_use_recent)
        self.l_msg = QtWidgets.QLabel("")
        self.l_msg.setWordWrap(True)
        v.addWidget(self.l_msg)
        self.reload_recent()

        self.b_new.clicked.connect(lambda: self.choose(new_rig_file()))
        self.b_open.clicked.connect(self.open_rig)
        self.b_demo.clicked.connect(lambda: self.choose(demo_rig_path()))
        self.b_demo_copy.clicked.connect(lambda: self.choose(copy_demo_rig()))
        self.b_use_recent.clicked.connect(self.use_selected)
        self.recent.itemDoubleClicked.connect(lambda _item: self.use_selected())

    def _file_dialog(self, start: str) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open rig", start, "Rig files (*.json)")
        return path

    def reload_recent(self) -> None:
        """Recent rigs that still exist, most recent first."""
        self.recent.clear()
        for r in self.settings.recent_rigs:
            if Path(r).is_file():
                item = QtWidgets.QListWidgetItem(Path(r).stem + ("  (demo, read-only)" if is_read_only(r) else ""))
                item.setData(QtCore.Qt.ItemDataRole.UserRole, r)
                item.setToolTip(r)
                self.recent.addItem(item)
        if self.recent.count():
            self.recent.setCurrentRow(0)
        self.b_use_recent.setEnabled(self.recent.count() > 0)

    def open_rig(self) -> None:
        path = self._open_file(str(userdata.paths().rigs))
        if path:
            self.choose(path)

    def use_selected(self) -> None:
        item = self.recent.currentItem()
        if item is not None:
            self.choose(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def choose(self, path: str | Path) -> None:
        """Validate, remember and announce ``path``."""
        from ..fixtures.rig import RigError, load_rig
        path = str(path)
        try:
            load_rig(path)
        except (OSError, ValueError, KeyError, RigError) as exc:
            self.l_msg.setText(f"Cannot use {Path(path).name}: {exc}")
            return
        self.settings.add_recent_rig(path)
        self.settings.save()
        self.reload_recent()
        self.l_msg.setText(f"{Path(path).stem}" + (" — demo rig, read-only: use “Save demo rig as…” to edit it"
                                                   if is_read_only(path) else ""))
        self.rig_chosen.emit(path)
