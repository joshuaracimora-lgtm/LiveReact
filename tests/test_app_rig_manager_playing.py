"""The Rig manager shows the rig that is playing now (walk-through `rig_switch`, UX 2026-11-17).

On a fresh install the console's Rig manager opened on an empty "Recent rigs" box: the user could not
see which rig the lights were playing. The rig in use is now the list's first row, marked "playing now";
the first-step hint (Demo rig / New rig) stays until the user has rigs of their own.
"""
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


def _rig_file(folder, name="Club"):
    p = folder / f"{name}.json"
    p.write_text(json.dumps({"name": name, "laser_profile": "trigger", "slots": [
        {"slot_id": "bar", "fixture_type": "ok103_wash_bar", "dmx_address": 101, "label": "Bar",
         "role": "wash", "side": "left"}]}), encoding="utf-8")
    return p


def _console(qapp):
    from livereact.app import App, AppConfig
    from livereact.clock import FakeClock
    from livereact.ui.console import ConsoleWindow
    app = App(AppConfig(demo=True, config_path=None), clock=FakeClock())
    win = ConsoleWindow(app)
    win.show()
    return app, win


def test_fresh_install_console_rig_manager_lists_the_demo_rig_as_playing(qapp):
    from PySide6 import QtCore
    from livereact.ui.rig_manager import demo_rig_path
    app, win = _console(qapp)
    d = win.open_rig_manager()
    qapp.processEvents()
    assert d.recent.count() == 1
    item = d.recent.item(0)
    assert "playing now" in item.text() and "read-only" in item.text()
    assert item.data(QtCore.Qt.ItemDataRole.UserRole) == str(demo_rig_path())
    # the first-step hint still shows: the user has no rigs of their own yet
    assert d.l_empty.isVisible()
    assert "Demo rig" in d.l_empty.text() and "New rig" in d.l_empty.text()
    assert d.focusWidget() is d.b_demo
    d.close(); win.close()


def test_playing_row_follows_a_switch_and_is_not_listed_twice(qapp, tmp_path):
    app, win = _console(qapp)
    d = win.open_rig_manager()
    rig = _rig_file(tmp_path)
    d.choose(str(rig))
    d.close()
    d = win.open_rig_manager()
    qapp.processEvents()
    texts = [d.recent.item(i).text() for i in range(d.recent.count())]
    assert texts[0].startswith("Club") and "playing now" in texts[0]
    assert sum(t.startswith("Club") for t in texts) == 1
    assert not d.l_empty.isVisible()
    d.close(); win.close()


def test_dialog_without_a_playing_rig_is_unchanged(qapp):
    from livereact.ui.rig_manager import RigManagerDialog
    d = RigManagerDialog()
    d.show()
    qapp.processEvents()
    assert d.recent.count() == 0 and d.l_empty.isVisible()
    d.close()


def test_playing_demo_row_cannot_be_renamed_or_deleted(qapp):
    app, win = _console(qapp)
    d = win.open_rig_manager()
    d.recent.setCurrentRow(0)
    assert not d.b_rename.isEnabled() and not d.b_delete.isEnabled()
    assert "read-only" in d.recent.item(0).text()
    d.close(); win.close()
