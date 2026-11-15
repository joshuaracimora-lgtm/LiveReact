"""UX_BACKLOG Open 7: the channel test only tested a definition's first mode.

A two-mode definition (common from Open Fixture Library) was confirmed whole after the user had seen one mode.
The screen now offers every mode, and Confirm (which marks the whole definition confirmed) waits for all of them.
"""
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


TWO_MODES = {"schema": SCHEMA, "id": "acme_two", "manufacturer": "Acme", "model": "Two", "category": "par",
             "source": {"kind": "user", "confirmed": False},
             "modes": [{"name": "3ch", "channels": [{"offset": 0, "function": "dimmer"},
                                                    {"offset": 1, "function": "red"},
                                                    {"offset": 2, "function": "green"}]},
                       {"name": "5ch", "channels": [{"offset": 0, "function": "dimmer"},
                                                    {"offset": 1, "function": "red"},
                                                    {"offset": 2, "function": "green"},
                                                    {"offset": 3, "function": "blue"},
                                                    {"offset": 4, "function": "strobe"}]}]}
ONE_MODE = {**TWO_MODES, "id": "acme_one", "modes": TWO_MODES["modes"][:1]}


def _panel(raw, saved):
    from livereact.ui.channel_test import ChannelTestPanel
    fb = DmxFrameBuffer()
    p = ChannelTestPanel(load_definition(raw), address=10, frame_buffer=fb,
                         save=lambda d: saved.append(d) or "mem", now=lambda: "2026-11-15T15:00:00")
    return p, fb


def _pass_all(p):
    for m in p.marks:
        m.setCurrentText("pass")


def test_a_two_mode_definition_offers_both_modes_and_switching_shows_that_modes_channels(qapp):
    p, fb = _panel(TWO_MODES, [])
    assert not p.mode_row.isHidden()
    assert [p.mode_picker.itemData(i) for i in range(p.mode_picker.count())] == ["3ch", "5ch"]
    assert len(p.sliders) == 3
    p.sliders[0].setValue(200)
    assert fb.latest()[9] == 200
    p.mode_picker.setCurrentIndex(1)
    assert p.mode.name == "5ch"
    assert len(p.sliders) == len(p.marks) == len(p.values) == 5
    assert "5ch" in p.head.text() and "DMX 10-14" in p.head.text()
    assert not any(fb.latest()), "switching mode must not leave the old mode's values on the fixture"
    p.sliders[4].setValue(77)
    assert fb.latest()[13] == 77


def test_confirm_waits_for_every_mode_and_keeps_each_modes_marks(qapp):
    saved = []
    p, _ = _panel(TWO_MODES, saved)
    _pass_all(p)
    assert not p.b_confirm.isEnabled()
    assert any("5ch" in m for m in p.missing())
    assert p.confirm() is None and saved == []
    p.mode_picker.setCurrentIndex(1)
    assert [m.currentText() for m in p.marks] == ["untested"] * 5
    _pass_all(p)
    p.mode_picker.setCurrentIndex(0)
    assert [m.currentText() for m in p.marks] == ["pass"] * 3, "a mode's marks survive a trip to another mode"
    assert p.missing() == []
    assert p.confirm() is not None and saved and saved[0].source.confirmed


def test_a_one_mode_definition_shows_no_picker_and_confirms_as_before(qapp):
    saved = []
    p, _ = _panel(ONE_MODE, saved)
    assert p.mode_row.isHidden()
    _pass_all(p)
    assert p.confirm() is not None and saved
