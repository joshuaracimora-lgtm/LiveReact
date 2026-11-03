"""Rig manager: pick the rig to play instead of passing ``--rig <path>``.

New (an empty rig in the user's rigs folder), Open (a file dialog there), Recent (from
settings.py) and the Demo rig (the packaged ``default_rig.json``, read-only: "Save as" copies
it to the user folder first). Emits ``rig_chosen(path)``; the console decides what to do with
it (switching live needs ``App.switch_rig``, roadmap item 4; until then: restart to apply).

Rename and delete (item 50) work only on files inside the user's rigs folder: the demo rig
ships read-only, and a rig opened from anywhere else (say a source checkout's ``rigs/``) is
not ours to move or remove.
"""

from __future__ import annotations

import json
import re
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


def in_user_rigs_folder(path: str | Path) -> bool:
    return Path(path).resolve().parent == userdata.paths(create=False).rigs.resolve()


def _user_rig(path: str | Path, verb: str) -> Path:
    p = Path(path)
    if not in_user_rigs_folder(p):
        raise ValueError(f"only rigs in your LiveReact folder can be {verb} here")
    return p


def rename_rig_file(path: str | Path, new_name: str) -> Path:
    """Rename a user rig: the file, its JSON ``name`` and nothing else."""
    p = _user_rig(path, "renamed")
    name = new_name.strip()
    if not name or re.search(r'[<>:"/\\|?*]', name):
        raise ValueError(f"{new_name!r} is not a usable rig name")
    target = p.with_name(f"{name}.json")
    if target.exists() and target != p:
        raise ValueError(f"a rig called {name!r} already exists")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["name"] = name
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if target != p:
        p.unlink()
    return target


def delete_rig_file(path: str | Path) -> None:
    """Delete a user rig file permanently."""
    _user_rig(path, "deleted").unlink()


class RigManagerDialog(QtWidgets.QDialog):
    rig_chosen = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None, settings: Settings | None = None,
                 open_file=None, ask_text=None, confirm=None) -> None:
        """``open_file(start_dir) -> path or ""``: the file picker; ``ask_text(current) ->
        name or ""`` and ``confirm(text) -> bool`` back Rename / Delete (Qt dialogs by
        default; tests pass stubs)."""
        super().__init__(parent)
        self.setWindowTitle("Rigs")
        self.settings = settings if settings is not None else Settings.load()
        self._open_file = open_file or self._file_dialog
        self._ask_text = ask_text or self._text_dialog
        self._confirm = confirm or self._confirm_dialog
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
        sel_row = QtWidgets.QHBoxLayout()
        self.b_use_recent = QtWidgets.QPushButton("Use selected")
        self.b_rename = QtWidgets.QPushButton("Rename…")
        self.b_delete = QtWidgets.QPushButton("Delete…")
        for b in (self.b_use_recent, self.b_rename, self.b_delete):
            sel_row.addWidget(b)
        v.addLayout(sel_row)
        self.l_msg = QtWidgets.QLabel("")
        self.l_msg.setWordWrap(True)
        v.addWidget(self.l_msg)
        self.reload_recent()

        self.b_new.clicked.connect(lambda: self.choose(new_rig_file()))
        self.b_open.clicked.connect(self.open_rig)
        self.b_demo.clicked.connect(lambda: self.choose(demo_rig_path()))
        self.b_demo_copy.clicked.connect(lambda: self.choose(copy_demo_rig()))
        self.b_use_recent.clicked.connect(self.use_selected)
        self.b_rename.clicked.connect(self.rename_selected)
        self.b_delete.clicked.connect(self.delete_selected)
        self.recent.itemDoubleClicked.connect(lambda _item: self.use_selected())
        self.recent.currentRowChanged.connect(lambda _row: self._update_buttons())

    def _file_dialog(self, start: str) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open rig", start, "Rig files (*.json)")
        return path

    def _text_dialog(self, current: str) -> str:
        text, ok = QtWidgets.QInputDialog.getText(self, "Rename rig", "New name:", text=current)
        return text if ok else ""

    def _confirm_dialog(self, text: str) -> bool:
        buttons = QtWidgets.QMessageBox.StandardButton
        return QtWidgets.QMessageBox.question(self, "Delete rig", text,
                                              buttons.Yes | buttons.No) == buttons.Yes

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
        self._update_buttons()

    def _selected_path(self) -> str | None:
        item = self.recent.currentItem()
        return None if item is None else item.data(QtCore.Qt.ItemDataRole.UserRole)

    def _update_buttons(self) -> None:
        path = self._selected_path()
        editable = path is not None and in_user_rigs_folder(path)
        self.b_rename.setEnabled(editable)
        self.b_delete.setEnabled(editable)

    def open_rig(self) -> None:
        path = self._open_file(str(userdata.paths().rigs))
        if path:
            self.choose(path)

    def use_selected(self) -> None:
        item = self.recent.currentItem()
        if item is not None:
            self.choose(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def rename_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        name = self._ask_text(Path(path).stem).strip()
        if not name:
            return
        try:
            new_path = rename_rig_file(path, name)
        except (OSError, ValueError) as exc:
            self.l_msg.setText(f"Cannot rename: {exc}")
            return
        # the renamed rig keeps its place in the recent list
        self.settings.recent_rigs = [str(new_path) if r == path else r for r in self.settings.recent_rigs]
        self.settings.save()
        self.reload_recent()
        self.l_msg.setText(f"Renamed to {name}")

    def delete_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        stem = Path(path).stem
        if not self._confirm(f"Delete “{stem}”? The rig file is removed permanently."):
            return
        try:
            delete_rig_file(path)
        except (OSError, ValueError) as exc:
            self.l_msg.setText(f"Cannot delete: {exc}")
            return
        self.settings.recent_rigs = [r for r in self.settings.recent_rigs if r != path]
        self.settings.save()
        self.reload_recent()
        self.l_msg.setText(f"Deleted {stem}")

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
