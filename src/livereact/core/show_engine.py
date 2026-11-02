"""ShowEngine: snapshot -> engines -> merge -> publish, at ~60 Hz.

The conductor. Each tick it reads one ShowState snapshot, runs every engine under
its own try/except (a crashing engine is skipped, reusing its previous contribution
— a Phrase Engine bug must never stop the Reactive Layer), merges the contributions,
applies manual params + safety, and publishes the finished frame to the double
buffer. ``freeze`` simply skips publishing, so the writer holds the last good frame
(docs/05 §5.1, §5.3).
"""

from __future__ import annotations

import logging
import threading

from ..clock import Clock, LoopTimer
from ..fixtures.rig import Rig
from ..merge.merge import (
    Contribution,
    ManualParams,
    finalize,
    merge_contributions,
    merge_contributions_ex,
)
from .frame_buffer import UNIVERSE_SIZE
from ..scenes.base import SAFE_DEFAULT
from .frame_buffer import DmxFrameBuffer
from .show_state import ShowState
from .silence import SilenceDetector
from ..engines.reactive import flash_colour
from ..engines.tease import TeaseLook, apply_tease

log = logging.getLogger(__name__)


def apply_strobe_colour(merged: bytearray, rig, rgb: tuple[float, float, float]) -> None:
    """D-36: while the reactive layer's strobe is in its ON phase, every wash fixture's colour is the
    flash colour (engines/reactive.py flash_colour: the song's palette lifted toward white by the
    song's brightness). Applied after the merge because COLOR is LTP by priority and the Phrase look
    would otherwise keep the flash in the plain wash colour."""
    c = Contribution()
    r, g, b = rgb
    for fx in rig.by_role("wash"):
        fx.set_color(c, r, g, b, min(r, g, b))   # the bar's centre-white zone follows the white share
    for i in range(UNIVERSE_SIZE):
        if c.touched[i]:
            merged[i] = c.values[i]


