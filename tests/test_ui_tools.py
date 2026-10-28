"""The UI agent's instruments: ui_snapshot (label overlap), venue_check (the grid drives the lights)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    sys.path.insert(0, str(ROOT / "tools" / "agent"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "agent" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fonts_are_real_so_label_sizes_mean_something():
    """Without QT_QPA_FONTDIR the offscreen platform draws placeholder boxes (tests/conftest.py)."""
    from PySide6 import QtGui, QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert "Arial" in QtGui.QFontDatabase.families() or len(QtGui.QFontDatabase.families()) > 5


def test_overlap_report_counts_overlapping_and_cropped_text():
    snap = _load("ui_snapshot")
    snap.qapp()
    from PySide6 import QtCore
    from livereact.fixtures.rig import load_rig
    from livereact.ui.floorplan import FloorPlanView
    view = FloorPlanView(load_rig("rigs/junior_crib.json"), editable=True)
    base = snap.overlap_report(view)
    scene = view.scene()
    a = scene.addSimpleText("OVERLAP-A"); a.setPos(10, 10)
    b = scene.addSimpleText("OVERLAP-B"); b.setPos(14, 12)
    far = scene.sceneRect().bottomRight() + QtCore.QPointF(500, 500)
    c = scene.addSimpleText("CROPPED"); c.setPos(far)
    rep = snap.overlap_report(view)
    assert rep["text_text"] >= base["text_text"] + 1
    assert rep["text_outside"] >= base["text_outside"] + 1
    assert ("OVERLAP-A", "OVERLAP-B") in rep["examples"]["text_text"] or \
           ("OVERLAP-B", "OVERLAP-A") in rep["examples"]["text_text"]


def test_venue_check_passes_on_the_venue_rigs():
    vc = _load("venue_check")
    res = vc.run(["rigs/junior_crib.json", "rigs/testing_setup.json", "rigs/josh_rig.json"])
    assert set(res["rigs"]) == {"rigs/junior_crib.json", "rigs/testing_setup.json"}   # josh_rig has no venue
    assert res["ok"], res["rigs"]
    inv = res["inventory"]
    assert "Break Single Spot Special" in inv["aims_at_targets"]
    assert "Drop Snare Roll Ramp" in inv["aims_at_targets"]
    assert sum(len(v) for v in inv.values()) == 28


def test_venue_check_catches_a_broken_link(monkeypatch):
    """If an aim scene stopped reading the fixture's position, the check must fail."""
    vc = _load("venue_check")
    from livereact.scenes import base
    monkeypatch.setattr(base, "aim", lambda *a, **k: (0.5, 0.5, True))
    fails = vc.check_edits_are_live("rigs/testing_setup.json")
    assert any("did not change" in f or "new geometry says" in f for f in fails)
