"""Roadmap item 33: first-run setup and About (offscreen; device lists are faked)."""
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


_OPEN: list = []


@pytest.fixture(autouse=True)
def _delete_dialogs(qapp):
    # a dialog still alive when Python exits crashes the interpreter (0xC0000409) after every test passed
    yield
    from PySide6 import QtCore
    while _OPEN:
        d = _OPEN.pop()
        d.close()
        d.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def _dialog(settings=None):
    from livereact.ui.first_run import FirstRunDialog
    d = FirstRunDialog(settings=settings, audio_provider=lambda: ["XDJ-XZ Audio", "Mic"],
                       interface_provider=lambda: ["Ethernet", "Wi-Fi"])
    _OPEN.append(d)
    return d


def test_needs_first_run_until_done(qapp):
    from livereact.settings import Settings
    from livereact.ui.first_run import needs_first_run
    s = Settings.load()
    assert needs_first_run(s)
    s.onboarding_done = True
    assert not needs_first_run(s)


def test_four_steps_with_defaults(qapp):
    d = _dialog()
    assert d.stack.count() == 4 and d.l_step.text() == "Step 1 of 4" and not d.b_back.isEnabled()
    assert d.choices() == {"output": "demo", "audio_device": None, "pdl_interface": None, "start_with": "demo_rig"}
    assert [d.cmb_audio.itemData(i) for i in range(d.cmb_audio.count())] == [None, "XDJ-XZ Audio", "Mic"]
    assert [d.cmb_interface.itemData(i) for i in range(d.cmb_interface.count())] == [None, "none", "Ethernet", "Wi-Fi"]


def test_walk_through_and_finish_saves_settings(qapp):
    from livereact.settings import Settings
    d = _dialog()
    got = []
    d.completed.connect(got.append)
    d.output_buttons["enttec"].setChecked(True)
    d.b_next.click()
    d.cmb_audio.setCurrentIndex(1)
    d.b_next.click()
    d.cmb_interface.setCurrentIndex(2)
    d.b_next.click()
    assert d.b_next.text() == "Finish"
    d.rb_add.setChecked(True)
    d.b_next.click()
    want = {"output": "enttec", "audio_device": "XDJ-XZ Audio", "pdl_interface": "Ethernet", "start_with": "add_fixtures"}
    assert got == [want]
    s = Settings.load()
    assert s.onboarding_done is True and s.extra["setup"] == want


def test_reopening_shows_the_saved_choices(qapp):
    from livereact.settings import Settings
    d = _dialog()
    d.output_buttons["artnet"].setChecked(True)
    d.cmb_interface.setCurrentIndex(1)
    d.finish()
    d2 = _dialog(Settings.load())
    assert d2.choices()["output"] == "artnet" and d2.choices()["pdl_interface"] == "none"


def test_back_navigation(qapp):
    d = _dialog()
    d.b_next.click()
    d.b_next.click()
    d.b_back.click()
    assert d.stack.currentIndex() == 1


def test_real_providers_do_not_crash():
    from livereact.ui.first_run import audio_devices, network_interfaces
    assert isinstance(audio_devices(), list) and isinstance(network_interfaces(), list)


# --- About ---

def test_about_lists_version_and_notices(qapp):
    from livereact.ui.about import AboutDialog, about_text, notices
    names = {n.name for n in notices()}
    assert {"Qt / PySide6", "Anthropic Python SDK", "python-prodj-link", "Open Fixture Library"} <= names
    qt = next(n for n in notices() if n.name == "Qt / PySide6")
    assert "LGPL" in qt.licence or "GPL" in qt.licence
    text = about_text()
    assert text.startswith("LiveReact ") and "Open Fixture Library" in text
    assert AboutDialog().text.toPlainText() == text


def test_about_credits_imported_ofl_fixtures(qapp):
    import json
    from pathlib import Path
    from livereact.fixtures import library
    from livereact.fixtures.ofl import ofl_to_definition
    from livereact.ui.about import about_text
    data = Path(__file__).resolve().parent / "data" / "ofl" / "test-par-rgbw.json"
    d = ofl_to_definition(json.loads(data.read_text(encoding="utf-8")), "test-maker/test-par-rgbw")
    library.save_user_definition(d)
    assert "fixture test-maker/test-par-rgbw, by LiveReact tests" in about_text()
