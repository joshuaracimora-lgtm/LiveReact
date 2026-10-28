"""Roadmap item 20: the rig manager dialog (ui/rig_manager.py) and its console menu entry."""
from __future__ import annotations

import json
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


def _dialog(open_file=None):
    from livereact.ui.rig_manager import RigManagerDialog
    d = RigManagerDialog(open_file=open_file)
    got: list[str] = []
    d.rig_chosen.connect(got.append)
    return d, got


def test_new_rig_is_an_empty_loadable_rig_in_the_user_folder(qapp, home):
    from livereact import userdata
    from livereact.fixtures.rig import load_rig
    d, got = _dialog()
    d.b_new.click()
    d.b_new.click()
    assert len(got) == 2 and got[0] != got[1]
    for p in got:
        assert os.path.dirname(p) == str(userdata.paths().rigs)
        assert load_rig(p).fixtures == []


def test_demo_rig_is_the_packaged_file_and_read_only(qapp):
    from livereact.ui.rig_manager import demo_rig_path, is_read_only
    d, got = _dialog()
    d.b_demo.click()
    assert got == [str(demo_rig_path())]
    assert is_read_only(got[0])
    assert "read-only" in d.l_msg.text()


def test_save_demo_as_copies_it_to_the_user_folder(qapp):
    from livereact import userdata
    from livereact.ui.rig_manager import demo_rig_path, is_read_only
    d, got = _dialog()
    d.b_demo_copy.click()
    p = got[0]
    assert os.path.dirname(p) == str(userdata.paths().rigs) and not is_read_only(p)
    assert json.loads(open(p, encoding="utf-8").read()) == json.loads(demo_rig_path().read_text(encoding="utf-8"))


def test_open_uses_the_picker_in_the_rigs_folder(qapp, tmp_path):
    from livereact import userdata
    from livereact.ui.rig_manager import demo_rig_path
    seen = []
    target = tmp_path / "mine.json"
    target.write_text(demo_rig_path().read_text(encoding="utf-8"), encoding="utf-8")
    d, got = _dialog(open_file=lambda start: seen.append(start) or str(target))
    d.b_open.click()
    assert seen == [str(userdata.paths().rigs)]
    assert got == [str(target)]


def test_cancelled_picker_chooses_nothing(qapp):
    d, got = _dialog(open_file=lambda start: "")
    d.b_open.click()
    assert got == []


def test_a_bad_rig_is_refused_and_not_remembered(qapp, tmp_path):
    from livereact.settings import Settings
    bad = tmp_path / "bad.json"
    bad.write_text('{"slots": [{"fixture_type": "nope", "dmx_address": 1}]}', encoding="utf-8")
    d, got = _dialog(open_file=lambda start: str(bad))
    d.b_open.click()
    assert got == [] and "Cannot use" in d.l_msg.text()
    assert Settings.load().recent_rigs == []


def test_recent_rigs_are_remembered_and_reusable(qapp, tmp_path):
    from livereact.settings import Settings
    d, got = _dialog()
    d.b_new.click()
    d.b_demo.click()
    assert Settings.load().recent_rigs == [got[1], got[0]]
    d2, got2 = _dialog()
    assert d2.recent.count() == 2
    d2.recent.setCurrentRow(1)
    d2.b_use_recent.click()
    assert got2 == [got[0]]


def test_missing_recent_files_are_hidden(qapp):
    from livereact.settings import Settings
    s = Settings.load()
    s.add_recent_rig("C:/nowhere/gone.json")
    s.save()
    d, _ = _dialog()
    assert d.recent.count() == 0 and not d.b_use_recent.isEnabled()


def test_console_rig_menu_opens_the_manager_and_says_restart(qapp):
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    from livereact.ui.rig_manager import RigManagerDialog
    app = App(AppConfig(demo=True, config_path=None))
    win = ConsoleWindow(app)
    titles = [a.text().replace("&", "") for a in win.menuBar().actions()]
    assert "Rig" in titles
    dlg = win.open_rig_manager()
    assert isinstance(dlg, RigManagerDialog)
    dlg.b_demo.click()
    assert "restart" in win.statusBar().currentMessage().lower()
    assert app.rig is not None   # nothing switched live (App.switch_rig is item 4)


def test_console_switches_live_when_the_app_can(qapp):
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    app = App(AppConfig(demo=True, config_path=None))
    switched = []
    app.switch_rig = switched.append
    win = ConsoleWindow(app)
    win.open_rig_manager().b_demo.click()
    assert len(switched) == 1
