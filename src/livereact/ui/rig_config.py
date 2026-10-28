"""Rig Config tab — edit fixture type/address/role/side without hand-editing JSON.

Lists the currently loaded rig's fixtures in an editable table (type dropdown,
DMX address spinbox, role/side dropdowns), validates on Save using the same
overlap/overflow rules the loader itself enforces, and writes a rig JSON file
compatible with ``--rig <path>``.

Patch view (2026-10-28, docs/APP_ROADMAP.md item 21): the type list also offers
the user's fixture definitions (fixtures/library.py) with a mode picker, a new
row takes the next free DMX block, overlapping rows are marked inline (the
status line carries the loader's own RigError), and an "Address set" column
is the "did I dial this address into the fixture" checklist (saved on the slot
as ``address_set``).

Deliberately does NOT apply changes to the live show — swapping ``App.rig`` out
from under the engine threads mid-tick is a real race the show pipeline isn't
built to survive. Save, then restart with the new file (same pattern as
plugging in hardware tonight: the app picks up devices at startup, not live).
"""

from __future__ import annotations

import json
import logging
import os

from PySide6 import QtCore, QtWidgets

from ..fixtures.definition import FixtureDefinition
from ..fixtures.profiles import FIXTURE_TYPES
from ..fixtures.rig import RigError, Rig, _rig_from_dict, rig_to_dict

log = logging.getLogger(__name__)

_COLUMNS = ["Slot ID", "Label", "Fixture Type", "Mode", "DMX Address", "Role", "Side", "Address set"]
C_SLOT, C_LABEL, C_TYPE, C_MODE, C_ADDR, C_ROLE, C_SIDE, C_SET = range(len(_COLUMNS))
_NATIVE, _DEF = "native:", "def:"
_OVERLAP_STYLE = "background:#7a2020;"


def _user_definitions() -> list[FixtureDefinition]:
    """The user's library definitions (builtin models are already the native types)."""
    try:
        from ..fixtures.library import list_definitions
        return [e.definition for e in list_definitions() if e.origin != "builtin"]
    except Exception:  # noqa: BLE001 - a broken user folder must not break the tab
        log.exception("could not list fixture definitions")
        return []


