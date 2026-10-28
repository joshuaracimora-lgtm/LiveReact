"""Bench check for the Saturday (2026-10-31) lasers: every colour the show can send, on the real heads.

BENCH ONLY -- drives the Enttec directly like tools/laser_probe.py (no App, no finalize(), so the
arm switch and the mount-height gate are NOT in the loop). No crowd, beam path confirmed safe,
someone watching the units; the tool asks before it opens the port.

What it checks, on BOTH heads of rigs/junior_crib_fan.json at once (Laser L @75, Laser R @1):

  1. colours   the Junior Crib "Rainbow" fan (engines/laser_fan.py FAN_SCENE) with CH12 set to each
               colour the palette can produce (engines/laser.py PALETTE_TO_LASER: red, yellow, green,
               cyan, blue, purple, white) and then the scene's own colour cycle (CH12 255, the big
               drop's rainbow). Each step holds until you type what you saw; a step whose answer
               doesn't contain the expected colour is listed at the end as a MISMATCH.
  2. bursts    the fan scene switching lit <-> primed (CH1 manual <-> 0) the way the fan profile's
               2-bar moments do at --bpm, colour following the palette walk, so you can confirm the
               heads snap on and off cleanly and show the colour from the first frame.

Usage (project root, venv active, Enttec plugged in):

  python tools/laser_color_bench.py                    # colours, then bursts, prompts for each colour
  python tools/laser_color_bench.py --dwell 4          # no typing: each colour 4 s (no MISMATCH check)
  python tools/laser_color_bench.py --only colours     # or --only bursts
  python tools/laser_color_bench.py --dry-run --dwell 0.2 --yes   # no hardware: print the frames

Every colour step is appended to docs/laser_findings.md (the evidence trail laser_probe.py keeps).
Everything is blacked out on every exit path, including Ctrl-C.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from laser_probe import RATE_HZ, Sender, blackout, build_backend, confirm, log_row  # noqa: E402

from livereact.core.frame_buffer import UNIVERSE_SIZE  # noqa: E402
from livereact.engines.laser import PALETTE_TO_LASER  # noqa: E402
from livereact.engines.laser_fan import FAN_SCENE  # noqa: E402
from livereact.engines.laser_feature import laser_color_name  # noqa: E402
from livereact.fixtures.rig import load_rig  # noqa: E402

RIG = ROOT / "rigs" / "junior_crib_fan.json"
RAINBOW = FAN_SCENE[11]


def show_colours() -> list[tuple[str, int]]:
    """(name, CH12) for every laser colour a palette maps to, then the rainbow cycle."""
    seen: dict[int, str] = {}
    for ch12 in PALETTE_TO_LASER.values():
        seen.setdefault(ch12, laser_color_name(ch12))
    return sorted(((n, v) for v, n in seen.items()), key=lambda nv: nv[1]) + [("rainbow", RAINBOW)]


class Heads:
    """Both lasers' 12 bytes in one universe (laser_probe.Frame drives one address)."""

    def __init__(self, addresses: list[int]) -> None:
        self.addresses = addresses
        self.values = list(FAN_SCENE)
        self._lock = threading.Lock()

    def scene(self, ch12: int, lit: bool = True) -> None:
        with self._lock:
            self.values = [FAN_SCENE[0] if lit else 0, *FAN_SCENE[1:11], ch12]

    def universe(self) -> bytes:
        with self._lock:
            uni = bytearray(UNIVERSE_SIZE)
            for a in self.addresses:
                uni[a - 1:a - 1 + 12] = bytes(self.values)
            return bytes(uni)

    def bytes_text(self) -> str:
        with self._lock:
            return ",".join(str(v) for v in self.values) + f" @ {self.addresses}"


def colours(heads: Heads, args) -> list[str]:
    mismatches = []
    steps = show_colours()
    print(f"\n== 1. colours: {len(steps)} steps on the Rainbow fan scene, both heads")
    for i, (name, ch12) in enumerate(steps, 1):
        heads.scene(ch12)
        print(f"\n[{i}/{len(steps)}] expected: {name.upper()}   (CH12={ch12})")
        if args.dwell is not None:
            time.sleep(args.dwell)
            log_row(heads, f"colour bench: expected {name}, held {args.dwell:g} s (not observed)", not args.no_log)
            continue
        seen = input("   what colour do you see on each head? (e.g. 'red both', 'L red R pink'): ").strip()
        log_row(heads, f"colour bench: expected {name}; seen: {seen or '(nothing typed)'}", not args.no_log)
        if name not in seen.lower():
            mismatches.append(f"{name} (CH12={ch12}): saw '{seen}'")
    return mismatches


def bursts(heads: Heads, args) -> None:
    bar_s = 4 * 60.0 / args.bpm
    walk = [v for _, v in show_colours()]
    print(f"\n== 2. bursts: {args.bursts} two-bar moments at {args.bpm:g} BPM ({2 * bar_s:.1f} s lit, "
          f"{2 * bar_s:.1f} s dark), colour walking the palette")
    for i in range(args.bursts):
        ch12 = walk[i % len(walk)]
        heads.scene(ch12, lit=True)
        print(f"   burst {i + 1}: {laser_color_name(ch12) if ch12 != RAINBOW else 'rainbow'} ON")
        time.sleep(2 * bar_s)
        heads.scene(ch12, lit=False)          # primed: every channel but CH1, as the fan profile does
        print("            dark (primed)")
        time.sleep(2 * bar_s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", default=None)
    ap.add_argument("--only", choices=("colours", "bursts"), default=None)
    ap.add_argument("--dwell", type=float, default=None, help="seconds per colour; default: until you type")
    ap.add_argument("--bpm", type=float, default=124.0)
    ap.add_argument("--bursts", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the bench-only confirmation")
    args = ap.parse_args()

    rig = load_rig(str(RIG))
    lasers = rig.by_role("laser")
    addresses = sorted(fx.offset + 1 for fx in lasers)
    print(f"rig {RIG.name}: lasers {[f'{fx.slot_id}@{fx.offset + 1}' for fx in lasers]}")
    confirm(args)
    backend = build_backend(args)
    heads = Heads(addresses)
    sender = Sender(backend, heads)
    mismatches: list[str] = []
    try:
        sender.start()
        if args.only in (None, "colours"):
            mismatches = colours(heads, args)
        if args.only in (None, "bursts"):
            bursts(heads, args)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        sender.stop.set()
        sender.join(timeout=1.0)
        blackout(backend)
        print("blacked out")
    if args.dwell is None and args.only in (None, "colours"):
        print("\nMISMATCHES:" if mismatches else "\nall colours matched what you typed")
        for m in mismatches:
            print(f"  - {m}")


if __name__ == "__main__":
    main()
