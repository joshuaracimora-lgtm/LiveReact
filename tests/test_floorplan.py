"""Headless tests for the scaled venue floor plan (ui/floorplan.py)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from livereact.fixtures.geometry import Point3, Venue, ft_to_m  # noqa: E402
from livereact.fixtures.rig import _rig_from_dict, load_rig  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets

    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _josh_rig_with_venue():
    rig = load_rig(os.path.join(HERE, "rigs", "josh_rig.json"))
    w, l, h = ft_to_m(30), ft_to_m(40), ft_to_m(12)
    rig.venue = Venue(w, l, h, Venue.default_targets(w, l, h))
    mh, laser = rig.fixture("mh"), rig.fixture("laser")
    mh.position = Point3(ft_to_m(5), ft_to_m(2), ft_to_m(1))
    laser.position = Point3(ft_to_m(25), ft_to_m(2), ft_to_m(3))
    rig.refresh_geometry()
    return rig


def test_plan_draws_the_default_rig_without_any_venue(qapp):
    from livereact.ui.floorplan import FloorPlanView
    from livereact.ui.visualizer import build_demo_universe

    rig = load_rig()
    view = FloorPlanView(rig, editable=False)
    assert len(view._bodies) == 8
    view.update_from_universe(rig.boot_universe())
    view.update_from_universe(build_demo_universe(rig, 1.0))   # lit, lasers in the drop window
    # every fixture lands inside the default room
    v = view.venue()
    for key in view._bodies:
        x, y = view.item_position_m(key)
        assert 0 <= x <= v.width_m and 0 <= y <= v.length_m


def test_plan_places_positioned_fixtures_to_the_centimetre(qapp):
    from livereact.ui.floorplan import FloorPlanView

    rig = _josh_rig_with_venue()
    view = FloorPlanView(rig, editable=True)
    x, y = view.item_position_m("mh")
    assert x == pytest.approx(ft_to_m(5), abs=0.01)
    assert y == pytest.approx(ft_to_m(2), abs=0.01)
    assert set(view._targets) == {"dj_booth", "dancefloor_centre", "back_wall", "ceiling_bounce"}


def test_show_tab_plan_is_not_movable_but_venue_plan_is(qapp):
    from PySide6 import QtWidgets
    from livereact.ui.floorplan import FloorPlanView

    movable = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    rig = _josh_rig_with_venue()
    ro = FloorPlanView(rig, editable=False)
    assert all(not (i.flags() & movable) for i in ro._bodies.values())
    assert all(not (i.flags() & movable) for i in ro._targets.values())
    rw = FloorPlanView(rig, editable=True)
    assert all(i.flags() & movable for i in rw._bodies.values())


def test_moving_head_beam_lands_where_the_geometry_says(qapp):
    """With a captured calibration, the drawn beam ends at the floor hit of
    the head's current pan/tilt."""
    from livereact.fixtures.geometry import aim
    from livereact.merge.merge import Contribution, ManualParams, render_universe
    from livereact.ui.floorplan import FloorPlanView

    import math

    rig = _josh_rig_with_venue()
    mh = rig.fixture("mh")
    mh.position = Point3(ft_to_m(5), ft_to_m(2), ft_to_m(10))   # trussed high, so the beam reaches the floor
    target = rig.venue.targets["dancefloor_centre"]
    pan01, tilt01, ok = aim(mh.position, mh.facing_deg, target, mh.calibration, mh.PAN_RANGE_DEG, mh.TILT_RANGE_DEG)
    assert ok
    c = Contribution()
    mh.set_aim(c, pan01, tilt01)
    mh.set_intensity(c, 1.0)
    uni = render_universe([c], rig, ManualParams())
    view = FloorPlanView(rig, editable=False)
    view.update_from_universe(uni)
    line = view._beams["mh"].line()
    end_x, end_y = view.from_scene(line.p2())
    # the target is 1.0 m above the floor, so the beam lands a little past it
    # on the floor -- on exactly the same bearing from the head
    want = math.atan2(target.x - mh.position.x, target.y - mh.position.y)
    got = math.atan2(end_x - mh.position.x, end_y - mh.position.y)
    assert got == pytest.approx(want, abs=math.radians(0.5))
    assert math.hypot(end_x - mh.position.x, end_y - mh.position.y) > math.hypot(target.x - mh.position.x, target.y - mh.position.y)


