"""Operator console — live readouts + manual controls, around the rig view.

The console embodies "automation is the default; manual is a bias/cap, not an
override" (docs/02 §5): the show always runs itself; the operator only nudges
brightness/strobe-cap/color, arms lasers, or hits the hard switches
(blackout/freeze) and skip/tap-tempo. Readouts make the show legible at a glance.

Layout (2026-09-18 redesign, Josh: "easy to see, organized by section and
function so I can track and press buttons while I am DJing"):

    STATUS strip   connections · BPM · beat/bar · deck · scene · BUCKET · health
    left column    live mini-map (read-only) + the status banners + energy bars
    right column   CUES (the big four) · SAFETY / OVERRIDES · FEEL (sliders)

Every control keeps the attribute name and keyboard shortcut it had before
(tests/test_console.py finds them that way); only the grouping, sizing and
theme (ui/theme.py) changed.
"""

from __future__ import annotations

import logging

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.messages import BeatMessage
from ..engines.color import band_levels
from ..engines.laser_feature import MOOD_OFF, laser_color_name
from ..engines.laser_show import MODE_FOLLOW, MODE_HOLD, MODE_ROTATE, look_names
from ..engines.laser_trigger import DUTY_WINDOW_S
from ..tap_tempo import TapTempo
from .floorplan import FloorPlanView
from .rig_config import RigConfigTab
from .theme import MUTED, apply_theme, bucket_color
from .venue_tab import VenueTab

log = logging.getLogger(__name__)


def _slider(lo: int, hi: int, value: int) -> QtWidgets.QSlider:
    s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(value)
    return s


_DOT_OK = "color:#3c6; font-weight:bold;"
_DOT_BAD = "color:#c33; font-weight:bold;"
# Feature-profile laser moods (engines/laser_feature.py) -> chip colors, calm
# to fan reading cool to hot like the bucket chip does.
_MOOD_COLORS = {
    MOOD_OFF: MUTED,
    "calm": "#2a5db0",
    "tension": "#b07a1e",
    "groove": "#7a3fb0",
    "fan": "#c0392b",
}


def _status_dot(label: str) -> QtWidgets.QLabel:
    """A glanceable red/green connection indicator (● label)."""
    dot = QtWidgets.QLabel(f"● {label}")
    dot.setStyleSheet(_DOT_BAD)
    return dot


def _set_dot(dot: QtWidgets.QLabel, ok: bool) -> None:
    dot.setStyleSheet(_DOT_OK if ok else _DOT_BAD)


def _readout(caption: str, value_label: QtWidgets.QLabel) -> QtWidgets.QWidget:
    """A small caption over a big value, for the status strip."""
    w = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(w)
    v.setContentsMargins(6, 0, 6, 0)
    v.setSpacing(0)
    cap = QtWidgets.QLabel(caption.upper())
    cap.setStyleSheet(f"color:{MUTED}; font-size:11px; font-weight:700;")
    value_label.setObjectName("status")
    v.addWidget(cap)
    v.addWidget(value_label)
    return w


def _button(text: str, role: str | None = None, checkable: bool = False) -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    if role:
        b.setObjectName(role)  # styled by ui/theme.py (#cue / #danger)
    b.setCheckable(checkable)
    b.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
    return b


