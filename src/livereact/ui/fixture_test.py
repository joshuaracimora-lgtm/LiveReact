"""Manual fixture-test panel for hardware bring-up (docs/04 §7, docs/12 Phase 7).

Drives individual DMX channels directly, bypassing the engines, so you can verify
each of the 8 fixtures one at a time when wiring the rig. A per-channel slider grid
(grouped by fixture) publishes a manual universe straight to the output backend.
"""

from __future__ import annotations

import logging
import os

from PySide6 import QtCore, QtWidgets

from ..core.frame_buffer import UNIVERSE_SIZE, DmxFrameBuffer
from ..fixtures.profiles import MovingHead, _read16
from ..fixtures.rig import Rig, RigError, save_rig
from ..merge.merge import Contribution

log = logging.getLogger(__name__)


class FixtureTestPanel(QtWidgets.QMainWindow):
    def __init__(self, rig: Rig, frame_buffer: DmxFrameBuffer, rig_path: str | None = None) -> None:
        super().__init__()
        self.rig = rig
        self.frame_buffer = frame_buffer
        self.rig_path = rig_path
        self._uni = bytearray(UNIVERSE_SIZE)
        self._sliders: dict[int, QtWidgets.QSlider] = {}  # channel(1-based) -> slider
        self.setWindowTitle("LiveReact — Fixture Test (manual DMX)")

        container = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(container)

        bar = QtWidgets.QHBoxLayout()
        all_off = QtWidgets.QPushButton("All off")
        all_off.clicked.connect(self.all_off)
        bar.addWidget(all_off)
        bar.addStretch(1)
        outer.addLayout(bar)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        grid = QtWidgets.QVBoxLayout(inner)
        for fx in rig.fixtures:
            grid.addWidget(self._fixture_group(fx))
        grid.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self.status = QtWidgets.QLabel("")
        outer.addWidget(self.status)

        self.setCentralWidget(container)
        self.resize(560, 760)
        self._publish()

    def _fixture_group(self, fx) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox(f"{fx.label}  (addr {fx.address}, {fx.type_name})")
        form = QtWidgets.QFormLayout(box)
        kinds = fx.channel_kinds()
        for local, ch in enumerate(fx.channels()):
            row = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            slider.setRange(0, 255)
            value = QtWidgets.QLabel("0")
            value.setMinimumWidth(28)
            slider.valueChanged.connect(lambda v, c=ch, lbl=value: self._on_change(c, v, lbl))
            self._sliders[ch] = slider
            hl.addWidget(slider)
            hl.addWidget(value)
            form.addRow(f"ch {ch} · {kinds[local].name.lower()}", row)
        flash = QtWidgets.QPushButton(f"Flash {fx.label}")
        flash.clicked.connect(lambda _=False, f=fx: self._flash(f))
        form.addRow(flash)
        if isinstance(fx, MovingHead):
            # docs/14 §4: jog pan/tilt above to point the real fixture at the
            # stage, then save that as its calibrated home — all scene
            # movement will oscillate around it within a bounded range
            # afterward, instead of the raw wide sweep.
            home_btn = QtWidgets.QPushButton(f"Save as Home Position ({fx.label})")
            home_btn.clicked.connect(lambda _=False, f=fx: self._save_home_position(f))
            form.addRow(home_btn)
        return box

    # --- actions ---
    def _on_change(self, channel: int, value: int, label: QtWidgets.QLabel) -> None:
        self._uni[channel - 1] = value
        label.setText(str(value))
        self._publish()

    def all_off(self) -> None:
        for ch, slider in self._sliders.items():
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
            self._uni[ch - 1] = 0
        self._publish()

    def _flash(self, fx) -> None:
        """Light one fixture to a recognizable state so you can identify it."""
        self.all_off()
        c = Contribution()
        fx.set_intensity(c, 1.0)
        fx.set_color(c, 1.0, 1.0, 1.0)
        for ch in fx.channels():
            v = c.values[ch - 1]
            if c.touched[ch - 1]:
                self._uni[ch - 1] = v
                self._sliders[ch].blockSignals(True)
                self._sliders[ch].setValue(v)
                self._sliders[ch].blockSignals(False)
        self._publish()

    def _save_home_position(self, fx: MovingHead) -> None:
        # 16-bit (2026-09-18): local ch1/ch2 = pan coarse/fine, ch3/ch4 = tilt.
        pan01 = _read16(self._uni, fx.offset + 0)
        tilt01 = _read16(self._uni, fx.offset + 2)
        fx.home_pan, fx.home_tilt = pan01, tilt01

        default_path = self.rig_path or os.path.join("rigs", "custom_rig.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save rig", default_path, "JSON (*.json)")
        if not path:
            return
        try:
            # The shared serializer (fixtures/rig.py) keeps every other
            # field -- mount heights, positions, unknown keys -- intact.
            save_rig(self.rig, path, self.rig.description
                     or "Home position saved via the fixture-test panel's Save as Home Position.")
        except RigError as exc:
            self.status.setText(f"Not saved — {exc}")
            self.status.setStyleSheet("color:#c33;")
            return
        log.info("Saved %s home position (pan=%.3f tilt=%.3f) to %s", fx.label, pan01, tilt01, path)
        self.rig_path = path
        self.status.setText(
            f"Saved {fx.label} home position (pan={pan01:.2f}, tilt={tilt01:.2f}) to {path} "
            f"— restart with --rig \"{path}\" to apply."
        )
        self.status.setStyleSheet("color:#3c6;")

    def _publish(self) -> None:
        self.frame_buffer.publish(bytes(self._uni))


def run_fixture_test(rig: Rig, backend, clock, dmx_rate_hz: float = 40.0, rig_path: str | None = None) -> int:
    """Open the fixture-test panel, driving ``backend`` via a DMX writer (blocks)."""
    import threading

    from ..output.writer import DmxWriter

    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    frame_buffer = DmxFrameBuffer()
    stop = threading.Event()
    writer = DmxWriter(frame_buffer, backend, clock, stop, hz=dmx_rate_hz)
    writer.start()
    win = FixtureTestPanel(rig, frame_buffer, rig_path=rig_path)
    win.show()
    try:
        return qt.exec()
    finally:
        stop.set()
        writer.join(timeout=3.0)
