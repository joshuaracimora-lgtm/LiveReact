"""Channel-test screen for one fixture definition: where a user confirms a definition works.

Every route that adds a fixture (Open Fixture Library, the manual reader, hand entry) ends here
(roadmap item 22). One slider per channel, labelled by function; a capability picker ("Red:
10-19") that sends the middle of that range; a pass/fail mark per channel; and **Confirm**,
which sets ``source.confirmed`` + ``confirmed_at`` and saves through fixtures/library.py.

Confirm needs every channel marked pass. For a laser it also needs the user's values for the
three intents (blank, static line, fan; captured from the sliders), because a GenericLaser
outputs nothing without them. Laser channels are sent as zeros unless BOTH "Enable laser output
on this screen" is ticked AND the console's arm switch is on (roadmap item 41: two keys for a
laser). ``laser_armed()`` reads ``manual.laser_enable``; without it the laser stays dark. The
switch is polled, so disarming in the console (button, L, MIDI, an event type) zeroes this screen.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime

from PySide6 import QtCore, QtWidgets

from ..core.frame_buffer import UNIVERSE_SIZE, DmxFrameBuffer
from ..fixtures.definition import LASER_INTENTS, FixtureDefinition, LaserIntents

UNTESTED, PASS, FAIL = "untested", "pass", "fail"
_MARKS = (UNTESTED, PASS, FAIL)
LOW_CONFIDENCE = 0.6
ARM_POLL_MS = 100
_INTENT_LABELS = {"blank": "Blank (laser off)", "line_static": "Static line", "fan": "Fan"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ChannelTestPanel(QtWidgets.QWidget):
    confirmed = QtCore.Signal(object)   # the saved FixtureDefinition

    def __init__(self, definition: FixtureDefinition, mode: str | None = None, address: int = 1,
                 frame_buffer: DmxFrameBuffer | None = None, save=None, now=_now,
                 laser_armed: Callable[[], bool] | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        """``save(definition) -> path``: where Confirm writes (default: the user library).
        ``laser_armed()``: the console's arm switch (``manual.laser_enable``); None = never armed."""
        super().__init__(parent)
        self.laser_armed: Callable[[], bool] = laser_armed or (lambda: False)
        self._armed = False
        self.definition = definition
        self.mode = definition.mode(mode)
        self.address = address
        self.frame_buffer = frame_buffer or DmxFrameBuffer()
        self._save = save
        self._now = now
        self.is_laser = definition.category == "laser"
        self.values = [0] * len(self.mode.channels)
        li = definition.laser_intents
        self.intents: dict[str, dict[int, int]] = {k: dict(getattr(li, k)) if li else {} for k in LASER_INTENTS}
        self.chop_channel = li.chop_channel if li else None
        self.sliders: list[QtWidgets.QSlider] = []
        self.pickers: list[QtWidgets.QComboBox] = []
        self.marks: list[QtWidgets.QComboBox] = []
        self.flagged: list[int] = []     # channel offsets with a low-confidence reading

        v = QtWidgets.QVBoxLayout(self)
        src = definition.source
        head = QtWidgets.QLabel(f"{definition.manufacturer} {definition.model} — {self.mode.name} — "
                                f"DMX {address}-{address + len(self.mode.channels) - 1} — "
                                + ("confirmed" if src.confirmed else "not confirmed yet"))
        head.setWordWrap(True)
        v.addWidget(head)

        self.cb_laser_out = QtWidgets.QCheckBox("Enable laser output on this screen")
        self.cb_laser_out.setVisible(self.is_laser)
        self.cb_laser_out.toggled.connect(lambda _on: self._on_box())
        v.addWidget(self.cb_laser_out)
        self.l_arm = QtWidgets.QLabel("")
        self.l_arm.setWordWrap(True)
        self.l_arm.setVisible(self.is_laser)
        v.addWidget(self.l_arm)
        self.arm_timer = QtCore.QTimer(self)
        self.arm_timer.setInterval(ARM_POLL_MS)
        self.arm_timer.timeout.connect(self.sync_arm)
        if self.is_laser:
            self._armed = self._read_arm()
            self.arm_timer.start()

        grid = QtWidgets.QGridLayout()
        for row, ch in enumerate(sorted(self.mode.channels, key=lambda c: c.offset)):
            name = QtWidgets.QLabel(f"CH{ch.offset + 1} · {ch.function.replace('_', ' ')}")
            doubt = [c for c in ch.capabilities if c.confidence is not None and c.confidence < LOW_CONFIDENCE]
            if doubt:
                # the manual reader was unsure: flag the row and say which manual page to check
                pages = sorted({c.source_page for c in doubt if c.source_page})
                name.setStyleSheet("color:#e0a030; font-weight:bold;")
                name.setToolTip("Check against the manual" + (f", page {', '.join(map(str, pages))}" if pages else "")
                                + f" (confidence {min(c.confidence for c in doubt):.0%})")
                self.flagged.append(ch.offset)
            grid.addWidget(name, row, 0)
            s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            s.setRange(0, 255)
            val = QtWidgets.QLabel("0")
            val.setMinimumWidth(28)
            s.valueChanged.connect(lambda x, i=ch.offset, lbl=val: self._on_change(i, x, lbl))
            picker = QtWidgets.QComboBox()
            picker.addItem("capability…", None)
            for cap in ch.capabilities:
                name = cap.label or cap.type.replace("_", " ")
                picker.addItem(f"{name}: {cap.range[0]}-{cap.range[1]}", (cap.range[0] + cap.range[1]) // 2)
                if cap.confidence is not None and cap.confidence < 0.6:
                    picker.setItemData(picker.count() - 1, f"low confidence ({cap.confidence:.0%})"
                                       + (f", manual page {cap.source_page}" if cap.source_page else ""),
                                       QtCore.Qt.ItemDataRole.ToolTipRole)
            picker.setEnabled(bool(ch.capabilities))
            picker.activated.connect(lambda idx, sl=s, p=picker: self._pick(sl, p, idx))
            mark = QtWidgets.QComboBox()
            mark.addItems(list(_MARKS))
            mark.currentIndexChanged.connect(lambda _i: self._update_confirm())
            grid.addWidget(s, row, 1)
            grid.addWidget(val, row, 2)
            grid.addWidget(picker, row, 3)
            grid.addWidget(mark, row, 4)
            self.sliders.append(s)
            self.pickers.append(picker)
            self.marks.append(mark)
        v.addLayout(grid)

        self.intent_box = QtWidgets.QGroupBox("Laser intents — set the sliders, then capture")
        il = QtWidgets.QGridLayout(self.intent_box)
        self.intent_buttons: dict[str, QtWidgets.QPushButton] = {}
        self.intent_labels: dict[str, QtWidgets.QLabel] = {}
        for r, k in enumerate(LASER_INTENTS):
            b = QtWidgets.QPushButton(f"Capture: {_INTENT_LABELS[k]}")
            b.clicked.connect(lambda _=False, k=k: self.capture_intent(k))
            lbl = QtWidgets.QLabel("")
            il.addWidget(b, r, 0)
            il.addWidget(lbl, r, 1)
            self.intent_buttons[k] = b
            self.intent_labels[k] = lbl
        self.intent_box.setVisible(self.is_laser)
        v.addWidget(self.intent_box)

        row = QtWidgets.QHBoxLayout()
        self.b_all_off = QtWidgets.QPushButton("All off")
        self.b_all_off.clicked.connect(self.all_off)
        self.b_confirm = QtWidgets.QPushButton("Confirm")
        self.b_confirm.clicked.connect(self.confirm)
        row.addWidget(self.b_all_off)
        row.addStretch(1)
        row.addWidget(self.b_confirm)
        v.addLayout(row)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self._refresh_intents()
        self._update_confirm()
        self._show_arm()
        self._publish()

    # --- output ---
    def _on_change(self, offset: int, value: int, label: QtWidgets.QLabel) -> None:
        self.values[offset] = value
        label.setText(str(value))
        self._publish()

    def _pick(self, slider: QtWidgets.QSlider, picker: QtWidgets.QComboBox, idx: int) -> None:
        value = picker.itemData(idx)
        if value is not None:
            slider.setValue(int(value))

    def _read_arm(self) -> bool:
        try:
            return bool(self.laser_armed())
        except Exception:   # fail safe: an unreadable switch is a disarmed one
            return False

    def _on_box(self) -> None:
        self._armed = self._read_arm()   # the switch as it is now, not as last polled
        self._show_arm()
        self._publish()

    def sync_arm(self) -> None:
        """Re-read the console's arm switch; republish when it changed."""
        armed = self._read_arm()
        if armed != self._armed:
            self._armed = armed
            self._show_arm()
            self._publish()

    def _show_arm(self) -> None:
        self.l_arm.setText("Console laser arm: ARMED" if self._armed else
                           "Console laser arm: DISARMED — arm lasers in the console (L) as well")

    def laser_live(self) -> bool:
        """Both keys turned: this screen's box and the console's arm switch."""
        return self.cb_laser_out.isChecked() and self._armed

    def output(self) -> bytes:
        """The universe this screen sends (laser channels zero unless both keys are on)."""
        uni = bytearray(UNIVERSE_SIZE)
        if not self.is_laser or self.laser_live():
            for i, v in enumerate(self.values):
                if self.address - 1 + i < UNIVERSE_SIZE:
                    uni[self.address - 1 + i] = v
        return bytes(uni)

    def _publish(self) -> None:
        self.frame_buffer.publish(self.output())

    def all_off(self) -> None:
        for s in self.sliders:
            s.setValue(0)

    # --- laser intents ---
    def capture_intent(self, intent: str) -> None:
        self.intents[intent] = {i: v for i, v in enumerate(self.values)}
        self._refresh_intents()
        self._update_confirm()

    def _refresh_intents(self) -> None:
        for k, lbl in self.intent_labels.items():
            vals = self.intents.get(k) or {}
            lbl.setText(" ".join(f"CH{o + 1}={v}" for o, v in sorted(vals.items()) if v) or
                        ("all 0" if vals else "not set"))

    # --- confirm ---
    def missing(self) -> list[str]:
        """What still stops Confirm, in words; empty when it may confirm."""
        out = []
        untested = [i + 1 for i, m in enumerate(self.marks) if m.currentText() != PASS]
        if untested:
            out.append("mark every channel pass (not yet: CH" + ", CH".join(map(str, untested)) + ")")
        if self.is_laser:
            unset = [_INTENT_LABELS[k] for k in LASER_INTENTS if not self.intents.get(k)]
            if unset:
                out.append("capture the laser's " + ", ".join(unset))
        return out

    def _update_confirm(self) -> None:
        miss = self.missing()
        self.b_confirm.setEnabled(not miss)
        self.b_confirm.setToolTip("; ".join(miss))

    def confirmed_definition(self) -> FixtureDefinition:
        d = self.definition
        source = dataclasses.replace(d.source, confirmed=True, confirmed_at=self._now())
        intents = d.laser_intents
        if self.is_laser:
            intents = LaserIntents(**{k: dict(self.intents[k]) for k in LASER_INTENTS},
                                   chop_channel=self.chop_channel)
        return dataclasses.replace(d, source=source, laser_intents=intents)

    def confirm(self) -> FixtureDefinition | None:
        miss = self.missing()
        if miss:
            self.status.setText("Cannot confirm yet: " + "; ".join(miss))
            return None
        d = self.confirmed_definition()
        save = self._save
        if save is None:
            from ..fixtures.library import save_user_definition as save
        try:
            path = save(d)
        except (OSError, ValueError) as exc:
            self.status.setText(f"Not saved — {exc}")
            return None
        self.definition = d
        self.status.setText(f"Confirmed and saved{f' to {path}' if path else ''}.")
        self.confirmed.emit(d)
        return d