def test_legacy_rig_json_without_geometry_draws_from_role_side_rows(qapp):
    from livereact.ui.floorplan import FloorPlanView

    rig = _rig_from_dict({"name": "t", "slots": [
        {"slot_id": "a", "fixture_type": "rgb_par", "dmx_address": 1, "role": "wash", "side": "left"},
        {"slot_id": "b", "fixture_type": "rgb_par", "dmx_address": 5, "role": "wash", "side": "right"},
    ]})
    view = FloorPlanView(rig)
    ax, _ = view.item_position_m("a")
    bx, _ = view.item_position_m("b")
    assert ax < bx


def test_destroying_the_plan_with_a_fixture_selected_raises_nothing(qapp, monkeypatch):
    """UI_BUGS #1: deleting the scene with a body selected fires selectionChanged
    while the items are already gone; the slot walked ``_bodies`` (deleted
    PlanItems) and printed RuntimeError on every venue_check / console close."""
    import sys

    from shiboken6 import Shiboken
    from livereact.ui.floorplan import FloorPlanView

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a[1]))
    rig = _josh_rig_with_venue()
    view = FloorPlanView(rig, editable=True)
    seen = []
    view.fixture_selected.connect(seen.append)
    view._bodies["mh"].setSelected(True)
    assert seen == ["mh"]
    view._scene.clearSelection()
    assert seen[-1] == ""
    view._targets["dj_booth"].setSelected(True)          # a target is not a fixture
    assert seen[-1] == ""
    view._bodies["laser"].setSelected(True)
    assert seen[-1] == "laser"
    Shiboken.delete(view._scene)
    assert errors == []


def _click(view, item, drag_px: tuple[int, int] = (0, 0)):
    """Press on ``item`` through the viewport, optionally drag, release."""
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    view.resize(800, 900)
    view._fit()
    vp = view.viewport()
    p = view.mapFromScene(item.scenePos())
    left = QtCore.Qt.MouseButton.LeftButton
    QTest.mousePress(vp, left, QtCore.Qt.KeyboardModifier.NoModifier, p)
    if drag_px != (0, 0):
        for k in (0.25, 0.5, 0.75, 1.0):
            q = QtCore.QPoint(p.x() + int(drag_px[0] * k), p.y() + int(drag_px[1] * k))
            QTest.mouseMove(vp, q)
        p = q
    QTest.mouseRelease(vp, left, QtCore.Qt.KeyboardModifier.NoModifier, p)


def test_a_plain_click_moves_no_fixture(qapp):
    """UI_BUGS #2: clicking (no drag) committed a drop for the selected item:
    an unplaced fixture got a real position (and a new side), a placed one
    was snapped to the 0.5 ft grid."""
    from livereact.ui.floorplan import FloorPlanView

    rig = _josh_rig_with_venue()
    mh = rig.fixture("mh")
    mh.position = Point3(ft_to_m(5.2), ft_to_m(2.3), ft_to_m(1))   # off the snap grid
    unplaced = next(fx for fx in rig.fixtures if fx.position is None)
    key = getattr(unplaced, "slot_id", unplaced.label)
    side = unplaced.side
    changed = []
    view = FloorPlanView(rig, editable=True, on_changed=lambda: changed.append(1))
    _click(view, view._bodies[key])
    assert view._bodies[key].isSelected()
    assert unplaced.position is None and unplaced.side == side
    _click(view, view._bodies["mh"])
    assert view._bodies["mh"].isSelected()
    assert mh.position == Point3(ft_to_m(5.2), ft_to_m(2.3), ft_to_m(1))
    _click(view, view._targets["dj_booth"])
    assert view._targets["dj_booth"].isSelected()
    assert changed == []
    # a real drag still commits (and snaps) the dragged body
    _click(view, view._bodies["mh"], drag_px=(60, 0))
    assert mh.position.x > ft_to_m(5.2) and changed == [1]


