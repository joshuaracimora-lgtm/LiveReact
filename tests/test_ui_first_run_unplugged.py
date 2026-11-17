"""2026-11-17 (UX_BACKLOG Open 11): re-opening first-time setup with the mixer or the DJ-network adapter unplugged
silently dropped the saved choice.

Help -> Run first-time setup again… lists only the audio inputs and network adapters present NOW. A saved "XDJ-XZ Audio" or
"USB Ethernet" that is unplugged at that moment was not in the list, so the box showed Auto-detect and Finish wrote
Auto-detect over the saved choice: the next launch at the show no longer picked the mixer.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _saved_setup():
    from livereact.settings import Settings
    s = Settings.load()
    s.extra["setup"] = {"output": "enttec", "audio_device": "XDJ-XZ Audio", "pdl_interface": "USB Ethernet",
                        "start_with": "demo_rig"}
    s.onboarding_done = True
    s.save()
    return Settings.load()


def test_reopening_setup_with_the_devices_unplugged_keeps_the_saved_choices(qapp):
    from livereact.settings import Settings
    from livereact.ui.first_run import FirstRunDialog
    d = FirstRunDialog(settings=_saved_setup(), audio_provider=lambda: ["Built-in Microphone"],
                       interface_provider=lambda: ["Wi-Fi"])
    try:
        assert d.cmb_audio.currentData() == "XDJ-XZ Audio"
        assert "not connected" in d.cmb_audio.currentText()      # the user sees why it is not in the live list
        assert d.cmb_interface.currentData() == "USB Ethernet"
        assert "not connected" in d.cmb_interface.currentText()
        for _ in range(4):
            d.b_next.click()                                     # Next, Next, Next, Finish: nothing changed
        setup = Settings.load().extra["setup"]
        assert setup["audio_device"] == "XDJ-XZ Audio"
        assert setup["pdl_interface"] == "USB Ethernet"
    finally:
        d.close()
        d.deleteLater()


def test_a_plugged_in_saved_device_is_listed_once_without_a_note(qapp):
    from livereact.ui.first_run import FirstRunDialog
    d = FirstRunDialog(settings=_saved_setup(), audio_provider=lambda: ["XDJ-XZ Audio", "Mic"],
                       interface_provider=lambda: ["USB Ethernet", "Wi-Fi"])
    try:
        assert [d.cmb_audio.itemData(i) for i in range(d.cmb_audio.count())] == [None, "XDJ-XZ Audio", "Mic"]
        assert d.cmb_audio.currentText() == "XDJ-XZ Audio"
        assert d.cmb_interface.currentText() == "USB Ethernet"
    finally:
        d.close()
        d.deleteLater()
