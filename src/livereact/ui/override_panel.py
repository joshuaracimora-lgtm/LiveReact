"""Manual Override panel: lasers, the operator's cues, and presets in one widget.

A standalone widget bound to ``app.manual`` (roadmap item 18); item 19 puts it in the console
in place of the Show tab's cue / laser / override groups. Every control calls exactly what the
console calls today (``ManualParams`` fields, ``metronome.cue_build`` / ``arm_drop``,
``phrase_engine.skip``, a tap-tempo ``BeatMessage``), so the show cannot tell them apart.
Hotkeys stay with the console window.

Lasers: the arm button writes ``manual.laser_enable``, the same switch as the console's "Arm
lasers" box. An event type that forbids lasers disarms them and blocks arming.

Presets: a change calls ``app.apply_preset(effect)`` when the app has it (item 4, Josh's);
until then the choice is only stored here.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import presets
from ..core.messages import BeatMessage
from ..presets import PresetEffect, PresetEvent
from ..tap_tempo import TapTempo

STATUS_ARMED = "ARMED"
STATUS_DISARMED = "disarmed"
STATUS_BLOCKED = "blocked by event"


def _button(text: str, role: str | None = None, checkable: bool = False) -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    if role:
        b.setObjectName(role)  # styled by ui/theme.py (#cue / #danger / #look)
    b.setCheckable(checkable)
    b.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
    return b


class OverridePanel(QtWidgets.QWidget):
    preset_changed = QtCore.Signal(object)   # PresetEffect

    def __init__(self, app, parent: QtWidgets.QWidget | None = None,
                 on_tap=None, on_burst=None) -> None:
        """``on_tap`` / ``on_burst``: the host window's own handlers, so its hotkeys and these
        buttons share one tap-tempo state (default: the panel's own)."""
        super().__init__(parent)
        self.app = app
        self._on_tap = on_tap
        self._on_burst = on_burst
        self.tap = TapTempo()
        self._tap_beat = 0
        self.preset_event = PresetEvent()
        self.effect: PresetEffect = presets.resolve(self.preset_event)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(self._build_lasers())
        v.addWidget(self._build_overrides())
        v.addWidget(self._build_presets())
        v.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------ lasers
    def _build_lasers(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Lasers")
        h = QtWidgets.QHBoxLayout(box)
        self.b_laser_arm = _button("ARM LASERS", "danger", checkable=True)
        self.b_laser_arm.setChecked(self.app.manual.laser_enable)
        self.l_laser_status = QtWidgets.QLabel("")
        self.l_laser_status.setObjectName("status")
        h.addWidget(self.b_laser_arm, 1)
        h.addWidget(self.l_laser_status)
        self.b_laser_arm.toggled.connect(self._arm_toggled)
        return box

    def lasers_allowed(self) -> bool:
        return self.effect.lasers_allowed

    def _arm_toggled(self, on: bool) -> None:
        # the same switch the console's "Arm lasers (L)" box writes
        self.app.manual.laser_enable = bool(on and self.lasers_allowed())
        self.refresh()

    def laser_status(self) -> str:
        if not self.lasers_allowed():
            return STATUS_BLOCKED
        return STATUS_ARMED if self.app.manual.laser_enable else STATUS_DISARMED

    # --------------------------------------------------------------- overrides
    def _build_overrides(self) -> QtWidgets.QWidget:
        m = self.app.manual
        outer = QtWidgets.QWidget()
        ov = QtWidgets.QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        self.b_overrides = QtWidgets.QToolButton()
        self.b_overrides.setText("Manual Override")
        self.b_overrides.setCheckable(True)
        self.b_overrides.setChecked(False)
        self.b_overrides.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.b_overrides.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        ov.addWidget(self.b_overrides)

        self.override_body = QtWidgets.QGroupBox()
        g = QtWidgets.QGridLayout(self.override_body)
        g.setSpacing(8)
        self.b_cue = _button("CUE BUILD\n(C)", "cue")
        self.b_arm = _button("ARM DROP\n(D)", "cue")
        self.b_burst = _button("STROBE BURST\n(Space)", "cue")
        self.b_skip = _button("SKIP SCENE\n(→)", "cue")
        self.b_tap = _button("Tap tempo (T)")
        self.b_laser_cue = _button("CUE LASER\n(K)", "cue", checkable=True)
        self.b_laser_cue.setChecked(m.laser_cue)
        self.b_laser_hit = _button("LASER HIT\n(J)", "cue")
        g.addWidget(self.b_cue, 0, 0); g.addWidget(self.b_arm, 0, 1)
        g.addWidget(self.b_burst, 1, 0); g.addWidget(self.b_skip, 1, 1)
        g.addWidget(self.b_laser_cue, 2, 0); g.addWidget(self.b_tap, 2, 1)
        g.addWidget(self.b_laser_hit, 3, 0)
        row = 4
        self.laser_mode_buttons: dict[str, QtWidgets.QRadioButton] = {}
        self.laser_look_buttons: dict[str, QtWidgets.QPushButton] = {}
        if getattr(self.app.rig, "laser_profile", "") == "show":
            row = self._build_laser_show(g, row)
        ov.addWidget(self.override_body)
        self.override_body.setVisible(False)       # collapsed by default

        self.b_overrides.toggled.connect(self._expand)
        self.b_cue.clicked.connect(self.app.metronome.cue_build)
        self.b_arm.clicked.connect(self.app.metronome.arm_drop)
        self.b_burst.clicked.connect(lambda: (self._on_burst or self.strobe_burst)())
        self.b_skip.clicked.connect(self.app.phrase_engine.skip)
        self.b_tap.clicked.connect(lambda: (self._on_tap or self.do_tap)())
        self.b_laser_cue.toggled.connect(lambda on: setattr(m, "laser_cue", on))
        self.b_laser_hit.clicked.connect(lambda: setattr(m, "laser_hit", m.laser_hit + 1))
        return outer

    def _build_laser_show(self, g: QtWidgets.QGridLayout, row: int) -> int:
        from ..engines.laser_show import MODE_FOLLOW, MODE_HOLD, MODE_ROTATE, look_names
        m = self.app.manual
        modes = QtWidgets.QHBoxLayout()
        for mode, text in ((MODE_FOLLOW, "Follow"), (MODE_ROTATE, "Rotate"), (MODE_HOLD, "Hold")):
            rb = QtWidgets.QRadioButton(text)
            self.laser_mode_buttons[mode] = rb
            modes.addWidget(rb)
            rb.toggled.connect(lambda on, mode=mode: on and setattr(m, "laser_mode", mode))
        self.laser_mode_buttons.get(m.laser_mode, self.laser_mode_buttons[MODE_FOLLOW]).setChecked(True)
        g.addLayout(modes, row, 0, 1, 2)
        row += 1
        self._look_group = QtWidgets.QButtonGroup(self)
        self._look_group.setExclusive(True)
        for i, name in enumerate(look_names()):
            b = _button(name, "look", checkable=True)
            self.laser_look_buttons[name] = b
            self._look_group.addButton(b)
            g.addWidget(b, row + i // 2, i % 2)
            b.clicked.connect(lambda _=False, name=name: self.hold_laser_look(name))
        if m.laser_scene in self.laser_look_buttons:
            self.laser_look_buttons[m.laser_scene].setChecked(True)
        return row + (len(self.laser_look_buttons) + 1) // 2

    def _expand(self, on: bool) -> None:
        self.override_body.setVisible(on)
        self.b_overrides.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)

    def hold_laser_look(self, name: str) -> None:
        """A look button: pick it and switch to the manual hold (as the Laser Show tab does)."""
        from ..engines.laser_show import MODE_HOLD
        self.app.manual.laser_scene = name
        self.laser_mode_buttons[MODE_HOLD].setChecked(True)

    def strobe_burst(self) -> None:
        self.app.manual.strobe_burst = True
        QtCore.QTimer.singleShot(400, lambda: setattr(self.app.manual, "strobe_burst", False))

    def do_tap(self) -> None:
        now = self.app.clock.monotonic()
        bpm = self.tap.tap(now)
        if bpm is not None:
            self._tap_beat = self._tap_beat % 4 + 1
            self.app.input_queue.put(BeatMessage(now, self._tap_beat, bpm, is_master=True, player=0))

    # ----------------------------------------------------------------- presets
    def _build_presets(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Presets")
        f = QtWidgets.QFormLayout(box)
        self.cmb_scheme = self._combo("schemes")
        self.cmb_genre = self._combo("genres")
        self.cmb_event_type = self._combo("event_types")
        f.addRow("Colour scheme", self.cmb_scheme)
        f.addRow("Genre", self.cmb_genre)
        f.addRow("Event type", self.cmb_event_type)
        self.e_event_name = QtWidgets.QLineEdit()
        self.e_event_name.setPlaceholderText("Event name")
        self.b_save_event = QtWidgets.QPushButton("Save event")
        save_row = QtWidgets.QHBoxLayout()
        save_row.addWidget(self.e_event_name, 1)
        save_row.addWidget(self.b_save_event)
        f.addRow(save_row)
        self.cmb_events = QtWidgets.QComboBox()
        self.b_load_event = QtWidgets.QPushButton("Load event")
        load_row = QtWidgets.QHBoxLayout()
        load_row.addWidget(self.cmb_events, 1)
        load_row.addWidget(self.b_load_event)
        f.addRow(load_row)
        self.l_preset_msg = QtWidgets.QLabel("")
        self.l_preset_msg.setWordWrap(True)
        f.addRow(self.l_preset_msg)
        self.reload_events()
        for c in (self.cmb_scheme, self.cmb_genre, self.cmb_event_type):
            c.currentIndexChanged.connect(self._combo_changed)
        self.b_save_event.clicked.connect(self.save_event)
        self.b_load_event.clicked.connect(self.load_selected_event)
        return box

    @staticmethod
    def _combo(kind: str) -> QtWidgets.QComboBox:
        c = QtWidgets.QComboBox()
        for key, label in presets.choices(kind):
            c.addItem(label, key)
        return c

    def _combo_changed(self) -> None:
        self.set_event(PresetEvent(name=self.e_event_name.text().strip(),
                                   scheme=self.cmb_scheme.currentData(),
                                   genre=self.cmb_genre.currentData(),
                                   event_type=self.cmb_event_type.currentData()))

    def set_event(self, event: PresetEvent) -> None:
        """Select ``event`` in the combos and apply it."""
        self.preset_event = event
        self.effect = presets.resolve(event)
        for combo, key in ((self.cmb_scheme, event.scheme), (self.cmb_genre, event.genre),
                           (self.cmb_event_type, event.event_type)):
            i = combo.findData(key)
            if i >= 0 and i != combo.currentIndex():
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)
        if not self.effect.lasers_allowed and self.app.manual.laser_enable:
            self.app.manual.laser_enable = False        # fail safe: the event forbids lasers
        apply = getattr(self.app, "apply_preset", None)
        if callable(apply):
            apply(self.effect)
            self.l_preset_msg.setText("")
        else:
            self.l_preset_msg.setText("Preset stored; it takes effect once the app supports presets.")
        self.preset_changed.emit(self.effect)
        self.refresh()

    def reload_events(self) -> None:
        self.cmb_events.clear()
        for name in presets.list_events():
            self.cmb_events.addItem(name)
        self.b_load_event.setEnabled(self.cmb_events.count() > 0)

    def save_event(self) -> None:
        name = self.e_event_name.text().strip()
        try:
            presets.save_event(PresetEvent(name, self.preset_event.scheme, self.preset_event.genre, self.preset_event.event_type))
        except ValueError as exc:
            self.l_preset_msg.setText(str(exc))
            return
        self.l_preset_msg.setText(f"Saved “{name}”.")
        self.reload_events()

    def load_selected_event(self) -> None:
        name = self.cmb_events.currentText()
        if not name:
            return
        try:
            ev = presets.load_event(name)
        except (OSError, ValueError) as exc:
            self.l_preset_msg.setText(f"Could not load “{name}”: {exc}")
            return
        self.e_event_name.setText(ev.name)
        self.set_event(ev)

    # ----------------------------------------------------------------- refresh
    def refresh(self) -> None:
        """Mirror state that other inputs (hotkeys, MIDI, the console) can change."""
        m = self.app.manual
        allowed = self.lasers_allowed()
        self.b_laser_arm.setEnabled(allowed or m.laser_enable)
        for b, on in ((self.b_laser_arm, m.laser_enable and allowed), (self.b_laser_cue, m.laser_cue)):
            if b.isChecked() != on:
                b.blockSignals(True)
                b.setChecked(on)
                b.blockSignals(False)
        self.b_laser_arm.setText("DISARM LASERS (L)" if m.laser_enable else "ARM LASERS (L)")
        self.l_laser_status.setText(self.laser_status())
        rb = self.laser_mode_buttons.get(m.laser_mode)
        if rb is not None and not rb.isChecked():
            rb.blockSignals(True)
            rb.setChecked(True)
            rb.blockSignals(False)
        look = self.laser_look_buttons.get(m.laser_scene)
        if look is not None and not look.isChecked():
            look.setChecked(True)
