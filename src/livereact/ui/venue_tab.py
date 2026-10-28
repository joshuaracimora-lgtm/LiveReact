"""Venue tab: the editable floor plan + per-fixture geometry + head calibration.

Josh's 2026-09-18 request: "in a box I can put the layout in terms of length
by width of the venue I'm at and then place my lighting fixtures accordingly
and orientations to that so I can properly calibrate my moving heads."

Everything the operator types is in FEET; the rig objects and the JSON file
stay in meters (fixtures/geometry.py). Edits apply to the live ``Rig``
immediately (the Show tab's mini-map redraws on its next refresh) but reach
disk only through "Save rig…" (fixtures/rig.py's shared serializer).

Moving-head calibration = measurements + one aim capture:

  1. Enter the room size; drag the ``dj_booth`` marker to where the booth is.
  2. Select the head; enter its X/Y, height (lens above floor) and facing.
  3. Tick "Jog live" (show running, Freeze off) and move Pan/Tilt until the
     real beam sits on the booth. The pin goes through the normal slew
     limit, so the head glides.
  4. Target = dj_booth, click SET AIM: the offsets are solved from that one
     capture (geometry.calibrate_from_capture).
  5. "Aim at target" with dancefloor_centre to verify; wrong direction ->
     tick Invert pan/tilt and SET AIM again. Consistently short/long means
     the profile's pan/tilt range constants are wrong (placeholders).
  6. Optionally "Set home = target" (writes home_pan/home_tilt so the ±10 %
     bounded scene motion is centred there). Untick Jog live. Save rig…

Jogging pins channels through merge/merge.py's CalibrationPins -- applied
after the merge and before finalize, so the laser arm switch and the
movement slew limit still apply to a pinned fixture. Leaving the tab clears every pin.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Callable

from PySide6 import QtCore, QtWidgets

from ..fixtures.geometry import (
    HeadCalibration,
    Point3,
    Venue,
    aim,
    calibrate_from_capture,
    calibrate_from_two_captures,
    ft_to_m,
    m_to_ft,
)
from ..fixtures.profiles import Laser, MotionBounds, MovingHead, _to16
from ..fixtures.rig import Rig, RigError, save_rig
from ..merge.merge import CalibrationPins
from .floorplan import DEFAULT_VENUE_FT, FloorPlanView
from .theme import MUTED

log = logging.getLogger(__name__)

_MAX_FT = 500.0


_NOT_PLACED = ("{} is not placed yet: the X / Y shown is only where it is drawn. "
               "Type its X, Y and Height (or drag it) first.")


def _ft_box(value_m: float, decimals: int = 1, step: float = 0.5, lo: float = 0.0, hi: float = _MAX_FT) -> QtWidgets.QDoubleSpinBox:
    b = QtWidgets.QDoubleSpinBox()
    b.setDecimals(decimals)
    b.setRange(lo, hi)
    b.setSingleStep(step)
    b.setSuffix(" ft")
    b.setValue(m_to_ft(value_m))
    b.setKeyboardTracking(False)
    return b


def _deg_box(value: float, lo: float = -180.0, hi: float = 180.0) -> QtWidgets.QDoubleSpinBox:
    b = QtWidgets.QDoubleSpinBox()
    b.setDecimals(1)
    b.setRange(lo, hi)
    b.setSingleStep(5.0)
    b.setSuffix("°")
    b.setValue(value)
    b.setKeyboardTracking(False)
    return b


def _hint(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{MUTED}; font-size:12px;")
    return lbl


class _WheelGuard(QtCore.QObject):
    """Event filter: an unfocused value widget ignores the wheel so the
    enclosing scroll area scrolls instead (Qt propagates an ignored wheel
    event to the parent)."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QtCore.QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return super().eventFilter(obj, event)


