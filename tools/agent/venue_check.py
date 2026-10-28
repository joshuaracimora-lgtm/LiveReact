"""Does the Venue grid actually drive the lights? The UI agent's correctness check.

    python tools/agent/venue_check.py [--rigs rigs/a.json ...] [--json]

Invariants that must hold on every rig with a venue (exit 1 if any fails):

1. **aim round trip** -- for each placed moving head, calibrated on ``dj_booth`` (one-point capture,
   as the Venue tab's SET AIM does), ``geometry.aim`` at every target and back through
   ``pan_tilt_to_angles`` points at that target (within ``AIM_TOL_DEG``), and a floor-level target's
   beam lands on it (within ``FLOOR_TOL_M``).
2. **edits are live** -- moving a calibrated head with the Venue tab's own X box changes the very next
   render of an aim scene (BreakSingleSpotSpecial at ``dj_booth``) to exactly the new geometry's aim.
3. **side follows the grid** -- dragging an uncalibrated head across the room's centre line with the
   X box flips its ``side``, and the mirrored sweep (DropStrobeStab) follows.

Plus an inventory (informational): which scenes read the grid (aim at targets / anchors), which only
use ``side`` (mirroring), which ignore the grid. Josh chose (2026-10-26) to verify and fix these links,
not to make every sweep grid-aware; the inventory is how he sees which looks the grid can't move.

Lives under tools/: the UI agent fixes the app, never the check that judges it.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

AIM_TOL_DEG = 0.25
FLOOR_TOL_M = 0.05
CAPTURE = (0.42, 0.61)           # the raw pan/tilt "seen" on dj_booth for the synthetic capture


def _heads(rig):
    from livereact.fixtures.profiles import MovingHead
    return [fx for fx in rig.fixtures if isinstance(fx, MovingHead)]


def _calibrate(fx, rig):
    from livereact.fixtures.geometry import calibrate_from_capture
    fx.calibration = calibrate_from_capture(fx.position, fx.facing_deg, rig.venue.targets["dj_booth"],
                                            CAPTURE[0], CAPTURE[1], fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG,
                                            target_name="dj_booth")


def check_round_trip(rig) -> list[str]:
    from livereact.fixtures.geometry import aim, beam_floor_hit, bearing, pan_tilt_to_angles, wrap_deg
    fails = []
    for fx in _heads(rig):
        if fx.position is None:
            continue
        _calibrate(fx, rig)
        for name, tgt in rig.venue.targets.items():
            p, t, ok = aim(fx.position, fx.facing_deg, tgt, fx.calibration, fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
            if not ok:
                continue          # out of the head's travel: nothing to round-trip
            az, el = pan_tilt_to_angles(p, t, fx.facing_deg, fx.calibration, fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
            want_az, want_el = bearing(fx.position, tgt)
            if abs(wrap_deg(az - want_az)) > AIM_TOL_DEG or abs(el - want_el) > AIM_TOL_DEG:
                fails.append(f"{fx.label} -> {name}: aims at az {az:.2f}/el {el:.2f}, target is "
                             f"{want_az:.2f}/{want_el:.2f}")
                continue
            if tgt.z < 0.01:
                hit = beam_floor_hit(fx.position, az, el)
                if hit is None or ((hit.x - tgt.x) ** 2 + (hit.y - tgt.y) ** 2) ** 0.5 > FLOOR_TOL_M:
                    fails.append(f"{fx.label} -> {name}: beam lands at {hit}, target at {tgt}")
    return fails


def _render_pan_tilt(scene_cls, rig, fx, **snap):
    from livereact.core.show_state import ShowSnapshot
    from livereact.fixtures.profiles import _read16
    c = scene_cls().render(rig, ShowSnapshot(**snap))
    return _read16(c.values, fx.offset + 0), _read16(c.values, fx.offset + 2)


def check_edits_are_live(rig_path: str) -> list[str]:
    from livereact.fixtures.geometry import aim, m_to_ft
    from livereact.fixtures.rig import load_rig
    from livereact.scenes.library import BreakSingleSpotSpecial, DropStrobeStab
    from livereact.ui.venue_tab import VenueTab
    from ui_snapshot import qapp
    qapp()
    fails: list[str] = []
    rig = load_rig(rig_path)
    tab = VenueTab(rig, None, on_pins=None, active=False)
    try:
        heads = [h for h in _heads(rig) if h.position is not None]
        if not heads:
            return [f"{rig_path}: no placed moving head to check"]
        fx = heads[0]
        key = getattr(fx, "slot_id", fx.label)
        tab.cb_fixture.setCurrentIndex(tab.cb_fixture.findData(key))
        _calibrate(fx, rig)
        before = _render_pan_tilt(BreakSingleSpotSpecial, rig, fx, energy=0.3)
        # a value the box can show (0.1 ft): this checks propagation; the box's rounding is UI_BUGS.md's
        new_x_ft = round(m_to_ft(fx.position.x) + (3.0 if fx.position.x < rig.venue.width_m / 2 else -3.0), 1)
        tab.sb_x.setValue(new_x_ft)                      # the operator's edit, through the real widget
        after = _render_pan_tilt(BreakSingleSpotSpecial, rig, fx, energy=0.3)
        want = aim(fx.position, fx.facing_deg, rig.venue.targets["dj_booth"], fx.calibration,
                   fx.PAN_RANGE_DEG, fx.TILT_RANGE_DEG)
        if abs(m_to_ft(fx.position.x) - new_x_ft) > 0.01:
            fails.append(f"{fx.label}: X box set {new_x_ft:.2f} ft, fixture at {m_to_ft(fx.position.x):.2f} ft")
        if after == before:
            fails.append(f"{fx.label}: moving it 3 ft did not change the aim scene's pan/tilt {after}")
        if abs(after[0] - want[0]) > 1e-3 or abs(after[1] - want[1]) > 1e-3:
            fails.append(f"{fx.label}: after the edit the scene aims {after}, the new geometry says {want[:2]}")
        # side follows the grid (an uncalibrated head: the sweeps' mirror is all the grid controls)
        from livereact.fixtures.geometry import HeadCalibration
        fx.calibration = HeadCalibration()
        w_ft = m_to_ft(rig.venue.width_m)
        tab.sb_x.setValue(round(w_ft * 0.2, 1))
        left_side, left = fx.side, _render_pan_tilt(DropStrobeStab, rig, fx, energy=1.0, bucket="drop",
                                                    bar=0, beat_in_bar=3)
        tab.sb_x.setValue(round(w_ft * 0.8, 1))
        right_side, right = fx.side, _render_pan_tilt(DropStrobeStab, rig, fx, energy=1.0, bucket="drop",
                                                      bar=0, beat_in_bar=3)
        if (left_side, right_side) != ("left", "right"):
            fails.append(f"{fx.label}: side did not follow the grid ({left_side} at 20 %, {right_side} at 80 %)")
        elif left == right:
            fails.append(f"{fx.label}: the mirrored sweep is identical on both sides {left}")
    finally:
        tab.close()
        tab.deleteLater()
    return fails


def inventory() -> dict[str, list[str]]:
    from livereact.scenes import library
    out = {"aims_at_targets": [], "mirrors_by_side": [], "ignores_grid": []}
    for cls in library.ALL_SCENES:
        src = inspect.getsource(cls)
        if "aim_spots(" in src or "apply_drop_movement(" in src or "anchors_for(" in src:
            out["aims_at_targets"].append(cls.name)
        elif "side" in src or "mirror" in src:
            out["mirrors_by_side"].append(cls.name)
        else:
            out["ignores_grid"].append(cls.name)
    return out


def run(rig_paths: list[str]) -> dict:
    from livereact.fixtures.rig import load_rig
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        res: dict = {"rigs": {}, "inventory": inventory()}
        for rp in rig_paths:
            rig = load_rig(rp)
            if rig.venue is None:
                continue
            res["rigs"][rp] = {"round_trip": check_round_trip(rig), "edits_are_live": check_edits_are_live(rp)}
        res["ok"] = all(not v for r in res["rigs"].values() for v in r.values())
        return res
    finally:
        os.chdir(cwd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rigs", nargs="*", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rigs = a.rigs or [str(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / "rigs").glob("*.json"))]
    res = run(rigs)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for rp, checks in res["rigs"].items():
            for name, fails in checks.items():
                print(f"{'PASS' if not fails else 'FAIL'}  {rp}  {name}")
                for f in fails:
                    print(f"      {f}")
        inv = res["inventory"]
        print(f"\nscenes that aim at grid targets ({len(inv['aims_at_targets'])}): {', '.join(inv['aims_at_targets'])}")
        print(f"scenes that only mirror by side ({len(inv['mirrors_by_side'])}): {', '.join(inv['mirrors_by_side'])}")
        print(f"scenes that ignore the grid ({len(inv['ignores_grid'])}): {', '.join(inv['ignores_grid'])}")
        print(f"\nvenue check: {'OK' if res['ok'] else 'FAILED'}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
