"""Headless smoke test for the manual fixture-test panel (Qt offscreen)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from livereact.core.frame_buffer import DmxFrameBuffer  # noqa: E402
from livereact.fixtures.rig import load_rig  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets

    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_panel_builds_a_slider_per_used_channel(qapp):
    from livereact.ui.fixture_test import FixtureTestPanel

    rig = load_rig()
    fb = DmxFrameBuffer()
    panel = FixtureTestPanel(rig, fb)
    assert len(panel._sliders) == 74  # one per used channel


def test_moving_a_slider_publishes_that_channel(qapp):
    from livereact.ui.fixture_test import FixtureTestPanel

    rig = load_rig()
    fb = DmxFrameBuffer()
    panel = FixtureTestPanel(rig, fb)
    panel._sliders[55].setValue(200)   # Par Left, channel 55 (index 54)
    assert fb.latest()[54] == 200


def test_flash_lights_a_single_fixture(qapp):
    from livereact.ui.fixture_test import FixtureTestPanel

    rig = load_rig()
    fb = DmxFrameBuffer()
    panel = FixtureTestPanel(rig, fb)
    panel._flash(rig.fixture("par_l"))
    frame = fb.latest()
    par = rig.fixture("par_l")
    assert any(frame[par.offset + i] for i in range(4))  # the flashed fixture is lit


def test_save_home_position_to_an_unwritable_path_says_why_instead_of_raising(qapp, tmp_path, monkeypatch):
    """UX_BACKLOG Open 9: a folder the user cannot write (or a path that is a directory) raised
    OSError out of the button's slot; the panel now says "Not saved" in red, like Rig Config and Venue."""
    from PySide6 import QtWidgets

    from livereact.fixtures.profiles import MovingHead
    from livereact.ui.fixture_test import FixtureTestPanel

    rig = load_rig()
    panel = FixtureTestPanel(rig, DmxFrameBuffer())
    head = next(fx for fx in rig.fixtures if isinstance(fx, MovingHead))
    bad = tmp_path / "is_a_directory.json"
    bad.mkdir()  # writing a file over a directory fails with OSError on every OS
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(bad), "JSON (*.json)")))
    panel._save_home_position(head)  # must not raise
    assert panel.status.text().startswith("Not saved")
    assert "c33" in panel.status.styleSheet()
    assert panel.rig_path != str(bad)
