"""Roadmap item 41: a laser on the channel-test screen needs two keys -- the screen's own
"Enable laser output" box AND the console's arm switch (``manual.laser_enable``)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.core.frame_buffer import DmxFrameBuffer  # noqa: E402
from livereact.fixtures.definition import SCHEMA, load_definition  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


LASER = {"schema": SCHEMA, "id": "acme_laser", "manufacturer": "Acme", "model": "Laser", "category": "laser",
         "source": {"kind": "user", "confirmed": False},
         "modes": [{"name": "2ch", "channels": [{"offset": 0, "function": "mode"},
                                                {"offset": 1, "function": "laser_pattern"}]}]}
PAR = {"schema": SCHEMA, "id": "acme_par", "manufacturer": "Acme", "model": "Par", "category": "par",
       "source": {"kind": "user", "confirmed": False},
       "modes": [{"name": "1ch", "channels": [{"offset": 0, "function": "dimmer"}]}]}


class Manual:
    laser_enable = False


def _panel(raw, manual=None, **kw):
    from livereact.ui.channel_test import ChannelTestPanel
    fb = DmxFrameBuffer()
    armed = (lambda: manual.laser_enable) if manual is not None else None
    p = ChannelTestPanel(load_definition(raw), address=1, frame_buffer=fb, save=lambda d: "mem",
                         laser_armed=armed, **kw)
    p.sliders[0].setValue(100)
    return p, fb


def test_box_then_console_arm(qapp):
    m = Manual()
    p, fb = _panel(LASER, m)
    p.cb_laser_out.setChecked(True)
    p.sync_arm()
    assert fb.latest()[0] == 0                       # one key is not enough
    m.laser_enable = True
    p.sync_arm()
    assert fb.latest()[0] == 100


def test_console_arm_then_box(qapp):
    m = Manual()
    m.laser_enable = True
    p, fb = _panel(LASER, m)
    p.sync_arm()
    assert fb.latest()[0] == 0
    p.cb_laser_out.setChecked(True)
    assert fb.latest()[0] == 100


def test_disarming_in_the_console_zeroes_the_laser_channels(qapp):
    m = Manual()
    m.laser_enable = True
    p, fb = _panel(LASER, m)
    p.cb_laser_out.setChecked(True)
    assert fb.latest()[0] == 100
    m.laser_enable = False
    p.sync_arm()
    assert fb.latest()[0] == 0 and fb.latest()[1] == 0
    assert "console" in p.l_arm.text().lower() and "disarmed" in p.l_arm.text().lower()


def test_the_screen_polls_the_console_arm_switch(qapp):
    m = Manual()
    m.laser_enable = True
    p, fb = _panel(LASER, m)
    p.cb_laser_out.setChecked(True)
    assert p.arm_timer.isActive()
    m.laser_enable = False
    p.arm_timer.timeout.emit()
    assert fb.latest()[0] == 0


def test_no_console_means_no_laser_output(qapp):
    p, fb = _panel(LASER)
    p.cb_laser_out.setChecked(True)
    p.sync_arm()
    assert fb.latest()[0] == 0


def test_a_par_ignores_the_arm_switch(qapp):
    p, fb = _panel(PAR)
    assert fb.latest()[0] == 100
    assert not p.l_arm.isVisibleTo(p)


def test_console_passes_its_arm_switch_to_the_library_screens(qapp):
    from livereact.app import App, AppConfig
    from livereact.ui.console import ConsoleWindow
    app = App(AppConfig(demo=True, config_path=None))
    win = ConsoleWindow(app)
    browser = win.open_fixture_library()
    browser.table.selectRow(0)
    panel = browser.test_selected()
    app.manual.laser_enable = True
    assert panel.laser_armed() is True
    app.manual.laser_enable = False
    assert panel.laser_armed() is False
    wizard = browser.open_wizard()
    wp = wizard.open_channel_test(load_definition(LASER))
    assert wp.laser_armed() is False
    app.manual.laser_enable = True
    assert wp.laser_armed() is True