class RigConfigTab(QtWidgets.QWidget):
    def __init__(self, rig: Rig, rig_path: str | None) -> None:
        super().__init__()
        self.rig = rig
        self.rig_path = rig_path
        # definitions the type combo offers: the rig's own (embedded) first, then the user's
        self.definitions: dict[str, FixtureDefinition] = {}
        for fx in rig.fixtures:
            d = getattr(fx, "definition", None)
            if d is not None:
                self.definitions.setdefault(d.id, d)
        for d in _user_definitions():
            self.definitions.setdefault(d.id, d)

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
            self._add_row(fx.slot_id, fx.label, fx.type_name, fx.address, fx.role, fx.side,
                          bool(fx.extra.get("address_set", False)))

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
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.check_overlaps()

    def reload_definitions(self) -> None:
        """Pick up definitions added to the user library since the tab was built."""
        for d in _user_definitions():
            if d.id not in self.definitions:
                self.definitions[d.id] = d
                for r in range(self.table.rowCount()):
                    tc = self.table.cellWidget(r, C_TYPE)
                    if isinstance(tc, QtWidgets.QComboBox):
                        tc.addItem(f"{d.manufacturer} {d.model} ({d.id})", _DEF + d.id)

    # ------------------------------------------------------------------ rows
    def _type_key(self, ftype: str) -> str:
        """A row's type combo key for a rig ``type_name`` (native type or ``id@mode``)."""
        if ftype in FIXTURE_TYPES:
            return _NATIVE + ftype
        fid = ftype.partition("@")[0]
        return _DEF + fid if fid in self.definitions else ""

    def _add_row(self, slot_id: str, label: str, ftype: str, address: int, role: str, side: str,
                 address_set: bool = False) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, C_SLOT, QtWidgets.QTableWidgetItem(slot_id))
        self.table.setItem(row, C_LABEL, QtWidgets.QTableWidgetItem(label))

        type_combo = QtWidgets.QComboBox()
        for t in sorted(FIXTURE_TYPES):
            type_combo.addItem(t, _NATIVE + t)
        for d in sorted(self.definitions.values(), key=lambda d: (d.manufacturer.lower(), d.model.lower())):
            type_combo.addItem(f"{d.manufacturer} {d.model} ({d.id})", _DEF + d.id)
        i = type_combo.findData(self._type_key(ftype))
        if i >= 0:
            type_combo.setCurrentIndex(i)
        self.table.setCellWidget(row, C_TYPE, type_combo)

        mode_combo = QtWidgets.QComboBox()
        self.table.setCellWidget(row, C_MODE, mode_combo)
        self._fill_modes(mode_combo, type_combo.currentData(), ftype.partition("@")[2] or None)

        addr_spin = QtWidgets.QSpinBox()
        addr_spin.setRange(1, 512)
        addr_spin.setValue(address)
        self.table.setCellWidget(row, C_ADDR, addr_spin)

        role_combo = QtWidgets.QComboBox()
        role_combo.addItems(["spot", "wash", "laser"])
        role_combo.setCurrentText(role)
        self.table.setCellWidget(row, C_ROLE, role_combo)

        side_combo = QtWidgets.QComboBox()
        side_combo.addItems(["left", "right"])
        side_combo.setCurrentText(side)
        self.table.setCellWidget(row, C_SIDE, side_combo)

        set_item = QtWidgets.QTableWidgetItem("")
        set_item.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled
                          | QtCore.Qt.ItemFlag.ItemIsSelectable)
        set_item.setCheckState(QtCore.Qt.CheckState.Checked if address_set else QtCore.Qt.CheckState.Unchecked)
        set_item.setToolTip("Tick once this DMX address is dialled into the fixture itself")
        self.table.setItem(row, C_SET, set_item)

        type_combo.currentIndexChanged.connect(lambda _i, tc=type_combo, mc=mode_combo: self._type_changed(tc, mc))
        mode_combo.currentIndexChanged.connect(lambda _i: self.check_overlaps())
        addr_spin.valueChanged.connect(lambda _v: self.check_overlaps())

    def _type_changed(self, type_combo: QtWidgets.QComboBox, mode_combo: QtWidgets.QComboBox) -> None:
        self._fill_modes(mode_combo, type_combo.currentData())
        self._update_reference()
        self.check_overlaps()

    def _fill_modes(self, combo: QtWidgets.QComboBox, key: str | None, select: str | None = None) -> None:
        combo.blockSignals(True)
        combo.clear()
        d = self.definitions.get(key[len(_DEF):]) if key and key.startswith(_DEF) else None
        if d is None:
            combo.addItem("—", None)
            combo.setEnabled(False)
        else:
            for m in d.modes:
                combo.addItem(f"{m.name} ({len(m.channels)} ch)", m.name)
            combo.setEnabled(len(d.modes) > 1)
            i = combo.findData(select) if select else -1
            combo.setCurrentIndex(max(i, 0))
        combo.blockSignals(False)

    def _row_type(self, row: int) -> tuple[str, str]:
        """("native", type_name) or ("def", "id@mode") for a row."""
        tc = self.table.cellWidget(row, C_TYPE)
        key = tc.currentData() if isinstance(tc, QtWidgets.QComboBox) else None
        if not key:
            return "native", ""
        if key.startswith(_NATIVE):
            return "native", key[len(_NATIVE):]
        mc = self.table.cellWidget(row, C_MODE)
        mode = mc.currentData() if isinstance(mc, QtWidgets.QComboBox) else None
        fid = key[len(_DEF):]
        return "def", f"{fid}@{mode}" if mode else fid

    def _row_channels(self, row: int) -> int:
        kind, name = self._row_type(row)
        if kind == "native":
            cls = FIXTURE_TYPES.get(name)
            return cls.channel_count if cls else 0
        fid, _, mode = name.partition("@")
        d = self.definitions.get(fid)
        try:
            return len(d.mode(mode or None).channels) if d else 0
        except KeyError:
            return 0

    def _row_address(self, row: int) -> int:
        spin = self.table.cellWidget(row, C_ADDR)
        return spin.value() if isinstance(spin, QtWidgets.QSpinBox) else 1

    def next_free_address(self, channels: int) -> int:
        """The lowest start address where ``channels`` fit without touching any row."""
        used = sorted((self._row_address(r), self._row_address(r) + self._row_channels(r) - 1)
                      for r in range(self.table.rowCount()))
        start = 1
        for lo, hi in used:
            if start + channels - 1 < lo:
                break
            start = max(start, hi + 1)
        return min(start, max(1, 513 - channels))

    def _add_blank_row(self) -> None:
        first_type = sorted(FIXTURE_TYPES)[0] if FIXTURE_TYPES else ""
        cls = FIXTURE_TYPES.get(first_type)
        address = self.next_free_address(cls.channel_count if cls else 1)
        self._add_row(f"slot_{self.table.rowCount() + 1}", "New Fixture", first_type, address, "wash", "left")
        self.check_overlaps()

    def check_overlaps(self) -> list[int]:
        """Mark rows whose DMX blocks collide or run past channel 512; returns those rows."""
        spans = [(self._row_address(r), self._row_address(r) + self._row_channels(r) - 1)
                 for r in range(self.table.rowCount())]
        bad = set()
        for i, (lo, hi) in enumerate(spans):
            if hi > 512:
                bad.add(i)
            for j in range(i):
                if lo <= spans[j][1] and spans[j][0] <= hi:
                    bad.update((i, j))
        for r in range(self.table.rowCount()):
            spin = self.table.cellWidget(r, C_ADDR)
            if isinstance(spin, QtWidgets.QSpinBox):
                lo, hi = spans[r]
                spin.setStyleSheet(_OVERLAP_STYLE if r in bad else "")
                spin.setToolTip(f"channels {lo}-{hi}" + (" — overlaps another fixture" if r in bad else ""))
        if bad:
            try:
                _rig_from_dict(self._collect())
                msg = "Address overlap"
            except Exception as exc:  # noqa: BLE001 - the loader's own message
                msg = str(exc)
            self.status.setText(f"⚠ {msg}")
            self.status.setStyleSheet("color:#c33;")
        elif self.status.text().startswith("⚠"):
            self.status.setText("")
        return sorted(bad)

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
        kind, name = self._row_type(row)
        if kind == "def":
            fid, _, mode = name.partition("@")
            d = self.definitions.get(fid)
            try:
                chans = d.mode(mode or None).channels if d else ()
            except KeyError:
                chans = ()
            lines = [f"{d.manufacturer} {d.model} — {mode}"] if d else []
            lines += [f"CH{c.offset + 1}  {c.function}" + (f"  (default {c.default})" if c.default else "")
                      for c in sorted(chans, key=lambda c: c.offset)]
            self.channel_ref.setPlainText("\n".join(lines))
            return
        cls = FIXTURE_TYPES.get(name)
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
        used_defs: set[str] = set()
        for row in range(self.table.rowCount()):
            slot_item = self.table.item(row, C_SLOT)
            label_item = self.table.item(row, C_LABEL)
            role_combo = self.table.cellWidget(row, C_ROLE)
            side_combo = self.table.cellWidget(row, C_SIDE)
            set_item = self.table.item(row, C_SET)
            kind, name = self._row_type(row)
            type_key = "fixture_type" if kind == "native" else "fixture"
            slot_id = (slot_item.text().strip() if slot_item else "") or f"slot_{row + 1}"
            edited = {
                "slot_id": slot_id,
                "label": (label_item.text().strip() if label_item else "") or "Fixture",
                type_key: name,
                "dmx_address": self._row_address(row),
                "role": role_combo.currentText() if role_combo else "wash",
                "side": side_combo.currentText() if side_combo else "left",
            }
            base = existing.get(slot_id, {})
            if base.get(type_key) != edited[type_key]:
                base = {}  # a different fixture class: its geometry/calibration no longer applies
            slot = {**base, **edited}
            if set_item is not None and set_item.checkState() == QtCore.Qt.CheckState.Checked:
                slot["address_set"] = True
            else:
                slot.pop("address_set", None)
            if kind == "def":
                used_defs.add(name.partition("@")[0])
            slots.append(slot)
        data = rig_to_dict(self.rig, self.rig.description or "Edited via the LiveReact console's Rig Config tab.")
        data["slots"] = slots
        from ..fixtures.library import BUILTIN_DIR
        defs = {fid: self.definitions[fid].to_dict() for fid in sorted(used_defs)
                if fid in self.definitions and not (BUILTIN_DIR / f"{fid}.json").is_file()}
        if defs:
            data["definitions"] = defs
        else:
            data.pop("definitions", None)
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
