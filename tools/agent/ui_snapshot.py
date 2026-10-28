"""Render the Venue tab offscreen for every rig and measure label overlap; the UI agent's eyes.

    python tools/agent/ui_snapshot.py [--out results/agent/<date>/ui] [--rigs rigs/a.json ...] [--json]

For each rig (default: every ``rigs/*.json``, plus ``junior_crib@small``, the same rig in a room
scaled to 60 % -- the dense case) it builds a real ``VenueTab`` with the offscreen Qt platform,
saves ``<rig>.png`` (the agent can open it with Read) and counts, in scene coordinates:

- ``text_text``: pairs of text items (captions, axis numbers, wall title, legend) that overlap
- ``text_body``: text items overlapping a fixture or target glyph
- ``text_outside``: text items not fully inside the scene rectangle (cropped by fitInView)

``overlap_report`` is what tools/agent/gate_ui.py compares, harness vs. the agent's branch, in the
same run. This tool lives under tools/ on purpose: the UI agent may change the floor plan, never
the instrument that judges it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The offscreen platform ships no fonts: without a font dir every glyph is a placeholder box and every
# label is measured at the wrong size, so the overlap counts would describe nothing real (2026-10-26).
if sys.platform == "win32" and Path(os.environ.get("WINDIR", r"C:\Windows"), "Fonts").is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")))

MIN_OVERLAP_PX2 = 2.0   # rectangles touching by a hair are not an overlap


def qapp():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _area(a, b) -> float:
    r = a.intersected(b)
    return max(0.0, r.width()) * max(0.0, r.height())


def overlap_report(plan) -> dict:
    """Overlap counts for a FloorPlanView (see the module docstring)."""
    from PySide6 import QtWidgets
    scene = plan.scene()
    texts = [it for it in scene.items()
             if isinstance(it, QtWidgets.QGraphicsSimpleTextItem) and it.isVisible() and it.text()]
    bodies = list(plan._bodies.values()) + list(plan._targets.values())
    trects = [(t.text(), t.sceneBoundingRect()) for t in texts]
    brects = [b.sceneBoundingRect() for b in bodies]
    pairs = []
    for i in range(len(trects)):
        for j in range(i + 1, len(trects)):
            if _area(trects[i][1], trects[j][1]) > MIN_OVERLAP_PX2:
                pairs.append((trects[i][0], trects[j][0]))
    on_body = [name for name, r in trects if any(_area(r, b) > MIN_OVERLAP_PX2 for b in brects)]
    sr = scene.sceneRect()
    outside = [name for name, r in trects if not sr.contains(r)]
    return {"text_items": len(trects), "text_text": len(pairs), "text_body": len(on_body),
            "text_outside": len(outside), "examples": {"text_text": pairs[:8], "text_body": on_body[:8],
                                                       "text_outside": outside[:8]}}


def load_variant(spec: str):
    """``rigs/x.json`` or ``rigs/x.json@small`` (room and positions scaled to 60 %)."""
    from livereact.fixtures.geometry import Point3, Venue
    from livereact.fixtures.rig import load_rig
    path, _, variant = spec.partition("@")
    rig = load_rig(path)
    if variant == "small":
        k = 0.6
        v = rig.venue
        if v is None:
            raise ValueError(f"{path}: @small needs a rig with a venue")
        rig.venue = Venue(v.width_m * k, v.length_m * k, v.height_m,
                          {n: Point3(p.x * k, p.y * k, p.z) for n, p in v.targets.items()})
        for fx in rig.fixtures:
            if fx.position is not None:
                fx.position = Point3(fx.position.x * k, fx.position.y * k, fx.position.z)
        rig.refresh_geometry()
    elif variant:
        raise ValueError(f"unknown variant {variant!r}")
    return rig


def snapshot(spec: str, out: Path | None, size=(1280, 800)) -> dict:
    from livereact.ui.venue_tab import VenueTab
    app = qapp()
    rig = load_variant(spec)
    tab = VenueTab(rig, None, on_pins=None, active=False)
    tab.resize(*size)
    tab.show()
    app.processEvents()
    tab.plan.refresh_layout()
    app.processEvents()
    rep = overlap_report(tab.plan)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        name = Path(spec.split("@")[0]).stem + ("@" + spec.split("@")[1] if "@" in spec else "")
        png = out / f"{name}.png"
        tab.grab().save(str(png))
        rep["png"] = str(png)
    tab.close()
    tab.deleteLater()
    app.processEvents()
    return rep


def default_specs() -> list[str]:
    specs = [str(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / "rigs").glob("*.json"))]
    if (ROOT / "rigs" / "junior_crib.json").exists():
        specs.append("rigs/junior_crib.json@small")
    return specs


def run(specs: list[str], out: Path | None) -> dict[str, dict]:
    cwd = os.getcwd()
    os.chdir(ROOT)          # rig paths and the packaged default rig resolve from the repo root
    try:
        return {s: snapshot(s, out) for s in specs}
    finally:
        os.chdir(cwd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "results" / "agent" / dt.date.today().strftime("%Y%m%d") / "ui"))
    ap.add_argument("--rigs", nargs="*", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--src", default=None,
                    help="measure the livereact package under this src/ instead (gate_ui.py: harness's worktree)")
    a = ap.parse_args(argv)
    if a.src:
        sys.path.insert(0, str(Path(a.src).resolve()))
    res = run(a.rigs or default_specs(), None if a.no_png else Path(a.out))
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"{'rig':40} text  text/text  text/body  outside")
        for s, r in res.items():
            print(f"{s:40} {r['text_items']:4}  {r['text_text']:9}  {r['text_body']:9}  {r['text_outside']:7}")
            for k in ("text_text", "text_body", "text_outside"):
                if r["examples"][k]:
                    print(f"    {k}: {r['examples'][k][:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
