"""Roadmap item 53: the console remembers its window (size, position, selected tab)
through settings.py — but only when a Settings is passed in, so every direct
``ConsoleWindow(app)`` (all older tests, the UI gate checks) stays exactly as before."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.app import App, AppConfig  # noqa: E402
from livereact.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _app() -> App:
    return App(AppConfig(demo=True, config_path=None))


def _default_size() -> tuple[int, int]:
    """1280 x 800, clamped to the screen (console._fits_the_screen): the offscreen test screen is smaller."""
    from PySide6 import QtCore
    from livereact.ui.console import _fits_the_screen
    size = _fits_the_screen(QtCore.QSize(1280, 800))
    return size.width(), size.height()


def _window(settings=None):
    from livereact.ui.console import ConsoleWindow
    return ConsoleWindow(_app(), settings=settings)


def test_close_saves_geometry_and_tab(qapp, tmp_path):
    s = Settings(path=tmp_path / "settings.json")
    win = _window(settings=s)
    win.move(12, 34)
    win.resize(640, 480)
    for i in range(win.tabs.count()):
        if win.tabs.tabText(i) == "Rig Config":
            win.tabs.setCurrentIndex(i)
    g = win.geometry()
    win.close()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["console_window"] == {"x": g.x(), "y": g.y(), "w": g.width(),
                                       "h": g.height(), "tab": "Rig Config"}


def test_saved_window_is_restored_at_the_next_start(qapp, tmp_path):
    s = Settings(path=tmp_path / "settings.json")
    s.extra["console_window"] = {"x": 20, "y": 30, "w": 600, "h": 500, "tab": "Venue"}
    win = _window(settings=s)
    g = win.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (20, 30, 600, 500)
    assert win.tabs.tabText(win.tabs.currentIndex()) == "Venue"


def test_a_position_off_every_screen_is_clamped_back(qapp, tmp_path):
    from PySide6 import QtGui
    s = Settings(path=tmp_path / "settings.json")
    s.extra["console_window"] = {"x": 5000, "y": 5000, "w": 640, "h": 480, "tab": "Show"}
    win = _window(settings=s)
    g = win.geometry()
    assert any(sc.availableGeometry().intersects(g) for sc in QtGui.QGuiApplication.screens())


def test_an_unknown_tab_name_keeps_the_default_tab(qapp, tmp_path):
    s = Settings(path=tmp_path / "settings.json")
    s.extra["console_window"] = {"x": 20, "y": 30, "w": 600, "h": 500, "tab": "No Such Tab"}
    win = _window(settings=s)
    assert win.tabs.currentIndex() == 0


def test_corrupt_window_entry_reads_as_default(qapp, tmp_path):
    s = Settings(path=tmp_path / "settings.json")
    s.extra["console_window"] = {"x": "wide", "tab": 3}
    win = _window(settings=s)
    assert (win.width(), win.height()) == _default_size()


def test_without_settings_nothing_is_read_or_written(qapp, tmp_path, home):
    win = _window()
    assert (win.width(), win.height()) == _default_size()
    win.close()
    assert not (home / "settings.json").exists()
    assert not list(home.rglob("settings.json")) if home.exists() else True


def test_make_console_uses_the_user_settings_file(qapp):
    from livereact import userdata
    from livereact.ui.console import make_console
    win = make_console(_app())
    assert win.settings is not None
    assert win.settings.path == userdata.paths().settings
    win.close()
    assert json.loads(win.settings.path.read_text(encoding="utf-8"))["console_window"]["w"] == win.width()
