"""First-run setup (roadmap item 33): four short pages, then never again.

1. Output: Enttec USB DMX, Art-Net, sACN, or demo (no hardware).
2. Audio input: the device that hears the mix (the DJ mixer's USB audio, or a microphone).
3. DJ Link network interface: the network card the CDJs / XDJ are on (or none).
4. Start with the demo rig, or add my own fixtures.

The choices are saved in settings.py (``extra["setup"]``) with ``onboarding_done``; wiring them
into ``AppConfig`` is Josh's (roadmap item 3). Device lists come from injectable providers so
the tests never touch real hardware.
"""

from __future__ import annotations

import socket

from PySide6 import QtCore, QtWidgets

from ..settings import Settings

OUTPUTS = (("enttec", "Enttec USB DMX interface"), ("artnet", "Art-Net (network)"),
           ("sacn", "sACN / E1.31 (network)"), ("demo", "Demo: no lights connected"))
START_DEMO, START_ADD = "demo_rig", "add_fixtures"
SETUP_KEY = "setup"


def audio_devices() -> list[str]:
    """Input-capable audio devices (sounddevice), [] when it is not installed or fails."""
    try:
        import sounddevice as sd  # noqa: PLC0415 - hardware extra
        return [d["name"] for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
    except Exception:  # noqa: BLE001 - no audio stack: the user can still choose "auto"
        return []


def network_interfaces() -> list[str]:
    try:
        return [name for _i, name in socket.if_nameindex()]
    except (OSError, AttributeError):
        return []


def needs_first_run(settings: Settings) -> bool:
    return not settings.onboarding_done


class FirstRunDialog(QtWidgets.QDialog):
    completed = QtCore.Signal(dict)

    def __init__(self, parent: QtWidgets.QWidget | None = None, settings: Settings | None = None,
                 audio_provider=audio_devices, interface_provider=network_interfaces) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to LiveReact")
        self.settings = settings if settings is not None else Settings.load()
        prev = self.settings.extra.get(SETUP_KEY) or {}
        v = QtWidgets.QVBoxLayout(self)
        self.stack = QtWidgets.QStackedWidget()
        v.addWidget(self.stack, 1)

        out = QtWidgets.QWidget()
        ol = QtWidgets.QVBoxLayout(out)
        ol.addWidget(QtWidgets.QLabel("How are your lights connected?"))
        self.output_buttons: dict[str, QtWidgets.QRadioButton] = {}
        for key, text in OUTPUTS:
            rb = QtWidgets.QRadioButton(text)
            self.output_buttons[key] = rb
            ol.addWidget(rb)
        self.output_buttons.get(prev.get("output"), self.output_buttons["demo"]).setChecked(True)
        ol.addStretch(1)
        self.stack.addWidget(out)

        aud = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(aud)
        al.addWidget(QtWidgets.QLabel("Which audio input hears the mix? (the mixer's USB audio is best)"))
        self.cmb_audio = QtWidgets.QComboBox()
        self.cmb_audio.addItem("Auto-detect", None)
        for name in audio_provider():
            self.cmb_audio.addItem(name, name)
        self._select(self.cmb_audio, prev.get("audio_device"))
        al.addWidget(self.cmb_audio)
        al.addStretch(1)
        self.stack.addWidget(aud)

        net = QtWidgets.QWidget()
        nl = QtWidgets.QVBoxLayout(net)
        nl.addWidget(QtWidgets.QLabel("Which network are your DJ players (Pro DJ Link) on?"))
        self.cmb_interface = QtWidgets.QComboBox()
        self.cmb_interface.addItem("Auto-detect", None)
        self.cmb_interface.addItem("No DJ Link players", "none")
        for name in interface_provider():
            self.cmb_interface.addItem(name, name)
        self._select(self.cmb_interface, prev.get("pdl_interface"))
        nl.addWidget(self.cmb_interface)
        nl.addStretch(1)
        self.stack.addWidget(net)

        rig = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(rig)
        rl.addWidget(QtWidgets.QLabel("Last step: which lights?"))
        self.rb_demo = QtWidgets.QRadioButton("Start with the demo rig (you can add your fixtures later)")
        self.rb_add = QtWidgets.QRadioButton("Add my own fixtures now")
        (self.rb_add if prev.get("start_with") == START_ADD else self.rb_demo).setChecked(True)
        rl.addWidget(self.rb_demo)
        rl.addWidget(self.rb_add)
        rl.addStretch(1)
        self.stack.addWidget(rig)

        nav = QtWidgets.QHBoxLayout()
        self.b_back = QtWidgets.QPushButton("Back")
        self.b_next = QtWidgets.QPushButton("Next")
        self.l_step = QtWidgets.QLabel("")
        nav.addWidget(self.b_back)
        nav.addWidget(self.l_step)
        nav.addStretch(1)
        nav.addWidget(self.b_next)
        v.addLayout(nav)
        self.b_back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.b_next.clicked.connect(self.next)
        self.go(0)

    @staticmethod
    def _select(combo: QtWidgets.QComboBox, value) -> None:
        i = combo.findData(value) if value is not None else -1
        if i >= 0:
            combo.setCurrentIndex(i)

    def go(self, page: int) -> None:
        page = max(0, min(self.stack.count() - 1, page))
        self.stack.setCurrentIndex(page)
        self.b_back.setEnabled(page > 0)
        last = page == self.stack.count() - 1
        self.b_next.setText("Finish" if last else "Next")
        self.l_step.setText(f"Step {page + 1} of {self.stack.count()}")

    def choices(self) -> dict:
        output = next(k for k, rb in self.output_buttons.items() if rb.isChecked())
        return {"output": output, "audio_device": self.cmb_audio.currentData(),
                "pdl_interface": self.cmb_interface.currentData(),
                "start_with": START_ADD if self.rb_add.isChecked() else START_DEMO}

    def next(self) -> None:
        if self.stack.currentIndex() < self.stack.count() - 1:
            self.go(self.stack.currentIndex() + 1)
            return
        self.finish()

    def finish(self) -> dict:
        c = self.choices()
        self.settings.extra[SETUP_KEY] = c
        self.settings.onboarding_done = True
        self.settings.save()
        self.completed.emit(c)
        self.accept()
        return c
