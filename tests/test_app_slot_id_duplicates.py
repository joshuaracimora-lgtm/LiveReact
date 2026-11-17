"""Roadmap item 55: the patch view marks duplicate slot IDs inline, the way it marks address overlaps.

Before, two rows with one Slot ID looked fine until Save refused them; the user could not see which rows
collided. Now the slot cells of both rows turn red with a tooltip as soon as the ID is typed, the status
line names the ID, and fixing either row clears the mark.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _tab(tmp_path):
    from livereact.fixtures.rig import _rig_from_dict
    from livereact.ui.rig_config import RigConfigTab
    return RigConfigTab(_rig_from_dict({"name": "t", "laser_profile": "accent", "slots": []}), str(tmp_path / "t.json"))


def _marked(tab, row):
    from livereact.ui.rig_config import C_SLOT
    item = tab.table.item(row, C_SLOT)
    return "share" in item.toolTip().lower() and item.background().color().name() != "#000000" \
        and item.background().style() != 0


def test_typing_a_duplicate_slot_id_marks_both_rows_and_names_it(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab._add_blank_row()
    tab._add_blank_row()
    tab._add_blank_row()
    assert tab.check_duplicate_slots() == []
    tab.table.item(2, 0).setText("slot_1")          # itemChanged fires, as when the user types
    assert tab.check_duplicate_slots() == [0, 2]
    assert _marked(tab, 0) and _marked(tab, 2) and not _marked(tab, 1)
    assert "slot_1" in tab.status.text() and tab.status.text().startswith("⚠")


def test_fixing_the_id_clears_the_mark_and_the_status(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab._add_blank_row()
    tab._add_blank_row()
    tab.table.item(1, 0).setText("slot_1")
    assert _marked(tab, 1)
    tab.table.item(1, 0).setText("slot_9")
    assert not _marked(tab, 0) and not _marked(tab, 1)
    assert tab.status.text() == ""


def test_ids_differing_only_in_surrounding_spaces_count_as_duplicates(qapp, tmp_path):
    """_collect strips the ID, so ' slot_1 ' and 'slot_1' are the same slot once saved."""
    tab = _tab(tmp_path)
    tab._add_blank_row()
    tab._add_blank_row()
    tab.table.item(1, 0).setText(" slot_1 ")
    assert tab.check_duplicate_slots() == [0, 1]


def test_an_address_overlap_and_a_duplicate_are_both_reported(qapp, tmp_path):
    """The overlap check must not wipe the duplicate warning off the status line (and vice versa)."""
    tab = _tab(tmp_path)
    tab._add_blank_row()
    tab._add_blank_row()
    tab.table.item(1, 0).setText("slot_1")
    spin0 = tab.table.cellWidget(0, 4)
    tab.table.cellWidget(1, 4).setValue(spin0.value())   # now they overlap as well
    assert tab.check_overlaps() and tab.check_duplicate_slots() == [0, 1]
    assert "slot_1" in tab.status.text()
    tab.table.item(1, 0).setText("slot_2")
    assert tab.status.text().startswith("⚠") and "slot_1" not in tab.status.text()


def test_removing_a_duplicated_row_clears_the_mark(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab._add_blank_row()
    tab._add_blank_row()
    tab.table.item(1, 0).setText("slot_1")
    tab.table.selectRow(1)
    tab._remove_selected()
    assert tab.check_duplicate_slots() == [] and not _marked(tab, 0)


def test_a_clean_table_is_unchanged(qapp, tmp_path):
    tab = _tab(tmp_path)
    tab._add_blank_row()
    tab._add_blank_row()
    assert not _marked(tab, 0) and not _marked(tab, 1) and tab.status.text() == ""
