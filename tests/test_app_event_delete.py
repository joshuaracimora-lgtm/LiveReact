"""Roadmap item 51: delete a saved event (presets.delete_event + the panel's button)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.app import App, AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _panel(confirm=None):
    from livereact.ui.override_panel import OverridePanel
    app = App(AppConfig(demo=True, config_path=None))
    app.manual.laser_enable = False
    return app, OverridePanel(app, confirm=confirm)


# ------------------------------------------------------------- presets module

def test_delete_event_removes_the_file(qapp):
    from livereact import presets
    presets.save_event(presets.PresetEvent("Rooftop set"))
    presets.save_event(presets.PresetEvent("Warehouse"))
    assert presets.delete_event("Rooftop set") is True
    assert presets.list_events() == ["Warehouse"]


def test_delete_event_of_an_unknown_name_is_false(qapp):
    from livereact import presets
    assert presets.delete_event("never saved") is False
    assert presets.delete_event("") is False   # a blank name deletes nothing


# ------------------------------------------------------------------ the panel

def test_delete_button_asks_then_deletes_and_reloads(qapp):
    from livereact import presets
    asked: list[str] = []
    app, p = _panel(confirm=lambda text: asked.append(text) or True)
    p.e_event_name.setText("Rooftop set")
    p.b_save_event.click()
    p.e_event_name.setText("Warehouse")
    p.b_save_event.click()
    p.cmb_events.setCurrentIndex(p.cmb_events.findText("Rooftop set"))
    p.b_delete_event.click()
    assert len(asked) == 1 and "Rooftop set" in asked[0]
    assert presets.list_events() == ["Warehouse"]
    assert [p.cmb_events.itemText(i) for i in range(p.cmb_events.count())] == ["Warehouse"]
    assert "Rooftop set" in p.l_preset_msg.text()


def test_declined_delete_changes_nothing(qapp):
    from livereact import presets
    app, p = _panel(confirm=lambda text: False)
    p.e_event_name.setText("Keeper")
    p.b_save_event.click()
    p.b_delete_event.click()
    assert presets.list_events() == ["Keeper"]
    assert p.cmb_events.count() == 1


def test_delete_button_follows_whether_events_exist(qapp):
    app, p = _panel(confirm=lambda text: True)
    assert not p.b_delete_event.isEnabled()      # nothing saved yet
    p.e_event_name.setText("Only one")
    p.b_save_event.click()
    assert p.b_delete_event.isEnabled()
    p.b_delete_event.click()
    assert p.cmb_events.count() == 0
    assert not p.b_delete_event.isEnabled() and not p.b_load_event.isEnabled()
