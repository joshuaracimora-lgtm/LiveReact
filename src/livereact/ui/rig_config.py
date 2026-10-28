"""Rig Config tab — edit fixture type/address/role/side without hand-editing JSON.

Lists the currently loaded rig's fixtures in an editable table (type dropdown,
DMX address spinbox, role/side dropdowns), validates on Save using the same
overlap/overflow rules the loader itself enforces, and writes a rig JSON file
compatible with ``--rig <path>``.

Deliberately does NOT apply changes to the live show — swapping ``App.rig`` out
from under the engine threads mid-tick is a real race the show pipeline isn't
built to survive. Save, then restart with the new file (same pattern as
plugging in hardware tonight: the app picks up devices at startup, not live).
"""

from __future__ import annotations

import json
import logging
import os

from PySide6 import QtWidgets

from ..fixtures.profiles import FIXTURE_TYPES
from ..fixtures.rig import RigError, Rig, _rig_from_dict, rig_to_dict

log = logging.getLogger(__name__)

_COLUMNS = ["Slot ID", "Label", "Fixture Type", "DMX Address", "Role", "Side"]


class RigConfigTab(QtWidgets.QWidget):
    def __init__(self, rig: Rig, rig_path: str | None) -> None:
        super().__init__()
        self.rig = rig
        self.rig_path = rig_path

        v = QtWidgets.QVBoxLayout(self)

        note = QtWidgets.QLabel(
            "Edit fixture type / DMX address / role / side below, then Save. "
            "Changes do NOT apply to the running show — restart LiveReact with "
            "--rig <saved file> to pick them up (same as plugging in new hardware)."
        )
        note.setWordWrap(True)
        v.addWidget(note)

        self.table = QtWidgets.QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_reference)
        v.addWidget(self.table)

        for fx in rig.fixtures:
            self._add_row(fx.slot_id, fx.label, fx.type_name, fx.address, fx.role, fx.side)

        btns = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add fixture")
        add_btn.clicked.connect(self._add_blank_row)
        remove_btn = QtWidgets.QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected)
        save_btn = QtWidgets.QPushButton("Save to file…")
        save_btn.clicked.connect(self._save)
        btns.addWidget(add_btn)
        btns.addWidget(remove_btn)
        btns.addStretch(1)
        btns.addWidget(save_btn)
        v.addLayout(btns)

        v.addWidget(QtWidgets.QLabel("Channel reference for the selected fixture type:"))
        self.channel_ref = QtWidgets.QTextEdit()
        self.channel_ref.setReadOnly(True)
        self.channel_ref.setMaximumHeight(140)
        v.addWidget(self.channel_ref)

        self.status = QtWidgets.QLabel("")
        v.addWidget(self.status)
        self.sync_sides()

    def sync_sides(self) -> None:
        """A placed fixture's side comes from its x (Rig.refresh_geometry), and
        the Venue tab moves fixtures after this table was built: show that
        side, locked, instead of the one it had at launch (which Save would
        write back). Unplaced fixtures keep an editable, declared side. The
        console calls this whenever the tab is opened."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            combo = self.table.cellWidget(row, 5)
            fx = self.rig._by_slot.get(item.text().strip()) if item else None
            if fx is None or not isinstance(combo, QtWidgets.QComboBox):
                continue
            derived = fx.x_norm is not None and fx.x_norm != 0
            if derived or not combo.isEnabled():
                combo.setCurrentText(fx.side)
            combo.setEnabled(not derived)
            combo.setToolTip("Set by where the fixture is placed on the Venue tab." if derived else "")

    # ------------------------------------------------------------------ rows
    def _add_row(self, slot_id: str, label: str, ftype: str, address: int, role: str, side: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(slot_id))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(label))

        type_combo = QtWidgets.QComboBox()
        type_combo.addItems(sorted(FIXTURE_TYPES))
        if ftype in FIXTURE_TYPES:
            type_combo.setCurrentText(ftype)
        type_combo.currentTextChanged.connect(self._update_reference)
        self.table.setCellWidget(row, 2, type_combo)

        addr_spin = QtWidgets.QSpinBox()
        addr_spin.setRange(1, 512)
        addr_spin.setValue(address)
        self.table.setCellWidget(row, 3, addr_spin)

        role_combo = QtWidgets.QComboBox()
        role_combo.addItems(["spot", "wash", "laser"])
        role_combo.setCurrentText(role)
        self.table.setCellWidget(row, 4, role_combo)

        side_combo = QtWidgets.QComboBox()
        side_combo.addItems(["left", "right"])
        side_combo.setCurrentText(side)
        self.table.setCellWidget(row, 5, side_combo)

    def _add_blank_row(self) -> None:
        first_type = sorted(FIXTURE_TYPES)[0] if FIXTURE_TYPES else ""
        self._add_row(f"slot_{self.table.rowCount() + 1}", "New Fixture", first_type, 1, "wash", "left")

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self.status.setText("")

    # ------------------------------------------------------------------ reference panel
    def _update_reference(self) -> None:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        if not rows:
            self.channel_ref.setPlainText("")
            return
        row = next(iter(rows))
        combo = self.table.cellWidget(row, 2)
        ftype = combo.currentText() if isinstance(combo, QtWidgets.QComboBox) else ""
        cls = FIXTURE_TYPES.get(ftype)
        doc = (cls.__doc__ or "(no channel reference documented)").strip() if cls else ""
        self.channel_ref.setPlainText(doc)

    # ------------------------------------------------------------------ save
    def _collect(self) -> dict:
        """The table's rows as a rig dict. Rows that still match a loaded
        slot start from the shared serializer's full slot (positions, home,
        mount height, calibration, unknown keys all kept) and only the six
        editable columns are overlaid; new rows get just those six."""
        existing = {s["slot_id"]: s for s in rig_to_dict(self.rig)["slots"]}
        slots = []
        for row in range(self.table.rowCount()):
            slot_item = self.table.item(row, 0)
            label_item = self.table.item(row, 1)
            type_combo = self.table.cellWidget(row, 2)
            addr_spin = self.table.cellWidget(row, 3)
            role_combo = self.table.cellWidget(row, 4)
            side_combo = self.table.cellWidget(row, 5)
            slot_id = (slot_item.text().strip() if slot_item else "") or f"slot_{row + 1}"
            edited = {
                "slot_id": slot_id,
                "label": (label_item.text().strip() if label_item else "") or "Fixture",
                "fixture_type": type_combo.currentText() if type_combo else "",
                "dmx_address": addr_spin.value() if addr_spin else 1,
                "role": role_combo.currentText() if role_combo else "wash",
                "side": side_combo.currentText() if side_combo else "left",
            }
            base = existing.get(slot_id, {})
            if base.get("fixture_type") != edited["fixture_type"]:
                base = {}  # a different fixture class: its geometry/calibration no longer applies
            slots.append({**base, **edited})
        data = rig_to_dict(self.rig, self.rig.description or "Edited via the LiveReact console's Rig Config tab.")
        data["slots"] = slots
        return data

    def _save(self) -> None:
        data = self._collect()
        try:
            _rig_from_dict(data)  # same overlap/overflow/unknown-type validation the loader uses
        except RigError as exc:
            self.status.setText(f"Not saved — {exc}")
            self.status.setStyleSheet("color:#c33;")
            return
        except Exception as exc:  # noqa: BLE001 - surface any bad row data clearly
            self.status.setText(f"Not saved — {exc}")
            self.status.setStyleSheet("color:#c33;")
            return

        default_path = self.rig_path or os.path.join("rigs", "custom_rig.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save rig", default_path, "JSON (*.json)")
        if not path:
            return
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        log.info("Saved rig config to %s", path)
        self.rig_path = path
        self.status.setText(f"Saved to {path} — restart with --rig \"{path}\" to apply.")
        self.status.setStyleSheet("color:#3c6;")