def apply_planned_look(merged: bytearray, rig, look: str, rgb: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> None:
    """A look-note cue's override of the merged frame (rekordbox/look_notes.py `cues`, 2026-10-29).

    Josh's Thriller ear notes: "drums cut right before the drop: straight to white out", "quiet spot
    between the roll and the main groove: strobe -> white out". Applied AFTER the engines' merge
    and BEFORE finalize, like the Venue-tab pins, so master brightness, the blackout switch, the
    slew limit and every laser clamp still apply; the lasers themselves are not touched.
    ``whiteout``: every wash and spot fixture at full intensity in ``rgb`` -- since D-36 the song's
    flash colour (engines/reactive.py flash_colour), not pure white: Josh on the hold after Thriller's
    roll, "it went from orange to white really quickly, which didn't really feel right". The bar's
    centre-white zone follows the white share; the strobe's dimmer toggle is overwritten with full."""
    if look != "whiteout":
        return
    c = Contribution()
    r, g, b = rgb
    for fx in list(rig.by_role("wash")) + list(rig.by_role("spot")):
        fx.set_color(c, r, g, b, min(r, g, b))
        fx.set_intensity(c, 1.0)
    for i in range(UNIVERSE_SIZE):
        if c.touched[i]:
            merged[i] = c.values[i]


class ShowEngine(threading.Thread):
    def __init__(
        self,
        show_state: ShowState,
        rig: Rig,
        frame_buffer: DmxFrameBuffer,
        engines: list,
        manual: ManualParams,
        clock: Clock,
        stop_event: threading.Event,
        hz: float = 60.0,
        phrase_engine=None,
        bible_failsafe_enabled: bool = False,
        laser_engine=None,
    ) -> None:
        super().__init__(name="show-engine", daemon=False)
        self.show_state = show_state
        self.rig = rig
        self.frame_buffer = frame_buffer
        self.engines = engines
        self.manual = manual
        self.clock = clock
        self.stop_event = stop_event
        self.hz = hz
        # Named reference for the console/recorder (the active look's name).
        # Optional/defaults None so a bare ShowEngine without a PhraseEngine
        # (e.g. a minimal test harness) still works.
        self.phrase_engine = phrase_engine
        # engines/laser.py's LaserEngine -- the one authority over the laser
        # channels (2026-09-15). It must ALSO be in `engines` (that's where it
        # renders); this reference is for the finalize gate below, which
        # reads its `.active` after the engines have ticked. None (e.g. a
        # minimal test harness) means the gate stays closed: no laser.
        self.laser_engine = laser_engine
        # Bible-integration Phase 1, LOOK-08/FAIL-04, gig-safe migration flag
        # (docs/bible_integration/, /Users/joshuaracimora/.claude/plans/
        # adaptive-squishing-pebble.md): off by default so the old
        # instant-hard-zero auto_blackout failsafe stays selectable until
        # explicitly enabled and approved for real hardware use. When True,
        # auto_blackout renders SAFE_DEFAULT instead of total darkness.
        self.bible_failsafe_enabled = bible_failsafe_enabled
        self._last: dict[str, Contribution] = {}
        self._swap_lock = threading.Lock()
        self._pending_swap: tuple | None = None   # (rig, engines, laser_engine) from replace_rig
        self._last_tick_t: float | None = None  # for dt -> movement slew-limiting (LIGHTING_BIBLE.md §6.6)
        self.silence = SilenceDetector(clock)  # docs/14 §2; .active exposed for the console
        self.tease = TeaseLook()  # P-24: the Tease's level/colour/punch state; .level exposed for the recorder

    def run(self) -> None:  # pragma: no cover - thin wrapper around tick()
        timer = LoopTimer(self.clock, self.hz)
        try:
            while not self.stop_event.is_set():
                self.tick()
                timer.wait()
        except Exception:  # noqa: BLE001
            log.exception("ShowEngine loop crashed")

    def replace_rig(self, rig: Rig, engines: list, laser_engine=None) -> None:
        """Swap the rig and the engine list at the start of the next tick (App.switch_rig, 2026-10-30).
        Thread-safe: the console calls this while the show thread runs. Every frame is built from
        an empty universe, so the old rig's channels go dark on the first frame of the new one."""
        with self._swap_lock:
            self._pending_swap = (rig, list(engines), laser_engine)

    def tick(self) -> bytes | None:
        """Run one frame. Returns the published universe, or None if frozen."""
        with self._swap_lock:
            swap, self._pending_swap = self._pending_swap, None
        if swap is not None:
            self.rig, self.engines, self.laser_engine = swap
            self._last = {}
        snap = self.show_state.snapshot()

        contributions: list[Contribution] = []
        for engine in self.engines:
            try:
                contribution = engine.tick(snap, self.rig)
                # Bible-integration Phase 4: tag with the producing engine's
                # own Titan-tier priority (see engines/reactive.py's own
                # comment) so merge_contributions' LTP-by-priority resolution
                # has something real to sort on, not an accident of this
                # list's own order.
                contribution.priority = engine.priority
                self._last[engine.name] = contribution
            except Exception:  # noqa: BLE001 - isolate a faulty engine
                log.exception("Engine %r failed; reusing last contribution", engine.name)
                contribution = self._last.get(engine.name)
            if contribution is not None:
                contributions.append(contribution)

        if self.manual.freeze:
            return None  # hold the last published frame

        now = self.clock.monotonic()
        dt = (now - self._last_tick_t) if self._last_tick_t is not None else 1.0 / self.hz
        self._last_tick_t = now

        auto_blackout = self.silence.update(snap)
        # 2026-10-05: Venue-tab calibration pins mean an operator is at the
        # console jogging a REAL fixture (TESTING.md Test 5) -- usually with
        # no music, which is exactly when the no-music failsafe would black
        # the head they are trying to see ("when I go to the moving head,
        # it doesn't turn on, so I can't calibrate it" -- Josh). The pins
        # suppress auto-blackout; the operator's own blackout switch
        # (manual.blackout, finalize) still wins outright, and the silence
        # detector keeps updating so it re-engages the instant the pins clear.
        if self.manual.calibration is not None:
            auto_blackout = False
        laser_on = bool(self.laser_engine.active) if self.laser_engine is not None else False
        safe_default_frame = None
        if self.bible_failsafe_enabled and auto_blackout:
            safe_default_frame = merge_contributions([SAFE_DEFAULT.render(self.rig, snap, 1.0)], self.rig)
        merged, absolute = merge_contributions_ex(contributions, self.rig)
        # Venue-tab calibration pins (merge/merge.py's CalibrationPins,
        # 2026-09-18): overwrite the merged frame BEFORE finalize, so every
        # safety clamp and the slew limit still apply to a pinned fixture.
        pins = self.manual.calibration
        pinned: frozenset[int] = frozenset()
        if pins is not None:
            for ch, value in pins.channels.items():
                if 1 <= ch <= UNIVERSE_SIZE:
                    merged[ch - 1] = 0 if value < 0 else 255 if value > 255 else int(value)
            for slot in pins.absolute_slots:
                try:
                    off = self.rig.fixture(slot).offset
                    absolute = absolute | {off}
                    pinned = pinned | {off}
                except KeyError:
                    pass
            laser_on = laser_on or pins.laser_open
        tint = flash_colour(snap.palette, snap.planned_flash_white)
        strobe_on = False
        for engine in self.engines:
            if getattr(engine, "strobe_on", False) and getattr(engine, "flash_rgb", None) is not None:
                apply_strobe_colour(merged, self.rig, engine.flash_rgb)   # D-36
                strobe_on = True
                break
        tease = self.tease.update(snap)
        if tease is not None:
            level, rgb, hold = tease
            apply_tease(merged, self.rig, level, rgb, strobe_on=strobe_on, hold=hold)   # P-24
        if snap.planned_look:
            apply_planned_look(merged, self.rig, snap.planned_look, tint)
        frame = finalize(merged, self.rig, self.manual, auto_blackout, laser_on, dt,
                         safe_default_frame, snap.drop_pending, absolute_movement=absolute,
                         pinned_movement=pinned)
        self.frame_buffer.publish(frame)
        return frame
