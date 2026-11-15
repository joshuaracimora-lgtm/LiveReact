"""Small new-user hints (UX_BACKLOG UX 0b and UX 10, 2026-11-15)."""
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


def test_demo_mode_says_the_laser_arm_is_simulated_and_the_switch_still_reads_armed(qapp):
    """UX 0b: in demo mode the big red DISARM LASERS / ARMED looked like a real laser was live."""
    from livereact.ui.override_panel import STATUS_ARMED, OverridePanel
    app = App(AppConfig(demo=True, config_path=None))
    app.manual.laser_enable = True
    p = OverridePanel(app)
    assert p.l_laser_status.text() == STATUS_ARMED        # the switch never lies
    assert not p.l_laser_sim.isHidden()
    assert "simulated" in p.l_laser_sim.text() and "no laser" in p.l_laser_sim.text()


def test_outside_demo_mode_there_is_no_simulated_caption(qapp):
    from livereact.ui.override_panel import OverridePanel
    app = App(AppConfig(demo=True, config_path=None))
    app.config.demo = False
    p = OverridePanel(app)
    assert p.l_laser_sim.isHidden()