class VenueTab(QtWidgets.QWidget):
    """``on_pins`` receives a CalibrationPins (or None) whenever the jog /
    probe state changes -- the console routes it to ``app.manual``."""

    def __init__(self, rig: Rig, rig_path: str | None,
                 on_pins: Callable[[CalibrationPins | None], None] | None = None,
                 is_frozen: Callable[[], bool] | None = None, active: bool = True) -> None:
        super().__init__()
        self.rig = rig
        self.rig_path = rig_path
        self.on_pins = on_pins
        self.is_frozen = is_frozen or (lambda: False)
        self._selected: str = ""
        self._loading = False
        # 2026-10-26: pins (full-white beam, jog, laser probe) only while this tab is
        # on screen. Built hidden inside the console, selecting the first fixture at
        # construction used to light a moving head and hold off the auto-blackout
        # from launch; the console now passes active=False and calls activate().
        self._active = active
        if self.rig.venue is None:
            w, l, h = (ft_to_m(v) for v in DEFAULT_VENUE_FT)
            self.rig.venue = Venue(w, l, h, Venue.default_targets(w, l, h))
            self.rig.refresh_geometry()

        # Rig.refresh_geometry only ever overwrites `side` from x: keep the sides
        # the rig came with, so Reset positions can give them back
        self._loaded_sides = {getattr(fx, "slot_id", fx.label): fx.side for fx in rig.fixtures}

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        outer.addWidget(self._build_venue_bar())

        split = QtWidgets.QSplitter()
        self.plan = FloorPlanView(rig, editable=True, on_changed=self._plan_changed)
        self.plan.fixture_selected.connect(self._on_plan_selected)
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.plan, 1)              # the whole column: the targets grid lives in the panel
        split.addWidget(left)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        panel = self._build_property_panel()
        scroll.setWidget(panel)
        # 2026-10-10 (Josh: "I can't scroll on the right section"): the panel
        # is wall-to-wall spin boxes / combos / sliders, and Qt hands the
        # mouse wheel to whichever one is under the cursor (changing its
        # value) instead of scrolling. Those widgets now take the wheel only
        # while focused (clicked); otherwise the wheel reaches the scroll area.
        self._wheel_guard = _WheelGuard(self)
        for cls in (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QAbstractSlider):
            for child in panel.findChildren(cls):
                if isinstance(child, QtWidgets.QScrollBar):
                    continue
                child.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
                child.installEventFilter(self._wheel_guard)
        split.addWidget(scroll)
        split.setSizes([760, 480])
        outer.addWidget(split, 1)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)      # two warnings + "Unsaved" outrun 1280 px on one line
        outer.addWidget(self.status)
        self._select_first()

    # ------------------------------------------------------------------ venue bar
    def _build_venue_bar(self) -> QtWidgets.QWidget:
        v = self.rig.venue
        box = QtWidgets.QGroupBox("Venue")
        h = QtWidgets.QHBoxLayout(box)
        self.sb_width = _ft_box(v.width_m, lo=3.0)
        self.sb_length = _ft_box(v.length_m, lo=3.0)
        self.sb_height = _ft_box(v.height_m if v.height_m is not None else ft_to_m(12.0), lo=3.0)
        for cap, w in (("Width", self.sb_width), ("Length", self.sb_length), ("Ceiling", self.sb_height)):
            h.addWidget(QtWidgets.QLabel(cap))
            h.addWidget(w)
        h.addStretch(1)
        self.b_reset = QtWidgets.QPushButton("Reset positions")
        self.b_save = QtWidgets.QPushButton("Save rig…")
        h.addWidget(self.b_reset)
        h.addWidget(self.b_save)
        for b, dim in ((self.sb_width, "width_m"), (self.sb_length, "length_m"), (self.sb_height, "height_m")):
            b.valueChanged.connect(lambda v, d=dim: self._venue_changed(d, v))
        self.b_reset.clicked.connect(self._reset_positions)
        self.b_save.clicked.connect(self._save)
        return box

    def _venue_changed(self, dim: str, value_ft: float) -> None:
        if self._loading:
            return
        v = self.rig.venue
        # only the edited dimension: the boxes show 0.1 ft, and a file with no
        # ceiling keeps none until the Ceiling box itself is edited
        setattr(v, dim, ft_to_m(value_ft))
        self.rig.refresh_geometry()
        self.plan.refresh_layout()
        self._refresh_targets()
        eps = 1e-6
        outside = [fx.label for fx in self.rig.fixtures if fx.position is not None
                   and not (-eps <= fx.position.x <= v.width_m + eps and -eps <= fx.position.y <= v.length_m + eps)]
        outside += [n for n, p in v.targets.items()
                    if not (-eps <= p.x <= v.width_m + eps and -eps <= p.y <= v.length_m + eps)]
        self._mark_dirty(f"Now outside the room: {', '.join(outside)} — move them back in (or enlarge the room)."
                         if outside else "")

    # ------------------------------------------------------------------ targets
    def _build_targets(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Targets (named aim points, ft)")
        self._targets_form = QtWidgets.QGridLayout(box)
        self._target_boxes: dict[str, tuple[QtWidgets.QDoubleSpinBox, ...]] = {}
        self._refresh_targets()
        return box

    def _refresh_targets(self) -> None:
        # rebuild the small grid: name  X  Y  Z
        while self._targets_form.count():
            item = self._targets_form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._target_boxes.clear()
        for col, cap in enumerate(("", "X", "Y", "Height")):
            self._targets_form.addWidget(_hint(cap), 0, col)
        for row, (name, p) in enumerate(sorted(self.rig.venue.targets.items()), start=1):
            self._targets_form.addWidget(QtWidgets.QLabel(name), row, 0)
            boxes = (_ft_box(p.x), _ft_box(p.y), _ft_box(p.z))
            for col, b in enumerate(boxes, start=1):
                self._targets_form.addWidget(b, row, col)
                b.valueChanged.connect(lambda v, n=name, a="xyz"[col - 1]: self._target_edited(n, a, v))
                if hasattr(self, "_wheel_guard"):   # rebuilt boxes scroll the panel too, like the first ones
                    b.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
                    b.installEventFilter(self._wheel_guard)
            self._target_boxes[name] = boxes

    def _target_edited(self, name: str, axis: str, value_ft: float) -> None:
        if self._loading:
            return
        # only the edited axis: the boxes show 0.1 ft, the file keeps more
        p = self.rig.venue.targets[name]
        self.rig.venue.targets[name] = dataclasses.replace(p, **{axis: ft_to_m(value_ft)})
        self.plan.refresh_layout()
        self._update_laser_readout()
        self._mark_dirty(self._target_captured_warning([name]))

    def _target_captured_warning(self, names: list[str]) -> str:
        """Same as _captured_warning, for the other end of the capture: the
        offsets were solved against where the target WAS. Kept as is (right if
        the target really moved, wrong if its old numbers were a typo)."""
        heads, used = [], set()
        for fx in self.rig.fixtures:
            if isinstance(fx, MovingHead) and fx.calibration.captured:
                on = {fx.calibration.target, fx.calibration.target2} & set(names)
                if on:
                    heads.append(fx.label); used |= on
        if not heads:
            return ""
        return (f"{', '.join(heads)}: aim captured on {', '.join(sorted(used))} at its old spot: check it with "
                "Aim at target, and SET AIM again if the beam misses.")

    # ------------------------------------------------------------------ property panel
    def _build_property_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)

        # fixture picker + placement
        place = self.grp_place = QtWidgets.QGroupBox("Fixture")
        form = QtWidgets.QFormLayout(place)
        self.cb_fixture = QtWidgets.QComboBox()
        for fx in self.rig.fixtures:
            self.cb_fixture.addItem(f"{fx.label}  ·  addr {fx.address}", getattr(fx, "slot_id", fx.label))
        self.cb_fixture.currentIndexChanged.connect(self._on_combo)
        form.addRow("Select", self.cb_fixture)
        self.sb_x = _ft_box(0.0)
        self.sb_y = _ft_box(0.0)
        self.sb_z = _ft_box(0.0, decimals=2, step=0.25)
        self.sb_facing = _deg_box(0.0)
        form.addRow("X (along booth wall)", self.sb_x)
        form.addRow("Y (out into the crowd)", self.sb_y)
        form.addRow("Height (lens above floor)", self.sb_z)
        form.addRow("Facing (0 = into the crowd)", self.sb_facing)
        for b, axis in ((self.sb_x, "x"), (self.sb_y, "y"), (self.sb_z, "z"), (self.sb_facing, "facing")):
            b.valueChanged.connect(lambda v, a=axis: self._placement_edited(a, v))
        v.addWidget(place)

        # moving head: calibrate
        self.grp_head = QtWidgets.QGroupBox("Calibrate (moving head)")
        hf = QtWidgets.QFormLayout(self.grp_head)
        self.cb_light = QtWidgets.QCheckBox("Light the selected head — open white at full,\n"
                                         "so you can see the beam")
        self.cb_light.setChecked(True)
        self.cb_jog = QtWidgets.QCheckBox("Jog live — pins the REAL head to the sliders")
        self.s_pan = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal); self.s_pan.setRange(0, 65535); self.s_pan.setValue(32768)
        self.s_tilt = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal); self.s_tilt.setRange(0, 65535); self.s_tilt.setValue(32768)
        self.l_pan = QtWidgets.QLabel("0.500"); self.l_tilt = QtWidgets.QLabel("0.500")
        pan_row = QtWidgets.QHBoxLayout(); pan_row.addWidget(self.s_pan, 1); pan_row.addWidget(self.l_pan)
        tilt_row = QtWidgets.QHBoxLayout(); tilt_row.addWidget(self.s_tilt, 1); tilt_row.addWidget(self.l_tilt)
        self.cb_target = QtWidgets.QComboBox()
        self.cb_invert_pan = QtWidgets.QCheckBox("Invert pan")
        self.cb_invert_tilt = QtWidgets.QCheckBox("Invert tilt")
        inv = QtWidgets.QHBoxLayout(); inv.addWidget(self.cb_invert_pan); inv.addWidget(self.cb_invert_tilt); inv.addStretch(1)
        self.b_set_aim = QtWidgets.QPushButton("SET AIM — the head is on this target now")
        self.b_set_aim.setObjectName("cue")
        # two-line labels (this, Light, ADD BOUND POINT): on one line they made the
        # panel wider than its column at 1280 x 800 (labels cut, help text off the edge)
        self.b_set_aim2 = QtWidgets.QPushButton("ADD 2ND POINT — the head is on a DIFFERENT\n"
                                                "target now (solves direction + range)")
        self.b_aim = QtWidgets.QPushButton("Aim at target (verify)")
        self.b_home = QtWidgets.QPushButton("Set home = target")
        self.l_cal = QtWidgets.QLabel("Not captured")
        self.l_cal.setWordWrap(True)
        hf.addRow(self.cb_light)
        hf.addRow(self.cb_jog)
        hf.addRow("Pan", pan_row)
        hf.addRow("Tilt", tilt_row)
        hf.addRow("Target", self.cb_target)
        hf.addRow(inv)
        hf.addRow(self.b_set_aim)
        hf.addRow(self.b_set_aim2)
        btns = QtWidgets.QHBoxLayout(); btns.addWidget(self.b_aim); btns.addWidget(self.b_home)
        hf.addRow(btns)
        hf.addRow(self.l_cal)
        # Raw motion box (2026-10-10): jog the beam onto each edge the crowd
        # can see, ADD BOUND POINT each time; every scene then stays inside.
        self.l_bounds = QtWidgets.QLabel("Motion box: none — the head sweeps its full travel")
        self.l_bounds.setWordWrap(True)
        self.b_bound_add = QtWidgets.QPushButton("ADD BOUND POINT —\nthe beam is on an edge now")
        self.b_bound_add.setObjectName("cue")
        self.b_bound_clear = QtWidgets.QPushButton("Clear box")
        brow = QtWidgets.QHBoxLayout(); brow.addWidget(self.b_bound_add, 1); brow.addWidget(self.b_bound_clear)
        hf.addRow(_hint("MOTION BOX (no geometry needed): tick Jog live, slide the beam onto the venue's "
                        "LEFT edge → ADD BOUND POINT; RIGHT edge → ADD BOUND POINT; then the lowest and "
                        "highest points you want it to reach (booth edges / far wall) → ADD BOUND POINT each. "
                        "Every scene's movement is then squeezed inside that box. Untick Jog live, Save rig…"))
        hf.addRow(brow)
        hf.addRow(self.l_bounds)
        hf.addRow(_hint("Selecting a head lights it (the no-music blackout is held off while it is pinned).  "
                        "1 Place the head and the dj_booth target.  2 Tick Jog live, slide Pan/Tilt until the "
                        "real beam sits on the booth.  3 SET AIM.  4 Jog onto a SECOND, well-separated target, "
                        "pick it, ADD 2ND POINT — this solves pan/tilt direction and travel for this unit as hung "
                        "(the Invert boxes are set for you).  5 Aim at a third target to verify.  "
                        "6 Untick Jog live, Save rig…"))
        v.addWidget(self.grp_head)
        self.cb_light.toggled.connect(self._light_toggled)
        self.cb_jog.toggled.connect(self._jog_toggled)
        self.s_pan.valueChanged.connect(self._sliders_moved)
        self.s_tilt.valueChanged.connect(self._sliders_moved)
        self.b_set_aim.clicked.connect(self._set_aim)
        self.b_set_aim2.clicked.connect(self._set_aim_second)
        self.b_bound_add.clicked.connect(self._bound_add)
        self.b_bound_clear.clicked.connect(self._bound_clear)
        self.b_aim.clicked.connect(self._aim_at_target)
        self.b_home.clicked.connect(self._set_home)

        # laser: beam geometry + probe
        self.grp_laser = QtWidgets.QGroupBox("Laser beam")
        lf = QtWidgets.QFormLayout(self.grp_laser)
        self.sb_beam_tilt = _deg_box(0.0, -90.0, 90.0)
        self.sb_fan = _deg_box(30.0, 1.0, 90.0)
        self.s_hoff = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal); self.s_hoff.setRange(0, 127); self.s_hoff.setValue(64)
        self.l_hoff = QtWidgets.QLabel("64")
        hrow = QtWidgets.QHBoxLayout(); hrow.addWidget(self.s_hoff, 1); hrow.addWidget(self.l_hoff)
        self.cb_probe = QtWidgets.QCheckBox("Probe static line live (needs Arm lasers)")
        lf.addRow("Beam tilt (+ up)", self.sb_beam_tilt)
        lf.addRow("Fan half-angle", self.sb_fan)
        lf.addRow("H offset (CH7 0-127, UNVERIFIED)", hrow)
        lf.addRow(self.cb_probe)
        lf.addRow(_hint("CH7's 0-127 half has never been probed on this unit. Tick Probe, sweep H offset, "
                        "and note whether the line moves, stays, or starts sweeping."))
        v.addWidget(self.grp_laser)
        self.sb_beam_tilt.valueChanged.connect(self._laser_edited)
        self.sb_fan.valueChanged.connect(self._laser_edited)
        self.s_hoff.valueChanged.connect(self._laser_edited)
        self.cb_probe.toggled.connect(self._probe_toggled)

        v.addWidget(self._build_targets())
        v.addStretch(1)
        return panel

    # ------------------------------------------------------------------ selection
    def _select_first(self) -> None:
        if self.rig.fixtures:
            self._load_fixture(getattr(self.rig.fixtures[0], "slot_id", self.rig.fixtures[0].label))

    def _on_combo(self, idx: int) -> None:
        key = self.cb_fixture.itemData(idx)
        if key and key != self._selected:
            self._load_fixture(key)
            self.plan.select(key)

    def _on_plan_selected(self, key: str) -> None:
        if key and key != self._selected:
            self._load_fixture(key)
            i = self.cb_fixture.findData(key)
            if i >= 0:
                self.cb_fixture.blockSignals(True)
                self.cb_fixture.setCurrentIndex(i)
                self.cb_fixture.blockSignals(False)

    def _fx(self):
        return self.rig.fixture(self._selected) if self._selected else None

    def _load_fixture(self, key: str) -> None:
        """Fill the panel from a fixture. Leaving a fixture clears its pins."""
        self._clear_pins()
        self._selected = key
        fx = self._fx()
        self._loading = True
        try:
            o = self.plan.fixture_origin(fx)
            self.sb_x.setValue(m_to_ft(o.x)); self.sb_y.setValue(m_to_ft(o.y)); self.sb_z.setValue(m_to_ft(o.z))
            self.sb_facing.setValue(fx.facing_deg)
            self._update_place_title()
            is_head, is_laser = isinstance(fx, MovingHead), isinstance(fx, Laser)
            self.grp_head.setVisible(is_head)
            self.grp_laser.setVisible(is_laser)
            if is_head and self.cb_light.isChecked():
                self._push_head_pins()  # 2026-10-05: light the head on select, so it can be calibrated
            if is_head:
                cal = fx.calibration
                self.cb_invert_pan.setChecked(cal.invert_pan)
                self.cb_invert_tilt.setChecked(cal.invert_tilt)
                self.s_pan.setValue(int(round((cal.pan01 if cal.pan01 is not None else 0.5) * 65535)))
                self.s_tilt.setValue(int(round((cal.tilt01 if cal.tilt01 is not None else 0.5) * 65535)))
                self.cb_target.clear()
                self.cb_target.addItems(sorted(self.rig.venue.targets))
                if cal.target in self.rig.venue.targets:
                    self.cb_target.setCurrentText(cal.target)
                elif "dj_booth" in self.rig.venue.targets:
                    self.cb_target.setCurrentText("dj_booth")   # the capture target in the documented flow
                self._update_cal_label()
            if is_laser:
                self.sb_beam_tilt.setValue(fx.beam_tilt_deg)
                self.sb_fan.setValue(fx.fan_half_angle_deg)
                self.s_hoff.setValue(int(round(fx.h_offset01 * 127)))
                self._update_laser_readout()
        finally:
            self._loading = False

    # ------------------------------------------------------------------ placement
    def _placement_edited(self, axis: str, value: float) -> None:
        if self._loading:
            return
        fx = self._fx()
        if fx is None:
            return
        # only the edited axis: the boxes show 0.1 ft, the file keeps more
        p = self.plan.fixture_origin(fx)
        if axis == "facing":
            fx.facing_deg = value
            fx.position = p
        else:
            fx.position = dataclasses.replace(p, **{axis: ft_to_m(value)})
        self.rig.refresh_geometry()
        self.plan.set_fixture_position(self._selected, fx.position)
        self.plan.refresh_layout()
        self.plan.select(self._selected)
        self._update_place_title()
        self._update_laser_readout()
        self._mark_dirty(self._captured_warning(fx))

    @staticmethod
    def _captured_warning(fx) -> str:
        """A SET AIM capture is solved against the position/facing it was
        taken at (geometry.calibrate_from_capture); after a move it is kept
        as is -- right if the head was re-hung the same way, wrong if the old
        numbers were a typo. Say so; don't guess."""
        if not isinstance(fx, MovingHead) or not fx.calibration.captured:
            return ""
        return (f"{fx.label}'s aim was captured at its old position/facing: check it with "
                "Aim at target, and SET AIM again if the beam misses.")

    def _update_place_title(self) -> None:
        fx = self._fx()
        self.grp_place.setTitle("Fixture" if fx is None or fx.position is not None else
                                "Fixture — not placed yet")

    def _plan_changed(self) -> None:
        """A drag on the plan moved a fixture or a target."""
        fx = self._fx()
        note = ""
        if fx is not None:
            self._loading = True
            try:
                o = self.plan.fixture_origin(fx)
                if abs(self.sb_x.value() - m_to_ft(o.x)) > 0.05 or abs(self.sb_y.value() - m_to_ft(o.y)) > 0.05:
                    note = self._captured_warning(fx)      # the drag moved the selected head
                self.sb_x.setValue(m_to_ft(o.x)); self.sb_y.setValue(m_to_ft(o.y))
            finally:
                self._loading = False
            self._update_place_title()
        self._loading = True
        try:
            moved = []
            for name, (bx, by, bz) in self._target_boxes.items():
                p = self.rig.venue.targets.get(name)
                if p is not None:
                    if abs(bx.value() - m_to_ft(p.x)) > 0.05 or abs(by.value() - m_to_ft(p.y)) > 0.05:
                        moved.append(name)                 # the drag moved this target
                    bx.setValue(m_to_ft(p.x)); by.setValue(m_to_ft(p.y)); bz.setValue(m_to_ft(p.z))
        finally:
            self._loading = False
        self._update_laser_readout()
        self._mark_dirty(" ".join(n for n in (note, self._target_captured_warning(moved)) if n))

    def _reset_positions(self) -> None:
        self._clear_pins()
        for fx in self.rig.fixtures:
            fx.position = None
            fx.facing_deg = 0.0
            fx.side = self._loaded_sides.get(getattr(fx, "slot_id", fx.label), fx.side)
        self.rig.refresh_geometry()
        self.plan.refresh_layout()
        if self._selected:
            self._load_fixture(self._selected)
        self._mark_dirty()

    # ------------------------------------------------------------------ moving-head calibration
    def _pan_tilt01(self) -> tuple[float, float]:
        return self.s_pan.value() / 65535, self.s_tilt.value() / 65535

    def _sliders_moved(self) -> None:
        pan01, tilt01 = self._pan_tilt01()
        self.l_pan.setText(f"{pan01:.3f}"); self.l_tilt.setText(f"{tilt01:.3f}")
        if self.cb_jog.isChecked():
            self._push_head_pins()

    def _jog_toggled(self, on: bool) -> None:
        if on and self.is_frozen():
            self.status.setText("Freeze is on — unfreeze (F) to jog the head.")
        self._push_head_pins()

    def _light_toggled(self, on: bool) -> None:
        self._push_head_pins()

    def _push_head_pins(self) -> None:
        """Pin the selected head: the calibration beam (open white, full,
        strobe off, mid speed) while "Light" is ticked, plus pan/tilt from
        the sliders while "Jog live" is ticked. Neither ticked = no pins.
        (2026-10-05: selecting a head used to do nothing until Jog live was
        ticked, and even then the no-music auto-blackout -- core/show_engine.py
        -- blacked it out; now the pins hold that failsafe off.)"""
        fx = self._fx()
        if not isinstance(fx, MovingHead) or self.on_pins is None or not self._active:
            return
        light = self.cb_light.isChecked()
        jog = self.cb_jog.isChecked()
        if not (light or jog):
            self.on_pins(None)
            return
        a = fx.address
        channels: dict[int, int] = {}
        if light:
            channels.update({a + 4: 128, a + 5: 255, a + 6: 0, a + 7: 0})  # speed, dimmer, strobe off, colour open
        if jog:
            pan01, tilt01 = self._pan_tilt01()
            ph, pl = _to16(pan01)
            th, tl = _to16(tilt01)
            channels.update({a: ph, a + 1: pl if fx.USE_FINE_CHANNELS else 0,
                             a + 2: th, a + 3: tl if fx.USE_FINE_CHANNELS else 0})
        self.on_pins(CalibrationPins(channels=channels,
                                     absolute_slots=frozenset({self._selected}) if jog else frozenset()))

    def _clear_pins(self) -> None:
        if self.on_pins is not None:
            self.on_pins(None)
        for cb in (getattr(self, "cb_jog", None), getattr(self, "cb_probe", None)):
            if cb is not None and cb.isChecked():
                cb.blockSignals(True); cb.setChecked(False); cb.blockSignals(False)

    def _target_point(self) -> tuple[str, Point3] | None:
        name = self.cb_target.currentText()
        p = self.rig.venue.targets.get(name)
        return (name, p) if p is not None else None

    def _head_is_live(self) -> bool:
        """A capture stores the sliders: only true of the real head while
        Jog live pins it there (and Freeze is not holding the output)."""
        if not self.cb_jog.isChecked():
            self.status.setText("Tick Jog live first: the sliders only move the real head while it is on.")
            return False
        if self.is_frozen():
            self.status.setText("Freeze is on — unfreeze (F) so the head is where the sliders say.")
            return False
        return True

    def _set_aim(self) -> None:
        fx = self._fx()
        tp = self._target_point()
        if not isinstance(fx, MovingHead) or tp is None:
            return
        if not self._head_is_live():
            return
        if fx.position is None:
            self.status.setText(_NOT_PLACED.format(fx.label))
            return
        name, target = tp
        pan01, tilt01 = self._pan_tilt01()
        fx.calibration = calibrate_from_capture(
            fx.position, fx.facing_deg, target, pan01, tilt01, fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG,
            invert_pan=self.cb_invert_pan.isChecked(), invert_tilt=self.cb_invert_tilt.isChecked(),
            target_name=name)
        self._update_cal_label()
        self._mark_dirty(f"Captured {fx.label} on {name}. Now 'Aim at target' with another target to verify.")

    def _set_aim_second(self) -> None:
        """Second capture: the head is now on a DIFFERENT target. Together
        with the SET AIM capture this solves each axis's direction and travel
        (geometry.calibrate_from_two_captures), so a verify aim goes TO the
        target instead of mirroring the move away from the first one."""
        fx = self._fx()
        tp = self._target_point()
        if not isinstance(fx, MovingHead) or tp is None:
            return
        if not self._head_is_live():
            return
        if fx.position is None:
            self.status.setText(_NOT_PLACED.format(fx.label))
            return
        c: HeadCalibration = fx.calibration
        if not c.captured or c.pan01 is None or c.tilt01 is None or c.target not in self.rig.venue.targets:
            self.status.setText("SET AIM on the first target before adding a second point.")
            return
        name_b, target_b = tp
        if name_b == c.target:
            self.status.setText(f"Pick a target other than {c.target} for the second point.")
            return
        pan_b, tilt_b = self._pan_tilt01()
        target_a = self.rig.venue.targets[c.target]
        fx.calibration = calibrate_from_two_captures(
            fx.position, fx.facing_deg,
            target_a, c.pan01, c.tilt01, target_b, pan_b, tilt_b,
            fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG,
            target_a_name=c.target, target_b_name=name_b)
        nc = fx.calibration
        for cb, val in ((self.cb_invert_pan, nc.invert_pan), (self.cb_invert_tilt, nc.invert_tilt)):
            cb.blockSignals(True); cb.setChecked(val); cb.blockSignals(False)
        weak = []
        if nc.pan_range_deg is None:
            weak.append("pan")
        if nc.tilt_range_deg is None:
            weak.append("tilt")
        self._update_cal_label()
        msg = f"Solved {fx.label} from {c.target} + {name_b}. Now 'Aim at target' with a third target to verify."
        if weak:
            msg += f"  ({'/'.join(weak)} barely moved between the two points — kept the profile's range for it; pick points further apart.)"
        self._mark_dirty(msg)

    def _aim_at_target(self) -> None:
        fx = self._fx()
        tp = self._target_point()
        if not isinstance(fx, MovingHead) or tp is None or fx.position is None:
            return
        name, target = tp
        pan01, tilt01, ok = aim(fx.position, fx.facing_deg, target, fx.calibration, fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
        self.s_pan.setValue(int(round(pan01 * 65535)))
        self.s_tilt.setValue(int(round(tilt01 * 65535)))
        self.status.setText(f"Aimed at {name}: pan {pan01:.3f} tilt {tilt01:.3f}"
                            + ("" if ok else "  — OUT OF RANGE, clamped"))

    def _set_home(self) -> None:
        fx = self._fx()
        tp = self._target_point()
        if not isinstance(fx, MovingHead) or tp is None or fx.position is None:
            return
        name, target = tp
        pan01, tilt01, ok = aim(fx.position, fx.facing_deg, target, fx.calibration, fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
        fx.home_pan, fx.home_tilt = pan01, tilt01
        self._update_cal_label()
        self._mark_dirty(f"Home = {name} (pan {pan01:.3f}, tilt {tilt01:.3f})" + ("" if ok else " — clamped")
                         + ("  — unused while a motion box is set (Clear box to use it)"
                            if fx.bounds is not None else ""))

    # --- raw motion box (2026-10-10) ---
    def _bound_add(self) -> None:
        fx = self._fx()
        if not isinstance(fx, MovingHead) or not self._head_is_live():
            return
        pan01, tilt01 = self._pan_tilt01()
        fx.bounds = (fx.bounds.include(pan01, tilt01) if fx.bounds is not None
                     else MotionBounds.point(pan01, tilt01))
        self._update_cal_label()
        b = fx.bounds
        if b.pan_max - b.pan_min < 0.01 and b.tilt_max - b.tilt_min < 0.01:
            self._mark_dirty(f"{fx.label}: first edge stored (pan {pan01:.3f} / tilt {tilt01:.3f}). "
                             "Jog to the opposite edge and ADD BOUND POINT again.")
        else:
            self._mark_dirty(f"{fx.label} motion box: pan {b.pan_min:.3f}–{b.pan_max:.3f}, "
                             f"tilt {b.tilt_min:.3f}–{b.tilt_max:.3f}. Add more edges or Save rig…")

    def _bound_clear(self) -> None:
        fx = self._fx()
        if not isinstance(fx, MovingHead):
            return
        fx.bounds = None
        self._update_cal_label()
        self._mark_dirty(f"{fx.label}: motion box cleared — full travel again.")

    def _update_bounds_label(self) -> None:
        fx = self._fx()
        if not isinstance(fx, MovingHead) or not hasattr(self, "l_bounds"):
            return
        b = fx.bounds
        if b is None:
            self.l_bounds.setText("Motion box: none — the head sweeps its full travel")
        else:
            self.l_bounds.setText(f"Motion box: pan {b.pan_min:.3f} → {b.pan_max:.3f}  ·  "
                                  f"tilt {b.tilt_min:.3f} → {b.tilt_max:.3f}  (every scene stays inside)")

    def _update_cal_label(self) -> None:
        fx = self._fx()
        if not isinstance(fx, MovingHead):
            return
        self._update_bounds_label()
        c: HeadCalibration = fx.calibration
        home = (f"  ·  home {fx.home_pan:.2f}/{fx.home_tilt:.2f}"
                + (" (unused: the motion box wins)" if fx.bounds is not None else "")
                if fx.home_pan is not None and fx.home_tilt is not None else "")
        if c.captured:
            two = (f"  ·  2-point ({c.target2}): pan travel {c.pan_range_deg:.0f}°, tilt travel {c.tilt_range_deg:.0f}°"
                   if c.pan_range_deg and c.tilt_range_deg else
                   f"  ·  2-point ({c.target2}), partial" if c.target2 else "")
            self.l_cal.setText(f"Captured on {c.target}: pan offset {c.pan_offset_deg:+.1f}°, "
                               f"tilt offset {c.tilt_offset_deg:+.1f}°{two}"
                               f"{' · pan inverted' if c.invert_pan else ''}"
                               f"{' · tilt inverted' if c.invert_tilt else ''}{home}")
        else:
            self.l_cal.setText("Not captured" + home)

    # ------------------------------------------------------------------ laser
    def _laser_edited(self) -> None:
        self.l_hoff.setText(str(self.s_hoff.value()))
        if self._loading:
            return
        fx = self._fx()
        if not isinstance(fx, Laser):
            return
        fx.beam_tilt_deg = self.sb_beam_tilt.value()
        fx.fan_half_angle_deg = self.sb_fan.value()
        fx.h_offset01 = self.s_hoff.value() / 127
        self._update_laser_readout()
        if self.cb_probe.isChecked():
            self._push_laser_pins()
        self._mark_dirty()

    def _update_laser_readout(self) -> None:
        """Intentionally empty since 2026-10-03 (Josh's call); kept so every
        caller stays valid."""
        return

    def _probe_toggled(self, on: bool) -> None:
        if on:
            self._push_laser_pins()
        else:
            self._clear_pins()

    def _push_laser_pins(self) -> None:
        fx = self._fx()
        if not isinstance(fx, Laser) or self.on_pins is None or not self._active:
            return
        from ..merge.merge import Contribution
        c = Contribution()
        fx.set_static_line(c, Laser.COLOR_GREEN, fx.h_offset01)
        channels = {ch: c.values[ch - 1] for ch in fx.channels() if c.touched[ch - 1]}
        self.on_pins(CalibrationPins(channels=channels, laser_open=True))

    # ------------------------------------------------------------------ save
    def _mark_dirty(self, note: str = "") -> None:
        """Flag unsaved edits; ``note`` is the action's own message, kept in
        front (it used to be overwritten the moment it was set)."""
        self.status.setText((note + "  ·  " if note else "")
                            + "Unsaved changes — Save rig… writes them to the rig file.")
        self.status.setStyleSheet(f"color:{MUTED};")

    def _save(self) -> None:
        default_path = self.rig_path or os.path.join("rigs", "custom_rig.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save rig", default_path, "JSON (*.json)")
        if not path:
            return
        try:
            # the rig's own description survives; the stock line only fills an empty one
            save_rig(self.rig, path, self.rig.description
                     or "Venue layout + calibration edited in the LiveReact console's Venue tab.")
        except RigError as exc:
            self.status.setText(f"Not saved — {exc}")
            self.status.setStyleSheet("color:#c33;")
            return
        self.rig_path = path                    # the next Save offers the file just written
        self.status.setText(f"Saved to {path}. Positions/calibration are already live; fixture patching needs a restart.")
        self.status.setStyleSheet("color:#3c6;")

    def activate(self) -> None:
        """Called by the console when the operator opens this tab: re-light the
        selected head (its pins were cleared on leaving) and re-highlight it."""
        self._active = True
        if self._selected:
            self.plan.select(self._selected)
            if isinstance(self._fx(), MovingHead) and self.cb_light.isChecked():
                self._push_head_pins()

    def deactivate(self) -> None:
        """Called by the console when the operator leaves this tab."""
        self._active = False
        self._clear_pins()