def test_dropping_a_plan_with_a_fixture_selected_does_not_crash(qapp):
    """The scene had no parent, so it died after its view and reported the
    selection change to the already-deleted view: an access violation that
    killed the process (a crash in pytest after any test that clicked a body)."""
    import gc

    from livereact.ui.floorplan import FloorPlanView

    def build_and_drop():
        view = FloorPlanView(_josh_rig_with_venue(), editable=True)
        view._bodies["mh"].setSelected(True)
        assert view._scene.parent() is view

    build_and_drop()
    gc.collect()


@pytest.mark.parametrize("rig_name", sorted(os.listdir(os.path.join(HERE, "rigs"))))
def test_booth_wall_title_clears_the_axis_numbers_and_stays_in_the_scene(qapp, rig_name):
    """UI_BUGS (labels): "DJ BOOTH WALL" sat at a fixed offset under the x-axis
    numbers and overlapped one of them on every rig."""
    from PySide6 import QtWidgets
    from livereact.ui.floorplan import FloorPlanView

    view = FloorPlanView(load_rig(os.path.join(HERE, "rigs", rig_name)), editable=True)
    texts = [i for i in view._scene.items() if isinstance(i, QtWidgets.QGraphicsSimpleTextItem)]
    booth = next(t for t in texts if t.text() == "DJ BOOTH WALL")
    ticks = [t for t in texts if t.text().endswith("′") and t.text()[:-1].isdigit()]
    rect = booth.sceneBoundingRect()
    assert ticks and not [t.text() for t in ticks if t.sceneBoundingRect().intersects(rect)]
    assert view._scene.sceneRect().contains(rect)


@pytest.mark.parametrize("width_ft", [10.0, 15.0, 30.0])
def test_legend_fits_the_scene_in_a_narrow_room(qapp, width_ft):
    """UI_BUGS (labels): the one-line glyph legend was wider than a narrow
    room's scene and was cropped."""
    from PySide6 import QtWidgets
    from livereact.ui.floorplan import _LEGEND, FloorPlanView

    rig = _josh_rig_with_venue()
    w, l, h = ft_to_m(width_ft), ft_to_m(30), ft_to_m(12)
    rig.venue = Venue(w, l, h, Venue.default_targets(w, l, h))
    rig.fixture("laser").position = Point3(w * 0.8, ft_to_m(2), ft_to_m(3))
    rig.refresh_geometry()
    view = FloorPlanView(rig, editable=True)
    legend = next(i for i in view._scene.items() if isinstance(i, QtWidgets.QGraphicsSimpleTextItem)
                  and "head" in i.text() and "target" in i.text())
    assert legend.text().split() == _LEGEND.split()          # every entry still there
    assert view._scene.sceneRect().contains(legend.sceneBoundingRect())
    for key, cap in view._captions.items():
        for line in cap._lines():
            assert not line.sceneBoundingRect().intersects(legend.sceneBoundingRect()), key


def test_crowded_small_room_keeps_every_caption_inside_the_scene(qapp):
    """UI_BUGS (labels): when no slot was free, the last resort stacked the
    caption straight down, ignoring the scene and the axis / wall-title bands:
    on a small room "booth_edge_right" landed under the room, cropped and on
    top of "DJ BOOTH WALL"."""
    from livereact.ui.floorplan import FloorPlanView

    rig = load_rig(os.path.join(HERE, "rigs", "junior_crib.json"))
    k, v = 0.6, rig.venue
    rig.venue = Venue(v.width_m * k, v.length_m * k, v.height_m,
                      {n: Point3(p.x * k, p.y * k, p.z) for n, p in v.targets.items()})
    for fx in rig.fixtures:
        if fx.position is not None:
            fx.position = Point3(fx.position.x * k, fx.position.y * k, fx.position.z)
    rig.refresh_geometry()
    view = FloorPlanView(rig, editable=True)
    view._layout_labels()
    scene = view._scene.sceneRect()
    room_bottom = view.to_scene(0, 0).y()
    for key, cap in view._captions.items():
        for line in cap._lines():
            r = line.sceneBoundingRect()
            assert scene.contains(r), (key, line.text())
            assert r.top() < room_bottom, (key, line.text())      # not in the axis / wall-title band


