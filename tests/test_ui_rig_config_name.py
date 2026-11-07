"""UX_BACKLOG UX 7 (2026-11-07): Rig Config had no name field, so a rig saved under a new file name still
announced the old rig's name ("Now playing rig: LiveReact default rig") and the fixture-delete box listed that
name for it. The tab now has a Rig name field that is written into the file and reaches the running show."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from livereact.app import App, AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _app() -> App:
    return App(AppConfig(demo=True, config_path=None))


def _save_to(monkeypatch, path):
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(path), "JSON (*.json)")))


def test_rig_config_shows_the_running_rigs_name_in_an_editable_field(qapp):
    from livereact.ui.console import ConsoleWindow
    app = _app()
    win = ConsoleWindow(app)
    assert win.rig_config.name_edit.text() == app.rig.name
    assert not win.rig_config.name_edit.isReadOnly()


def test_a_renamed_rig_saved_and_used_now_plays_under_its_new_name(qapp, tmp_path, monkeypatch):
    from livereact.ui.console import ConsoleWindow
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    app = _app()
    win = ConsoleWindow(app)
    cfg = win.rig_config
    old = app.rig.name
    cfg.name_edit.setText("  Basement rig  ")
    out = tmp_path / "basement.json"
    _save_to(monkeypatch, out)
    cfg._save(apply=True)
    assert json.loads(out.read_text(encoding="utf-8"))["name"] == "Basement rig"
    assert app.rig.name == "Basement rig" != old                 # the RUNNING show's rig
    assert "Basement rig" in win.statusBar().currentMessage()
    assert win.rig_config.name_edit.text() == "Basement rig"     # the rebuilt tab shows it too


def test_a_blank_rig_name_keeps_the_old_name(qapp, tmp_path, monkeypatch):
    from livereact.ui.console import ConsoleWindow
    app = _app()
    win = ConsoleWindow(app)
    cfg = win.rig_config
    cfg.name_edit.setText("   ")
    out = tmp_path / "unnamed.json"
    _save_to(monkeypatch, out)
    cfg._save()
    assert json.loads(out.read_text(encoding="utf-8"))["name"] == app.rig.name
