"""Roadmap item 21: the patch view (ui/rig_config.py): library definitions with a mode picker,
next-free-block addressing, inline overlap, and the "address set on the fixture" checklist."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.fixtures.definition import SCHEMA, load_definition  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    from livereact.fixtures import library
    library.save_user_definition(load_definition({
        "schema": SCHEMA, "id": "acme_bar", "manufacturer": "Acme", "model": "Bar",
        "category": "wash_bar", "source": {"kind": "user", "confirmed": True},
        "modes": [
            {"name": "3ch", "channels": [{"offset": i, "function": f}
                                         for i, f in enumerate(["red", "green", "blue"])]},
            {"name": "5ch", "channels": [{"offset": i, "function": f}
                                         for i, f in enumerate(["dimmer", "red", "green", "blue", "strobe"])]},
        ]}))
    return tmp_path / "home"


def _tab(rig=None):
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui.rig_config import RigConfigTab
    rig = rig or _rig_from_dict({"name": "t", "slots": [
        {"slot_id": "p1", "fixture_type": "rgb_par", "dmx_address": 1, "label": "Par"}]})
    return RigConfigTab(rig, None)


def _pick_def(tab, row, fid, mode=None):
    from livereact.ui import rig_config as rc
    tc = tab.table.cellWidget(row, rc.C_TYPE)
    tc.setCurrentIndex(tc.findData(rc._DEF + fid))
    if mode:
        mc = tab.table.cellWidget(row, rc.C_MODE)
        mc.setCurrentIndex(mc.findData(mode))


def test_columns(qapp):
    from livereact.ui import rig_config as rc
    tab = _tab()
    headers = [tab.table.horizontalHeaderItem(i).text() for i in range(tab.table.columnCount())]
    assert headers == ["Slot ID", "Label", "Fixture Type", "Mode", "DMX Address", "Role", "Side", "Address set"]
    assert not tab.table.cellWidget(0, rc.C_MODE).isEnabled()     # native types have no modes


def test_type_list_offers_user_definitions_with_modes(qapp):
    from livereact.ui import rig_config as rc
    tab = _tab()
    tc = tab.table.cellWidget(0, rc.C_TYPE)
    keys = [tc.itemData(i) for i in range(tc.count())]
    assert rc._NATIVE + "rgb_par" in keys and rc._DEF + "acme_bar" in keys
    assert rc._DEF + "rgb_par" not in keys                     # builtins are the native entries
    _pick_def(tab, 0, "acme_bar")
    mc = tab.table.cellWidget(0, rc.C_MODE)
    assert mc.isEnabled() and [mc.itemData(i) for i in range(mc.count())] == ["3ch", "5ch"]
    assert tab._row_type(0) == ("def", "acme_bar@3ch")
    mc.setCurrentIndex(1)
    assert tab._row_channels(0) == 5


def test_add_takes_the_next_free_block(qapp):
    from livereact.ui import rig_config as rc
    tab = _tab()                       # rgb_par at 1-4
    tab._add_blank_row()               # first type: ok103_wash_bar, 6 channels
    assert tab._row_address(1) == 5 and tab._row_channels(1) == 6
    tab.table.cellWidget(0, rc.C_ADDR).setValue(20)            # par now 20-23, bar 5-10
    assert tab.next_free_address(4) == 1
    assert tab.next_free_address(16) == 24                     # 11-19 is too small


def test_next_free_address_skips_gaps_that_are_too_small(qapp):
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui.rig_config import RigConfigTab
    rig = _rig_from_dict({"name": "t", "slots": [
        {"slot_id": "a", "fixture_type": "rgb_par", "dmx_address": 1},        # 1-4
        {"slot_id": "b", "fixture_type": "rgb_par", "dmx_address": 8},        # 8-11
    ]})
    tab = RigConfigTab(rig, None)
    assert tab.next_free_address(3) == 5          # fits in 5-7
    assert tab.next_free_address(4) == 12         # 5-7 is too small


def test_overlap_is_marked_inline_and_cleared(qapp):
    from livereact.ui import rig_config as rc
    tab = _tab()
    tab._add_blank_row()
    assert tab.check_overlaps() == []
    tab.table.cellWidget(1, rc.C_ADDR).setValue(3)              # 3-6 collides with 1-4
    assert tab.check_overlaps() == [0, 1]
    assert "overlap" in tab.status.text().lower()
    assert tab.table.cellWidget(1, rc.C_ADDR).styleSheet()
    tab.table.cellWidget(1, rc.C_ADDR).setValue(5)
    assert tab.check_overlaps() == [] and tab.status.text() == ""
    assert tab.table.cellWidget(1, rc.C_ADDR).styleSheet() == ""


def test_overlap_counts_a_definition_mode_width(qapp):
    from livereact.ui import rig_config as rc
    tab = _tab()
    tab._add_blank_row()
    _pick_def(tab, 1, "acme_bar", "5ch")
    tab.table.cellWidget(1, rc.C_ADDR).setValue(5)
    assert tab.check_overlaps() == []
    tab.table.cellWidget(0, rc.C_ADDR).setValue(9)              # par 9-12 vs bar 5-9
    assert tab.check_overlaps() == [0, 1]


def test_collect_writes_a_v2_slot_and_embeds_the_definition(qapp):
    from livereact.fixtures.generic import GenericFixture
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui import rig_config as rc
    tab = _tab()
    tab._add_blank_row()
    _pick_def(tab, 1, "acme_bar", "5ch")
    data = tab._collect()
    slot = data["slots"][1]
    assert slot["fixture"] == "acme_bar@5ch" and "fixture_type" not in slot
    assert set(data["definitions"]) == {"acme_bar"}
    rig = _rig_from_dict(data)
    assert isinstance(rig.fixtures[1], GenericFixture) and rig.fixtures[1].channel_count == 5
    # and back to a native type: the v2 key goes away
    tc = tab.table.cellWidget(1, rc.C_TYPE)
    tc.setCurrentIndex(tc.findData(rc._NATIVE + "rgb_par"))
    data = tab._collect()
    assert data["slots"][1]["fixture_type"] == "rgb_par" and "fixture" not in data["slots"][1]
    assert "definitions" not in data


def test_address_set_checklist_round_trips(qapp):
    from PySide6 import QtCore
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui import rig_config as rc
    tab = _tab()
    assert "address_set" not in tab._collect()["slots"][0]      # unticked: v1 slot unchanged
    tab.table.item(0, rc.C_SET).setCheckState(QtCore.Qt.CheckState.Checked)
    data = tab._collect()
    assert data["slots"][0]["address_set"] is True
    tab2 = rc.RigConfigTab(_rig_from_dict(data), None)
    assert tab2.table.item(0, rc.C_SET).checkState() == QtCore.Qt.CheckState.Checked


def test_existing_v2_rig_shows_its_definition_and_mode(qapp, home):
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui import rig_config as rc
    from livereact.ui.rig_config import RigConfigTab
    embedded = load_definition({
        "schema": SCHEMA, "id": "rig_only", "manufacturer": "Z", "model": "Spot", "category": "par",
        "source": {"kind": "user"},
        "modes": [{"name": "a", "channels": [{"offset": 0, "function": "dimmer"}]},
                  {"name": "b", "channels": [{"offset": 0, "function": "dimmer"}, {"offset": 1, "function": "red"}]}],
    }).to_dict()
    rig = _rig_from_dict({"name": "t", "definitions": {"rig_only": embedded},
                          "slots": [{"slot_id": "s", "fixture": "rig_only@b", "dmx_address": 7}]})
    tab = RigConfigTab(rig, None)
    assert tab._row_type(0) == ("def", "rig_only@b")
    assert tab.table.cellWidget(0, rc.C_ADDR).value() == 7
    assert tab._collect()["definitions"]["rig_only"]["model"] == "Spot"


def test_channel_reference_for_a_definition(qapp):
    tab = _tab()
    _pick_def(tab, 0, "acme_bar", "5ch")
    tab.table.selectRow(0)
    tab._update_reference()
    text = tab.channel_ref.toPlainText()
    assert "CH1  dimmer" in text and "CH5  strobe" in text


def test_save_validates_through_the_loader(qapp, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    from livereact.ui import rig_config as rc
    tab = _tab()
    tab._add_blank_row()
    _pick_def(tab, 1, "acme_bar", "5ch")
    out = tmp_path / "rig.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a: (str(out), "")))
    tab._save()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["slots"][1]["fixture"] == "acme_bar@5ch"
    tab.table.cellWidget(1, rc.C_ADDR).setValue(2)
    out.unlink()
    tab._save()
    assert not out.exists() and "Not saved" in tab.status.text()