class ConsoleWindow(QtWidgets.QMainWindow):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.tap = TapTempo()
        self._tap_beat = 0
        self.setWindowTitle("LiveReact — Operator Console")
        apply_theme()

        # Live floor plan on the Show tab: view only, nothing draggable mid-set.
        self.rig_view = FloorPlanView(app.rig, editable=False)

        show = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(show)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        outer.addWidget(self._build_status_strip())

        split = QtWidgets.QSplitter()
        split.addWidget(self._build_left_column())
        split.addWidget(self._build_right_column())
        split.setSizes([720, 520])
        outer.addWidget(split, 1)

        rig_path = getattr(app.config, "rig_path", None)
        # Venue tab: the editable floor plan + head calibration. Its jog/probe
        # pins land on app.manual.calibration (read once per show-engine tick)
        # and are cleared the moment the operator leaves the tab.
        self.venue_tab = VenueTab(app.rig, rig_path,
                                  on_pins=lambda pins: setattr(app.manual, "calibration", pins),
                                  is_frozen=lambda: app.manual.freeze, active=False)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(show, "Show")
        # Laser Show tab (2026-10-08): only for rigs on the `show` laser
        # profile (engines/laser_show.py) -- the other profiles have nothing
        # to pick here.
        self.laser_show_tab = None
        if getattr(app.rig, "laser_profile", "") == "show":
            self.laser_show_tab = self._build_laser_show_tab()
            tabs.addTab(self.laser_show_tab, "Laser Show")
        tabs.addTab(self.venue_tab, "Venue")
        self.rig_config = RigConfigTab(app.rig, rig_path)
        self.rig_config_tab = self.rig_config   # the UI agent's name for it (side sync on open)
        tabs.addTab(self.rig_config, "Rig Config")
        tabs.currentChanged.connect(self._tab_changed)
        self.tabs = tabs
        self.setCentralWidget(tabs)
        # Rig menu (2026-10-28, ui/rig_manager.py): pick the rig instead of --rig <path>.
        self.rig_manager = None
        rig_menu = self.menuBar().addMenu("&Rig")
        rig_menu.addAction("Rig manager…", self.open_rig_manager)
        self.fixture_library = None
        fx_menu = self.menuBar().addMenu("&Fixtures")
        fx_menu.addAction("Fixture library…", self.open_fixture_library)
        fx_menu.addAction("Anthropic API key…", self.open_api_key)
        self.resize(1280, 800)

        self._install_shortcuts()

    def _tab_changed(self, idx: int) -> None:
        if self.tabs.widget(idx) is self.venue_tab:
            self.venue_tab.activate()
        else:
            self.venue_tab.deactivate()
            self.venue_tab.plan.select("")
        # the Venue plan is rebuilt on edits; the Show mini-map shares the
        # same rig objects, so re-layout it whenever we come back to Show
        if idx == 0:
            self.rig_view.refresh_layout()
        # the Venue tab moves fixtures (and so their side) under this table
        if self.tabs.widget(idx) is self.rig_config_tab:
            self.rig_config_tab.sync_sides()

    # ------------------------------------------------------------------ build
    def _build_status_strip(self) -> QtWidgets.QWidget:
        """One row across the top: is everything connected, and where in the
        music are we. Read at a glance between mixes."""
        w = QtWidgets.QGroupBox("Status")
        h = QtWidgets.QHBoxLayout(w)
        h.setSpacing(12)

        self.dot_pdl = _status_dot("LINK")
        self.dot_midi = _status_dot("MIDI")
        self.dot_audio = _status_dot("AUDIO")
        self.dot_dmx = _status_dot("DMX")
        conn = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(conn)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        row1 = QtWidgets.QHBoxLayout(); row1.addWidget(self.dot_pdl); row1.addWidget(self.dot_midi)
        row2 = QtWidgets.QHBoxLayout(); row2.addWidget(self.dot_audio); row2.addWidget(self.dot_dmx)
        cv.addLayout(row1)
        cv.addLayout(row2)
        h.addWidget(conn)

        self.l_bpm = QtWidgets.QLabel("—")
        self.l_beat = QtWidgets.QLabel("—")
        self.l_deck = QtWidgets.QLabel("—")
        self.l_scene = QtWidgets.QLabel("—")
        self.l_health = QtWidgets.QLabel("—")
        # Energy bucket (core/show_state.py's debounced `bucket`) -- the single
        # most useful "what does the show think is happening" readout, never
        # shown before 2026-09-18.
        self.l_bucket = QtWidgets.QLabel("—")
        self.l_bucket.setObjectName("bucket")
        self.l_bucket.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.l_bucket.setMinimumWidth(110)
        # Laser profile + mood (2026-10-01): "ACCENT" on the home rig; on the
        # bar's feature rig the lasers are the main light, so what they are
        # doing right now (engines/laser_feature.py's mood) sits beside the
        # bucket that drives it.
        self.l_laser = QtWidgets.QLabel("—")
        self.l_laser.setObjectName("bucket")
        self.l_laser.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.l_laser.setMinimumWidth(130)

        h.addWidget(_readout("BPM", self.l_bpm))
        h.addWidget(_readout("Beat / Bar", self.l_beat))
        h.addWidget(_readout("Deck", self.l_deck))
        h.addWidget(_readout("Scene", self.l_scene), 1)
        h.addWidget(_readout("Bucket", self.l_bucket))
        h.addWidget(_readout("Laser", self.l_laser))
        self.l_health.setStyleSheet(f"color:{MUTED};")
        h.addWidget(self.l_health)
        return w

    def _build_left_column(self) -> QtWidgets.QWidget:
        """Live mini-map + the status banners + the energy bars."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        rig = QtWidgets.QGroupBox("Rig (live)")
        rv = QtWidgets.QVBoxLayout(rig)
        rv.addWidget(self.rig_view, 1)
        # Banner labels: what the automation is doing right now.
        self.l_cue = QtWidgets.QLabel("")
        self.l_arm = QtWidgets.QLabel("")
        self.l_laser_flash = QtWidgets.QLabel("")
        self.l_synth = QtWidgets.QLabel("")
        # docs/14 §2: distinguishes the system's auto-blackout (no music detected)
        # from the operator's manual toggle — they're independent triggers,
        # so the manual button's own checked-state won't reflect the auto one.
        self.l_auto_blackout = QtWidgets.QLabel("")
        for lbl in (self.l_cue, self.l_arm, self.l_laser_flash, self.l_synth, self.l_auto_blackout):
            lbl.setWordWrap(True)
            rv.addWidget(lbl)
        v.addWidget(rig, 1)

        energy = QtWidgets.QGroupBox("Energy")
        form = QtWidgets.QFormLayout(energy)
        self.pb_energy = QtWidgets.QProgressBar(); self.pb_energy.setRange(0, 100)
        self.pb_low = QtWidgets.QProgressBar(); self.pb_low.setRange(0, 100); self.pb_low.setObjectName("low")
        self.pb_mid = QtWidgets.QProgressBar(); self.pb_mid.setRange(0, 100); self.pb_mid.setObjectName("mid")
        self.pb_high = QtWidgets.QProgressBar(); self.pb_high.setRange(0, 100); self.pb_high.setObjectName("high")
        for pb in (self.pb_energy, self.pb_low, self.pb_mid, self.pb_high):
            pb.setTextVisible(False)
        form.addRow("Energy", self.pb_energy)
        form.addRow("Low", self.pb_low)
        form.addRow("Mid", self.pb_mid)
        form.addRow("High", self.pb_high)
        v.addWidget(energy)
        return w

    def _build_right_column(self) -> QtWidgets.QWidget:
        m = self.app.manual
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # --- LASERS / MANUAL OVERRIDE / PRESETS (ui/override_panel.py, 2026-10-28) --
        # The cue buttons (C/D/Space/->/K/J/T) and the laser arm switch (L) live in
        # the panel now; the attribute names below are kept so the hotkeys and
        # everything else that finds them by name are unchanged. Its Manual
        # Override group opens expanded here so the cues stay one press away.
        from .override_panel import OverridePanel
        self.override_panel = OverridePanel(self.app, on_tap=self._do_tap, on_burst=self._strobe_burst)
        self.override_panel.b_overrides.setChecked(True)
        p = self.override_panel
        self.b_cue, self.b_arm, self.b_burst, self.b_skip = p.b_cue, p.b_arm, p.b_burst, p.b_skip
        self.b_laser_cue, self.b_laser_hit, self.b_tap = p.b_laser_cue, p.b_laser_hit, p.b_tap
        self.cb_laser = p.b_laser_arm   # the arm switch: writes manual.laser_enable, as the checkbox did
        v.addWidget(self.override_panel)

        # --- LASER SCENES (laser profile "manual", 2026-10-10, Junior Crib) --
        # Sophia's Laser Show look buttons, on the Show tab: press a scene to
        # cue it on every head, OFF (the launch state) to kill them. Nothing
        # listens to the music (engines/laser_manual.py). Only rigs on that
        # profile get this group -- every other rig's Show tab is unchanged.
        self.laser_scene_buttons: dict[str, QtWidgets.QPushButton] = {}
        if getattr(self.app.rig, "laser_profile", "") == "manual":
            from ..engines.laser_manual import manual_scene_names as scene_names
            eng = self.app.laser_engine
            lasers = QtWidgets.QGroupBox("Lasers — press a scene to cue it (off at launch)")
            ll = QtWidgets.QGridLayout(lasers)
            ll.setSpacing(8)
            self._laser_scene_group = QtWidgets.QButtonGroup(w)
            self._laser_scene_group.setExclusive(True)
            names = ["OFF"] + scene_names()
            for i, name in enumerate(names):
                b = _button(name, "danger" if name == "OFF" else "look", checkable=True)
                self.laser_scene_buttons[name] = b
                self._laser_scene_group.addButton(b)
                ll.addWidget(b, i // 3, i % 3)
                if name == "OFF":
                    b.clicked.connect(eng.off)
                else:
                    b.clicked.connect(lambda _c=False, n=name: eng.cue(n))
            self.laser_scene_buttons[eng.scene or "OFF"].setChecked(True)
            v.addWidget(lasers)

        # --- SAFETY / OVERRIDES: the hard switches ------------------------
        safe = QtWidgets.QGroupBox("Safety / Overrides")
        sl = QtWidgets.QGridLayout(safe)
        sl.setSpacing(8)
        self.b_black = _button("BLACKOUT (B)", "danger", checkable=True)
        self.b_freeze = _button("FREEZE (F)", "danger", checkable=True)
        # Arming lasers (L) is the override panel's arm switch above.
        sl.addWidget(self.b_black, 0, 0); sl.addWidget(self.b_freeze, 0, 1)
        v.addWidget(safe)

        # --- FEEL: biases/caps, never overrides ---------------------------
        feel = QtWidgets.QGroupBox("Feel (bias / cap)")
        fl = QtWidgets.QFormLayout(feel)
        self.s_bright = _slider(0, 100, int(m.master_brightness * 100))
        self.s_strobe = _slider(0, 100, int(m.strobe_cap * 100))
        self.s_warm = _slider(-100, 100, int(m.warm_cool_bias * 100))
        # Synth Sync (Josh, 2026-09-15): wash follows the synths/stabs (the mid
        # band) and strobes pause -- see engines/reactive.py's SYNTH_* block.
        self.cb_synth = QtWidgets.QCheckBox("Synth Sync (S) — wash follows the synths")
        self.cb_synth.setChecked(m.synth_sync)
        fl.addRow("Master brightness", self.s_bright)
        fl.addRow("Strobe cap", self.s_strobe)
        fl.addRow("Warm ← → Cool", self.s_warm)
        fl.addRow("", self.cb_synth)
        v.addWidget(feel)
        v.addStretch(1)

        # wiring
        self.s_bright.valueChanged.connect(lambda x: setattr(m, "master_brightness", x / 100))
        self.s_strobe.valueChanged.connect(lambda x: setattr(m, "strobe_cap", x / 100))
        self.s_warm.valueChanged.connect(lambda x: setattr(m, "warm_cool_bias", x / 100))
        self.cb_synth.toggled.connect(lambda on: setattr(m, "synth_sync", on))
        self.b_black.toggled.connect(self._set_blackout)
        self.b_freeze.toggled.connect(lambda on: setattr(m, "freeze", on))
        return w

    def _build_laser_show_tab(self) -> QtWidgets.QWidget:
        """Sophia's Thai (2026-10-08): the laser mode (follow the music /
        rotate / hold), the manual override grid (one button per look --
        pressing one holds it), the drop accent, and the light bar's Synth
        Sync. Everything here is a ManualParams field read by
        engines/laser_show.py or the Synth Sync path in engines/reactive.py
        (unchanged)."""
        m = self.app.manual
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        modes = QtWidgets.QGroupBox("Laser mode")
        ml = QtWidgets.QVBoxLayout(modes)
        self.rb_laser_follow = QtWidgets.QRadioButton(
            "Follow the music — break: Slow Rainbow · build: Tension ramp · armed: chop · drop: Fan 8 bars then Rainbow · peak: Ping-Pong")
        self.rb_laser_rotate = QtWidgets.QRadioButton("Rotate the static scenes every 16 bars, starting from the picked one")
        self.rb_laser_hold = QtWidgets.QRadioButton("Hold one look (manual override) — pick it below")
        self._laser_mode_buttons = {MODE_FOLLOW: self.rb_laser_follow, MODE_ROTATE: self.rb_laser_rotate,
                                    MODE_HOLD: self.rb_laser_hold}
        self._laser_mode_buttons.get(m.laser_mode, self.rb_laser_follow).setChecked(True)
        for rb in self._laser_mode_buttons.values():
            ml.addWidget(rb)
        self.cb_laser_accent = QtWidgets.QCheckBox("Drop accent in rotate / hold (chop the lasers at 8th notes for 2 bars when a drop lands)")
        self.cb_laser_accent.setChecked(m.laser_drop_accent)
        ml.addWidget(self.cb_laser_accent)
        self.l_laser_show = QtWidgets.QLabel("")
        self.l_laser_show.setWordWrap(True)
        ml.addWidget(self.l_laser_show)
        v.addWidget(modes)

        looks = QtWidgets.QGroupBox("Looks — press one to hold it (manual override)")
        gl = QtWidgets.QGridLayout(looks)
        gl.setSpacing(8)
        self.laser_look_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._laser_look_group = QtWidgets.QButtonGroup(w)
        self._laser_look_group.setExclusive(True)
        for i, name in enumerate(look_names()):
            b = _button(name, "look", checkable=True)  # #look / #look:checked in ui/theme.py
            self.laser_look_buttons[name] = b
            self._laser_look_group.addButton(b)
            gl.addWidget(b, i // 3, i % 3)
        if m.laser_scene in self.laser_look_buttons:
            self.laser_look_buttons[m.laser_scene].setChecked(True)
        v.addWidget(looks)

        bar = QtWidgets.QGroupBox("Light bar")
        bl = QtWidgets.QVBoxLayout(bar)
        self.cb_synth_bar = QtWidgets.QCheckBox("SynthSync (light bar)")
        self.cb_synth_bar.setChecked(m.synth_sync)
        bl.addWidget(self.cb_synth_bar)
        v.addWidget(bar)
        v.addStretch(1)

        for mode, rb in self._laser_mode_buttons.items():
            rb.toggled.connect(lambda on, mode=mode: on and setattr(m, "laser_mode", mode))
        for name, b in self.laser_look_buttons.items():
            b.clicked.connect(lambda _=False, name=name: self._hold_laser_look(name))
        self.cb_laser_accent.toggled.connect(lambda on: setattr(m, "laser_drop_accent", on))
        # Same flag as the Show tab's Synth Sync box (S); refresh() keeps both in step.
        self.cb_synth_bar.toggled.connect(lambda on: setattr(m, "synth_sync", on))
        return w

    def _hold_laser_look(self, name: str) -> None:
        """A look button: pick it and switch to the manual hold."""
        self.app.manual.laser_scene = name
        self.rb_laser_hold.setChecked(True)      # its toggled slot writes laser_mode

    def _install_shortcuts(self) -> None:
        def sc(key, fn):
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=fn)

        sc("B", self.b_black.toggle)
        sc("F", self.b_freeze.toggle)
        sc(QtCore.Qt.Key.Key_Right, self.app.phrase_engine.skip)
        sc("T", self._do_tap)
        sc("S", self.cb_synth.toggle)
        sc("L", self.cb_laser.toggle)
        sc("K", self.b_laser_cue.toggle)
        sc("J", self.b_laser_hit.click)
        sc("C", self.app.metronome.cue_build)
        sc("D", self.app.metronome.arm_drop)
        sc(QtCore.Qt.Key.Key_Space, self._strobe_burst)

    # ------------------------------------------------------------------ actions
    def open_rig_manager(self):
        from .rig_manager import RigManagerDialog
        self.rig_manager = RigManagerDialog(self)
        self.rig_manager.rig_chosen.connect(self._rig_chosen)
        self.rig_manager.show()
        return self.rig_manager

    def open_fixture_library(self):
        from .fixture_library import FixtureLibraryBrowser
        self.fixture_library = FixtureLibraryBrowser()
        self.fixture_library.setWindowTitle("Fixture library")
        # a fixture added or confirmed there shows up in Rig Config's type list
        self.fixture_library.library_changed.connect(self.rig_config.reload_definitions)
        self.fixture_library.show()
        return self.fixture_library

    def open_api_key(self):
        from .api_key import ApiKeyDialog
        dlg = ApiKeyDialog(self)
        dlg.show()
        return dlg

    def _rig_chosen(self, path: str) -> None:
        """Switch live when the app can (App.switch_rig, roadmap item 4); until then the
        choice is remembered (recent rigs) and applies at the next start."""
        switch = getattr(self.app, "switch_rig", None)
        if callable(switch):
            switch(path)
            self.statusBar().showMessage(f"Switched to {path}", 8000)
        else:
            self.statusBar().showMessage(f"Rig chosen: {path} — restart LiveReact with this rig to apply it.")

    def _set_blackout(self, on: bool) -> None:
        self.app.manual.blackout = on

    def _do_tap(self) -> None:
        now = self.app.clock.monotonic()
        bpm = self.tap.tap(now)
        if bpm is not None:
            self._tap_beat = self._tap_beat % 4 + 1
            self.app.input_queue.put(BeatMessage(now, self._tap_beat, bpm, is_master=True, player=0))

    def _strobe_burst(self) -> None:
        self.app.manual.strobe_burst = True
        QtCore.QTimer.singleShot(400, lambda: setattr(self.app.manual, "strobe_burst", False))

    # ------------------------------------------------------------------ refresh
    def refresh(self) -> None:
        snap = self.app.show_state.snapshot()
        self.override_panel.refresh()   # arm switch / laser cue changed by hotkeys or MIDI
        latest = self.app.frame_buffer.latest()
        if self.tabs.currentWidget() is self.venue_tab:
            self.venue_tab.plan.update_from_universe(latest)   # see the jogged head move
        else:
            self.rig_view.update_from_universe(latest)

        self.l_bpm.setText(f"{snap.bpm:.1f}")
        self.l_beat.setText(f"{snap.beat_in_bar} / {snap.bar}")
        self.l_deck.setText(str(snap.master_deck) if snap.master_deck else "—")
        self.l_scene.setText(self.app.phrase_engine.current_scene_name)
        self.l_bucket.setText(snap.bucket.upper())
        self.l_bucket.setStyleSheet(f"background:{bucket_color(snap.bucket)}; color:white;")
        self.pb_energy.setValue(int(snap.energy * 100))
        low, mid, high = band_levels(snap)
        self.pb_low.setValue(int(low * 100))
        self.pb_mid.setValue(int(mid * 100))
        self.pb_high.setValue(int(high * 100))

        clock = "free-run" if snap.free_running else "locked"
        trans = "  TRANSITION" if snap.in_transition else ""
        pdl = "fresh" if snap.pdl_fresh else "STALE"
        midi = "fresh" if snap.midi_fresh else "stale"
        if snap.phrase_source:
            nxt = f" → {snap.next_section_kind} in {snap.beats_to_next_section}" if snap.beats_to_next_section >= 0 else ""
            rb = f"\nrb: {snap.phrase_label} ({snap.section_kind}){nxt}"
        elif snap.deck_status_fresh:
            rb = f"\nrb: track {snap.track_id} beat {snap.track_beat} (no phrase table)"
        else:
            rb = ""
        self.l_health.setText(f"clock {clock}\nPDL {pdl} · MIDI {midi}{trans}{rb}")

        # connection dots: PDL/MIDI green only once real beat data is flowing (not
        # just a device seen on the wire); DMX green only once a real hardware
        # backend (not the null/log fallback) has successfully opened.
        _set_dot(self.dot_pdl, snap.pdl_fresh)
        _set_dot(self.dot_midi, snap.midi_fresh)
        _set_dot(self.dot_audio, snap.audio_fresh)
        writer = self.app.writer
        dmx_ok = writer.is_open and writer.backend.name != "null-log"
        _set_dot(self.dot_dmx, dmx_ok)

        if self.app.show_engine.silence.active:
            self.l_auto_blackout.setText("● AUTO-BLACKOUT — no music detected")
            self.l_auto_blackout.setStyleSheet(_DOT_BAD)
        else:
            self.l_auto_blackout.setText("")

        laser = self.app.laser_engine
        # A MIDI "laser_cue" control writes manual.laser_cue directly; keep
        # the button showing the real state without re-firing its toggled slot.
        if self.b_laser_cue.isChecked() != self.app.manual.laser_cue:
            self.b_laser_cue.blockSignals(True)
            self.b_laser_cue.setChecked(self.app.manual.laser_cue)
            self.b_laser_cue.blockSignals(False)
        mood = getattr(laser, "mood", None)  # engines/laser_feature.py and laser_trigger.py have one
        trigger = getattr(laser, "trigger", None)  # only engines/laser_trigger.py (2026-10-06)
        scene_name = getattr(laser, "scene_name", None)  # only engines/laser_show.py (2026-10-08)
        if scene_name is not None:
            # Show profile: the chip names the scene; the banner says what the
            # rotation / accent are doing. Keep the Laser Show tab's Synth
            # Sync box in step with the Show tab's (one flag, two boxes).
            if self.laser_show_tab is not None and self.cb_synth_bar.isChecked() != self.app.manual.synth_sync:
                self.cb_synth_bar.blockSignals(True)
                self.cb_synth_bar.setChecked(self.app.manual.synth_sync)
                self.cb_synth_bar.blockSignals(False)
            if self.cb_synth.isChecked() != self.app.manual.synth_sync:
                self.cb_synth.blockSignals(True)
                self.cb_synth.setChecked(self.app.manual.synth_sync)
                self.cb_synth.blockSignals(False)
            accent = bool(getattr(laser, "accent", False))
            mode = getattr(laser, "mode", "")
            self.l_laser.setText(f"SHOW · {scene_name.upper()}" + (" · ACCENT" if accent else ""))
            self.l_laser.setStyleSheet(f"background:{'#b03030' if accent else '#2a5db0'}; color:white;")
            if self.app.manual.blackout:
                text, style = "○ LASERS BLACKED OUT (B)", _DOT_BAD
            elif not self.app.manual.laser_enable:
                text, style = "○ LASERS DISARMED — press L (show profile arms at launch)", _DOT_BAD
            else:
                reason = getattr(laser, "reason", "")
                nxt = getattr(laser, "bars_to_next", None)
                if getattr(laser, "rotating", False):
                    tail = f"rotating · next in {int(nxt)} bars" if nxt is not None else "rotating on the clock (no beat grid)"
                    if reason:
                        tail = f"{reason} · {tail}"
                else:
                    tail = reason or mode
                text = f"● LASER SHOW [{mode}] — {scene_name}{' · DROP ACCENT' if accent else ''} · {tail}"
                style = _DOT_OK
            self.l_laser_flash.setText(text)
            self.l_laser_flash.setStyleSheet(style)
            if self.laser_show_tab is not None:
                self.l_laser_show.setText(text)
                # the grid highlights what is showing NOW (follow / rotate
                # move it around; in hold it is the picked look)
                b = self.laser_look_buttons.get(scene_name)
                if b is not None and not b.isChecked():
                    b.setChecked(True)
                rb = self._laser_mode_buttons.get(mode)
                if rb is not None and not rb.isChecked():
                    rb.blockSignals(True); rb.setChecked(True); rb.blockSignals(False)
            mood = None
        elif mood is None:
            self.l_laser.setText("ACCENT")
            self.l_laser.setStyleSheet(f"background:{MUTED}; color:white;")
        elif trigger is not None:
            lit = bool(laser.active)
            self.l_laser.setText(f"TRIGGER · {mood.upper()} · {laser.budget:.0%} of {int(DUTY_WINDOW_S)} s")
            self.l_laser.setStyleSheet(f"background:{'#b03030' if lit else MUTED}; color:white;")
            if not self.app.manual.laser_enable:
                self.l_laser_flash.setText("○ LASERS NOT ARMED — press L (trigger profile: 2-bar moments on the drop / PEAK blocks / a big wub; J = hit now, K = hold)")
                self.l_laser_flash.setStyleSheet(_DOT_BAD)
            elif mood == MOOD_OFF:
                self.l_laser_flash.setText("○ LASER OFF — waiting for real audio")
                self.l_laser_flash.setStyleSheet(_DOT_BAD)
            elif lit:
                self.l_laser_flash.setText(f"● LASER MOMENT — {trigger.upper()} · {laser_color_name(laser.color)}")
                self.l_laser_flash.setStyleSheet(_DOT_OK)
            else:
                self.l_laser_flash.setText("○ lasers dark, ready — next moment on the drop hit, a PEAK block, a big wub, J or K")
                self.l_laser_flash.setStyleSheet(f"color:{MUTED};")
            mood = None  # the feature-profile banners below do not apply
        else:
            self.l_laser.setText(f"FEATURE · {mood.upper()}")
            self.l_laser.setStyleSheet(f"background:{_MOOD_COLORS.get(mood, MUTED)}; color:white;")
        if scene_name is not None:
            pass  # the show-profile banner above is final
        elif mood is not None:
            # Feature profile banners: the lasers ARE the light here, so "not
            # armed" is the loudest thing the console can say.
            colors = f"{laser_color_name(laser.color_l)} / {laser_color_name(laser.color_r)}"
            if not self.app.manual.laser_enable:
                self.l_laser_flash.setText("○ LASERS NOT ARMED — press L (feature profile: the lasers are the light)")
                self.l_laser_flash.setStyleSheet(_DOT_BAD)
            elif mood == MOOD_OFF:
                self.l_laser_flash.setText("○ LASER OFF — waiting for real audio")
                self.l_laser_flash.setStyleSheet(_DOT_BAD)
            else:
                forced = " (K forcing the fan)" if laser.source == "manual" else ""
                self.l_laser_flash.setText(
                    f"● LASER {mood.upper()}{forced} — {colors} · sweep {int(laser.sweep * 100)}%")
                self.l_laser_flash.setStyleSheet(_DOT_OK)
        elif laser.manual_cued and not self.app.manual.laser_enable:
            self.l_laser_flash.setText("○ LASER CUED but not armed — press L to arm")
            self.l_laser_flash.setStyleSheet(_DOT_BAD)
        elif laser.manual_cued and laser.source == "manual":
            self.l_laser_flash.setText(f"● LASER CUED (K) — sweep follows mids+highs · {int(laser.sweep * 100)}%")
            self.l_laser_flash.setStyleSheet(_DOT_OK)
        elif laser.manual_cued:
            self.l_laser_flash.setText("○ LASER CUED — waiting for real audio")
            self.l_laser_flash.setStyleSheet(_DOT_BAD)
        elif laser.active:
            self.l_laser_flash.setText("● LASER SWEEP")
            self.l_laser_flash.setStyleSheet(_DOT_OK)
        elif laser.primed:
            self.l_laser_flash.setText("○ laser primed — next 8-bar block")
            self.l_laser_flash.setStyleSheet("")
        else:
            self.l_laser_flash.setText("")

        if self.app.metronome.build_cue_active:
            if self.app.metronome.build_cue_source == "auto":
                self.l_cue.setText("● BUILD (auto: kick back at half the drop's level) — C confirms it")
            else:
                self.l_cue.setText("● BUILD CUED — held until the drop lands (C again cancels)")
            self.l_cue.setStyleSheet(_DOT_OK)
        else:
            self.l_cue.setText("")
        if self.app.metronome.drop_armed:
            src = self.app.metronome.drop_arm_source
            rising = " · RISING" if snap.rising else ""
            if self.app.metronome.drop_cut_pending:
                self.l_arm.setText("● DROP CUT — lands on the next downbeat; D again cancels")
            elif src == "roll":
                self.l_arm.setText(f"● DROP ARMED (snare roll){rising} — the next kick pattern cuts it")
            elif src == "rise":
                self.l_arm.setText(f"● DROP ARMED (intensity rising){rising} — the next kick pattern cuts it")
            else:
                self.l_arm.setText(f"● DROP ARMED (D){rising} — strobe runs while the intensity rises; D again CUTS on the next downbeat")
            self.l_arm.setStyleSheet(_DOT_OK)
        else:
            self.l_arm.setText("")

        if self.app.reactive_engine.synth_sync_active:
            self.l_synth.setText("● SYNTH SYNC — wash follows the mids; strobes cut through")
            self.l_synth.setStyleSheet(_DOT_OK)
        elif self.app.manual.synth_sync:
            self.l_synth.setText("● SYNTH SYNC armed — waiting for real audio")
            self.l_synth.setStyleSheet(_DOT_BAD)
        else:
            self.l_synth.setText("")


def run_console(app, fps: int = 30) -> int:
    """Open the console and refresh it at ``fps`` (blocks until the window closes)."""
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_theme(qt)
    win = ConsoleWindow(app)
    win.show()
    timer = QtCore.QTimer()
    timer.timeout.connect(win.refresh)
    timer.start(int(1000 / fps))
    # SIGINT/SIGTERM (and tools/run_show.sh's `touch logs/STOP` + kill) only set
    # app.stop_event; Qt's loop never saw it, so the window had to be force-killed
    # (2026-10-08 demo run). Poll the event and quit the loop when it is set.
    stop_poll = QtCore.QTimer()
    stop_poll.timeout.connect(lambda: qt.quit() if app.stop_event.is_set() else None)
    stop_poll.start(250)
    return qt.exec()
