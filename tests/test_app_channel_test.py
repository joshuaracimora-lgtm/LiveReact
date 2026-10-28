"""Roadmap item 22: the channel-test screen for a definition (ui/channel_test.py)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.core.frame_buffer import DmxFrameBuffer  # noqa: E402
from livereact.fixtures.definition import SCHEMA, load_definition, validate  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


PAR = {"schema": SCHEMA, "id": "acme_par", "manufacturer": "Acme", "model": "Par", "category": "par",
       "source": {"kind": "manual_ai", "confirmed": False},
       "modes": [{"name": "3ch", "channels": [
           {"offset": 0, "function": "dimmer"},
           {"offset": 1, "function": "color_wheel", "capabilities": [
               {"range": [0, 9], "type": "color_wheel", "label": "Open"},
               {"range": [10, 19], "type": "color_wheel", "label": "Red", "confidence": 0.4, "source_page": 3}]},
           {"offset": 2, "function": "strobe"}]}]}
LASER = {"schema": SCHEMA, "id": "acme_laser", "manufacturer": "Acme", "model": "Laser", "category": "laser",
         "source": {"kind": "user", "confirmed": False},
         "modes": [{"name": "2ch", "channels": [{"offset": 0, "function": "mode"},
                                                {"offset": 1, "function": "laser_pattern"}]}]}


def _panel(raw, address=10, saved=None):
    from livereact.ui.channel_test import ChannelTestPanel
    fb = DmxFrameBuffer()
    save = (lambda d: saved.append(d) or "mem") if saved is not None else None
    return ChannelTestPanel(load_definition(raw), address=address, frame_buffer=fb, save=save,
                            now=lambda: "2026-10-28T04:00:00"), fb


def _pass_all(p):
    for m in p.marks:
        m.setCurrentText("pass")


def test_slider_per_channel_labelled_by_function(qapp):
    from PySide6 import QtWidgets
    p, _ = _panel(PAR)
    assert len(p.sliders) == 3
    labels = [w.text() for w in p.findChildren(QtWidgets.QLabel) if w.text().startswith("CH")]
    assert labels == ["CH1 · dimmer", "CH2 · color wheel", "CH3 · strobe"]


def test_slider_publishes_at_the_address(qapp):
    p, fb = _panel(PAR, address=10)
    p.sliders[0].setValue(200)
    assert fb.latest()[9] == 200


def test_capability_picker_sends_the_middle_of_its_range(qapp):
    p, fb = _panel(PAR)
    picker = p.pickers[1]
    assert picker.itemText(2) == "Red: 10-19"
    assert "low confidence" in picker.itemData(2, 3)          # ToolTipRole, with the manual page
    picker.activated.emit(2)
    assert p.sliders[1].value() == 14 and fb.latest()[10] == 14
    assert not p.pickers[0].isEnabled()                        # no capabilities listed


def test_confirm_needs_every_channel_pass(qapp):
    saved = []
    p, _ = _panel(PAR, saved=saved)
    assert not p.b_confirm.isEnabled()
    p.marks[0].setCurrentText("pass")
    p.marks[1].setCurrentText("fail")
    p.marks[2].setCurrentText("pass")
    assert not p.b_confirm.isEnabled() and p.confirm() is None and saved == []
    p.marks[1].setCurrentText("pass")
    assert p.b_confirm.isEnabled()
    p.b_confirm.click()
    d = saved[0]
    assert d.source.confirmed is True and d.source.confirmed_at == "2026-10-28T04:00:00"
    assert d.source.kind == "manual_ai" and validate(d) == []


def test_confirm_saves_through_the_library_by_default(qapp):
    from livereact.fixtures import library
    p, _ = _panel(PAR)
    got = []
    p.confirmed.connect(got.append)
    _pass_all(p)
    p.b_confirm.click()
    d, _mode = library.resolve("acme_par@3ch")
    assert d.source.confirmed and got and got[0] == d


def test_confirm_reports_a_refused_save(qapp):
    raw = dict(PAR, id="rgb_par")                               # a builtin id
    p, _ = _panel(raw)
    _pass_all(p)
    assert p.confirm() is None and "Not saved" in p.status.text()


def test_unconfirmed_laser_cannot_be_confirmed_without_all_three_intents(qapp):
    saved = []
    p, _ = _panel(LASER, saved=saved)
    assert p.intent_box.isVisibleTo(p) and p.cb_laser_out.isVisibleTo(p)
    _pass_all(p)
    assert not p.b_confirm.isEnabled()
    p.capture_intent("blank")
    p.sliders[0].setValue(100)
    p.sliders[1].setValue(40)
    p.capture_intent("fan")
    assert not p.b_confirm.isEnabled()
    assert p.confirm() is None and saved == []
    assert "Static line" in p.status.text()
    p.sliders[1].setValue(12)
    p.capture_intent("line_static")
    assert p.b_confirm.isEnabled()
    d = p.confirm()
    assert d.source.confirmed
    assert d.laser_intents.blank == {0: 0, 1: 0}
    assert d.laser_intents.fan == {0: 100, 1: 40}
    assert d.laser_intents.line_static == {0: 100, 1: 12}
    assert validate(d) == []


def test_confirmed_laser_definition_is_ready_to_drive(qapp):
    from livereact.fixtures.generic import GenericLaser
    p, _ = _panel(LASER, saved=[])
    _pass_all(p)
    for k in ("blank", "line_static", "fan"):
        p.sliders[0].setValue(0 if k == "blank" else 100)
        p.capture_intent(k)
    assert GenericLaser(p.confirm()).ready


def test_laser_channels_send_zero_unless_enabled_on_this_screen(qapp):
    p, fb = _panel(LASER, address=1)
    p.sliders[0].setValue(100)
    assert fb.latest()[0] == 0
    p.cb_laser_out.setChecked(True)
    assert fb.latest()[0] == 100
    p.cb_laser_out.setChecked(False)
    assert fb.latest()[0] == 0


def test_non_laser_hides_laser_controls_and_all_off(qapp):
    p, fb = _panel(PAR)
    assert not p.intent_box.isVisibleTo(p) and not p.cb_laser_out.isVisibleTo(p)
    p.sliders[2].setValue(90)
    p.b_all_off.click()
    assert fb.latest()[11] == 0 and p.values == [0, 0, 0]


def test_rig_fixture_test_panel_names_definition_channels(qapp):
    from PySide6 import QtWidgets
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui.fixture_test import FixtureTestPanel
    rig = _rig_from_dict({"name": "t", "definitions": {"acme_par": PAR},
                          "slots": [{"slot_id": "p", "fixture": "acme_par@3ch", "dmx_address": 5},
                                    {"slot_id": "n", "fixture_type": "rgb_par", "dmx_address": 1}]})
    panel = FixtureTestPanel(rig, DmxFrameBuffer())
    labels = {w.text() for w in panel.findChildren(QtWidgets.QLabel)}
    assert {"ch 5 · dimmer", "ch 6 · color wheel", "ch 7 · strobe"} <= labels
    assert "ch 1 · dimmer" in labels and "ch 2 · color" in labels      # native: kind names, as before
