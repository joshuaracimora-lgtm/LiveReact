"""Resilience 2026-11-15: the user settings file cannot be written (disk full, a read-only or
synced folder, a Windows file lock from antivirus/OneDrive on settings.json).

Before: ``Settings.save()`` raised inside the Rig manager, the recent-rig menu and first-run
setup, so picking a rig mid-set did nothing at all (the switch came after the save) and
first-run setup never closed. Now the action still happens, the user is told in words that it
will not be remembered, and the health banner names it in a hardware session.

The failure is real, not mocked: the settings path is made a directory, so both reading and the
atomic replace fail in the OS.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    from livereact import settings as settings_mod
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(settings_mod, "_last_save_error", None, raising=False)   # restored after each test
    return tmp_path / "home"


def _block_settings_file():
    """Make settings.json impossible to write: a directory sits where the file goes."""
    from livereact import userdata
    p = userdata.paths().settings
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_save_or_error_returns_a_plain_sentence_and_leaves_no_temp_file():
    from livereact import settings as settings_mod
    from livereact.settings import Settings
    p = _block_settings_file()
    s = Settings.load()               # unreadable reads as defaults
    assert s.recent_rigs == []
    s.add_recent_rig("x.json")
    msg = s.save_or_error()
    assert msg is not None and "settings" in msg.lower() and "remember" in msg.lower()
    assert not p.with_name(p.name + ".tmp").exists()
    assert settings_mod.last_save_error() == msg
    with pytest.raises(OSError):      # plain save() still raises, as before
        s.save()


def test_a_good_save_clears_the_error(tmp_path):
    from livereact import settings as settings_mod
    from livereact.settings import Settings
    blocked = _block_settings_file()
    assert Settings.load().save_or_error() is not None
    s = Settings.load(tmp_path / "ok" / "settings.json")
    assert s.save_or_error() is None
    assert settings_mod.last_save_error() is None
    assert blocked.is_dir()


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


_OPEN: list = []


@pytest.fixture
def cleanup(qapp):
    yield
    from PySide6 import QtCore
    while _OPEN:
        d = _OPEN.pop()
        d.close()
        d.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def test_rig_manager_still_switches_the_rig_when_settings_cannot_be_saved(qapp, cleanup):
    from livereact.ui.rig_manager import RigManagerDialog, demo_rig_path
    _block_settings_file()
    d = RigManagerDialog()
    _OPEN.append(d)
    got: list[str] = []
    d.rig_chosen.connect(got.append)
    d.choose(demo_rig_path())
    assert got == [str(demo_rig_path())]
    assert "not be remembered" in d.l_msg.text()


def test_recent_rig_menu_still_switches_when_settings_cannot_be_saved(qapp):
    from livereact.ui.console import ConsoleWindow
    from livereact.ui.rig_manager import demo_rig_path
    _block_settings_file()
    chosen: list[str] = []
    shown: list[str] = []
    fake = SimpleNamespace(_rig_chosen=chosen.append,
                           statusBar=lambda: SimpleNamespace(showMessage=shown.append))
    ConsoleWindow._use_recent_rig(fake, str(demo_rig_path()))
    assert chosen == [str(demo_rig_path())]
    assert shown and "not be remembered" in shown[-1]
    ConsoleWindow._use_saved_rig(fake, str(demo_rig_path()))
    assert chosen == [str(demo_rig_path())] * 2


def test_first_run_setup_still_finishes_when_settings_cannot_be_saved(qapp, cleanup):
    from livereact.ui.first_run import FirstRunDialog
    _block_settings_file()
    d = FirstRunDialog(audio_provider=lambda: [], interface_provider=lambda: [])
    _OPEN.append(d)
    got = []
    d.completed.connect(got.append)
    d.finish()
    assert len(got) == 1 and got[0]["output"] == "demo"
    assert d.result() == d.DialogCode.Accepted


def test_health_banner_names_unsaved_settings_in_a_hardware_session():
    from livereact.settings import Settings
    from livereact.ui.health import health_warnings
    app = SimpleNamespace(config=SimpleNamespace(demo=False), writer=SimpleNamespace(is_open=True),
                          failed_inputs={}, started_at=0.0, sources=[])
    snap = SimpleNamespace(pdl_fresh=True, audio_fresh=True)
    assert health_warnings(app, snap, 60.0) == []
    _block_settings_file()
    Settings.load().save_or_error()
    w = health_warnings(app, snap, 60.0)
    assert len(w) == 1 and w[0].startswith("SETTINGS NOT SAVED") and "show keeps running" in w[0]