@pytest.mark.parametrize("rig_name", ["testing_setup.json", "junior_crib.json"])
def test_axis_numbers_are_not_drawn_under_a_body(qapp, rig_name):
    """UI_BUGS (labels): a fixture on the booth wall (wash bar) hung over the
    x-axis numbers, and a target on the left wall over a y-axis number; the
    numbers sat at a fixed offset from the room edge whatever was there."""
    from PySide6 import QtWidgets
    from livereact.ui.floorplan import FloorPlanView

    view = FloorPlanView(load_rig(os.path.join(HERE, "rigs", rig_name)), editable=True)
    texts = [i for i in view._scene.items() if isinstance(i, QtWidgets.QGraphicsSimpleTextItem)]
    ticks = [t for t in texts if t.text().endswith("′") and t.text()[:-1].isdigit()]
    bodies = [b.sceneBoundingRect() for b in (*view._bodies.values(), *view._targets.values())]
    under = [t.text() for t in ticks if any(t.sceneBoundingRect().intersects(b) for b in bodies)]
    assert ticks and under == []
    scene = view._scene.sceneRect()
    assert all(scene.contains(t.sceneBoundingRect()) for t in texts if t.text())
    booth = next(t for t in texts if t.text() == "DJ BOOTH WALL")
    assert not [t.text() for t in ticks if t.sceneBoundingRect().intersects(booth.sceneBoundingRect())]


def test_tooltips_follow_a_drag(qapp):
    """UI_BUGS (labels): after dragging a fixture or a target its tooltip
    still gave the old position in feet (only refresh_layout rebuilt them)."""
    from livereact.fixtures.geometry import m_to_ft
    from livereact.ui.floorplan import FloorPlanView

    rig = _josh_rig_with_venue()
    view = FloorPlanView(rig, editable=True)
    _click(view, view._bodies["mh"], drag_px=(60, 0))
    mh = rig.fixture("mh")
    assert mh.position.x > ft_to_m(5.5)
    assert f"{m_to_ft(mh.position.x):.1f} ft along the booth wall" in view._bodies["mh"].toolTip()
    _click(view, view._targets["dj_booth"], drag_px=(0, -60))
    p = rig.venue.targets["dj_booth"]
    assert f"{m_to_ft(p.y):.1f} ft out" in view._targets["dj_booth"].toolTip()


def test_a_target_under_a_fixture_stays_visible_and_clickable(qapp):
    """UI_BUGS (labels): `_mark_stacks` ignored targets, so a target under a
    fixture (junior_crib's dj_booth under the wash bar) was drawn beneath it:
    hidden, and a click there picked the fixture."""
    from livereact.ui.floorplan import FloorPlanView

    rig = _josh_rig_with_venue()
    o = rig.fixture("mh").position
    rig.venue.targets["dj_booth"] = Point3(o.x, o.y, ft_to_m(4))
    view = FloorPlanView(rig, editable=True)
    target = view._targets["dj_booth"]
    top = view._scene.items(target.scenePos())[0]            # topmost item at the diamond's centre
    assert top is target
    # moved off the fixture (a drag), it goes back under the bodies' layer
    _click(view, target, drag_px=(0, -80))
    assert target.zValue() < view._bodies["mh"].zValue()
    # and a fixture dragged onto a target puts the target back on top
    rig2 = _josh_rig_with_venue()
    view2 = FloorPlanView(rig2, editable=True)
    t2 = view2._targets["dj_booth"]
    mh = view2._bodies["mh"]
    view2.resize(800, 900); view2._fit()
    d = view2.mapFromScene(t2.scenePos()) - view2.mapFromScene(mh.scenePos())
    _click(view2, mh, drag_px=(d.x(), d.y()))
    assert view2._scene.items(t2.scenePos())[0] is t2
