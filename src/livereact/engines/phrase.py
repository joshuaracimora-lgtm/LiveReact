"""Phrase Engine — rotates scenes on inferred bar boundaries with crossfades.

Despite the name there is no real phrase *detection* (the XDJ-XZ doesn't transmit
song structure, docs/04 §4.1); this rotates the active scene on a fixed bar cadence
— exactly what the spec intends. Rules (docs/08 §4, docs/12 Phase 4):
  - rotate every ROTATION_BARS inferred bars; crossfade over CROSSFADE_BARS
  - enforce a minimum HOLD_BARS after any change (no "slot-machine" whiplash)
  - suspend starting new rotations while in a transition or a loop
  - a bar boundary fires a rotation once, not repeatedly while the bar holds
"""

from __future__ import annotations

import logging

from ..core.show_state import ShowSnapshot
from ..dynamics import musical_bar_position
from ..fixtures.profiles import ChannelKind
from ..fixtures.rig import Rig
from ..merge.merge import Contribution
from ..scenes.base import EnergyBucket, Scene
from ..scenes.catalog import SceneCatalog

log = logging.getLogger(__name__)

ROTATION_BARS = 16   # rotate the look on this cadence
CROSSFADE_BARS = 2   # crossfade duration (scheduled rotations)
BUCKET_CHANGE_CROSSFADE_BARS = 1  # a bucket-driven rotation (see _maybe_rotate): quicker, the
                                  # music already moved; DROP-family entries cut hard instead
HOLD_BARS = 4        # minimum hold after any change
# How a bucket change drives rotation (2026-09-15, third live capture,
# recordings/session-20260915-125433 replayed with the one-look-per-bucket
# core catalog). Two questions per change: did the bucket FAMILY change
# (break/outro, build, drop/peak -- the same grouping engines/palette.py
# uses), and did the rank go UP within the family (DROP -> PEAK)?
#   family change, any direction  -> rotate, bypass the hold (the classifier
#                                    already debounced it; re-gating it here
#                                    just played a break under the drop look)
#   rank up within the family     -> rotate, bypass the hold (DROP -> PEAK)
#   rank down within the family   -> nothing (PEAK -> DROP is an overlay flip
#                                    on the same drop; swapping looks on it
#                                    was pure churn, and once the crossfade
#                                    outlived the drop it put the DROP look
#                                    over a break)
# Entering the drop family is additionally a HARD CUT on the very tick it
# commits (mid-bar included): LIGHTING_BIBLE.md §5, "the cut IS the effect."
_FAMILY = {"break": "break", "outro": "break", "build": "build", "drop": "drop", "peak": "drop"}
_RANK = {"break": 0, "outro": 0, "build": 1, "drop": 2, "peak": 3}


class PhraseEngine:
    def __init__(self, catalog: SceneCatalog | None = None) -> None:
        self.catalog = catalog or SceneCatalog()
        self._current: Scene = self.catalog.default()
        self._next: Scene | None = None
        self._crossfade_start_pos = 0.0
        self._crossfade_bars = float(CROSSFADE_BARS)  # per-rotation: 0 for a DROP/PEAK hard cut
        self._last_change_bar = 0
        self._last_bar_seen = -1
        self._last_bucket_seen: str | None = None
        self._skip_requested = False
        self._bucket_rotation_requested = False  # a bucket change that must rotate, see _maybe_rotate
        self._hard_cut_requested = False         # ...and cut instead of crossfading (drop-family entry)
        self._was_in_transition = False
        self.rotations = 0  # for tests/telemetry

    # LIGHTING_BIBLE.md §2.3's Titan tiers (Bible-integration Phase 4) --
    # see engines/reactive.py's own priority comment for the full tier list.
    priority = 50

    @property
    def name(self) -> str:
        return "phrase"

    @property
    def current_scene_name(self) -> str:
        if self._next is not None:
            return f"{self._current.name} -> {self._next.name}"
        return self._current.name

    # --- UI / test hooks ---
    def skip(self) -> None:
        """Force a rotation at the next bar (still respects the minimum hold)."""
        self._skip_requested = True

    def force_scene(self, scene: Scene) -> None:
        """Jump straight to a specific scene (no crossfade) — for testing/override."""
        self._current = scene
        self._next = None

    # --- per-frame ---
    def tick(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        self._detect_settle(snap)
        self._maybe_rotate(snap, rig)
        self._advance_crossfade(snap)
        return self._render(snap, rig)

    def _detect_settle(self, snap: ShowSnapshot) -> None:
        # When a mix ends, settle into a fresh look on the next downbeat (docs/08 §4).
        if self._was_in_transition and not snap.in_transition:
            self._skip_requested = True
        self._was_in_transition = snap.in_transition

    def _maybe_rotate(self, snap: ShowSnapshot, rig: Rig) -> None:
        # A real energy-state change should prompt a rotation on its own
        # timeline, not wait for the next scheduled ROTATION_BARS boundary --
        # CONFIRMED live (2026-09-09 hardware capture, see [[livereact-project]]
        # memory): scene rotation and bucket classification were otherwise two
        # fully independent clocks, measured as long as 24s/14 bars of the calm
        # `Deep Wash Breathe` running over a real drop. See _FAMILY/_RANK
        # above for exactly which changes rotate. Checked every tick (not only
        # on a new bar) so a drop-family entry can cut on the tick it commits.
        if self._last_bucket_seen is not None and snap.bucket != self._last_bucket_seen:
            old_fam, new_fam = _FAMILY.get(self._last_bucket_seen, "break"), _FAMILY.get(snap.bucket, "break")
            if new_fam != old_fam:
                self._bucket_rotation_requested = True
                self._hard_cut_requested = new_fam == "drop"
            elif _RANK.get(snap.bucket, 0) > _RANK.get(self._last_bucket_seen, 0):
                self._bucket_rotation_requested = True
        self._last_bucket_seen = snap.bucket

        # The track plan names the look for this section (TIME-20, repeat the hook):
        # a planned look that is not showing yet rotates in like a bucket change.
        if (snap.plan_active and snap.planned_scene and self._next is None
                and self._current.name != snap.planned_scene and not self._bucket_rotation_requested
                and self._planned_scene(snap, rig) is not None):
            self._bucket_rotation_requested = True

        # Only act on a *new* bar so a boundary fires once, not every tick --
        # except a hard cut, which lands the moment it's asked for.
        if snap.bar == self._last_bar_seen and not self._hard_cut_requested:
            return
        self._last_bar_seen = snap.bar

        if self._next is not None:
            if not self._bucket_rotation_requested:
                return  # already crossfading
            # A bucket change landing mid-crossfade (2026-09-15, recordings/
            # session-20260915-125433: a scheduled rotation into a second
            # BREAK look had just started when BUILD committed, so the build
            # look waited a full crossfade + a bar -- 3.3 bars of an 8-bar
            # build under a break look) finishes the in-flight fade now and
            # rotates right away instead of waiting it out.
            self._current = self._next
            self._next = None
            self._last_change_bar = snap.bar
        if snap.in_transition or snap.loop_active:
            return  # suspend rotations during a mix or loop

        # Bible-integration gap fix (2026-09-13, found during a Phase 1-6
        # re-audit): `Scene.min_hold_bars` ("per-look override of the
        # catalog-wide HOLD_BARS," scenes/base.py's own docstring) has been
        # populated by dozens of Phase 5 scenes with real Bible-specified
        # values (e.g. BreakLowWash=16, DropBlinderClear=4, AllDark=1) but
        # was NEVER actually consulted here -- every look used the same
        # flat HOLD_BARS=4 floor regardless of what it declared. Fixed:
        # the CURRENTLY-HELD look's own min_hold_bars now gates how long it
        # must be shown before any rotation away from it, falling back to
        # the catalog-wide HOLD_BARS when a scene doesn't override it (the
        # 8 pre-Bible-integration scenes, which never set this field).
        # A bucket-driven rotation bypasses it (see _FAMILY/_RANK above):
        # the floor is an anti-whiplash guard on the SCHEDULER, and a bucket
        # change is already debounced upstream (core/metronome.py's dwell +
        # confirm) -- re-gating it here meant a real build ran its full 11s
        # under `Break Ceiling Drift` (2026-09-15, session-20260915-121503).
        hold_bars = self._current.min_hold_bars if self._current.min_hold_bars is not None else HOLD_BARS
        bars_since_change = snap.bar - self._last_change_bar
        if bars_since_change < hold_bars and not self._bucket_rotation_requested:
            return  # enforce the minimum hold

        if not (self._skip_requested or self._bucket_rotation_requested):
            # A planned look holds for its whole section: the plan, not the
            # scheduler, decides when the look changes (TIME-20).
            if snap.plan_active and snap.planned_scene and self._current.name == snap.planned_scene:
                return
            # Scheduled cadence only: while a higher-ranked look of the same
            # family is showing (Peak Full Rig after PEAK fell back to plain
            # DROP), keep it -- see the PEAK -> DROP rule above; the scheduler
            # must not do the swap the bucket rule declined.
            cur = self._current.bucket.value
            if _FAMILY.get(cur) == _FAMILY.get(snap.bucket) and _RANK.get(cur, 0) > _RANK.get(snap.bucket, 0):
                return
            # Nor inside a break: the break look holds until the bucket moves
            # (LIGHTING_BIBLE.md §9.1 "Break: slow, static hue"; break_low_wash's
            # fail mode "motion/hue churn kills the rest"). A scheduled swap there
            # starts a look that needs 16 bars (TIME-02) in a break that may end
            # first -- on the ear-verified set it was cut by the drop after 0.1-8.8
            # bars every time (SWEETER 32.8/203.7 s, messy 133.7 s). OUTRO is the
            # same family: a BREAK look under a promoted OUTRO keeps holding too
            # (the promotion is often a mid-track break, messy 136.1 s), and a
            # break-family look is still left by the bucket rule or a skip/settle.
            if _FAMILY.get(snap.bucket) == "break" and _FAMILY.get(cur) == "break":
                return
        if self._skip_requested or self._bucket_rotation_requested or bars_since_change >= ROTATION_BARS:
            if self._hard_cut_requested:
                crossfade = 0.0
            elif self._bucket_rotation_requested:
                crossfade = float(BUCKET_CHANGE_CROSSFADE_BARS)
            else:
                crossfade = float(CROSSFADE_BARS)
            self._bucket_rotation_requested = False
            self._hard_cut_requested = False
            self._begin_rotation(snap, rig, crossfade_bars=crossfade)

    def _planned_scene(self, snap: ShowSnapshot, rig: Rig | None) -> Scene | None:
        """The track plan's look for this section, when it exists in the catalog, is
        eligible on this rig and belongs to the family the bucket is showing."""
        if not snap.plan_active or not snap.planned_scene:
            return None
        scene = self.catalog.find(snap.planned_scene)
        if scene is None or not self.catalog.eligible(scene, rig):
            return None
        if scene.bucket.value != snap.bucket:
            return None   # e.g. PEAK showing: the catalog's PEAK look, not the drop hook (the big moment looks bigger)
        return scene

    def _begin_rotation(self, snap: ShowSnapshot, rig: Rig, crossfade_bars: float = float(CROSSFADE_BARS)) -> None:
        # snap.bucket -- an ALREADY-DEBOUNCED, already-adaptively-rescaled
        # bucket_for_energy(energy_norm), computed once in metronome.py (see
        # BUCKET_MIN_DWELL_S there), not a fresh call here. Same reasoning as
        # reactive.py and engines/laser.py: one shared notion of "what bucket
        # is this," not independently (and previously drift-prone) recomputed
        # per engine (found live 2026-09-07, see show_state.py's bucket
        # docstring).
        bucket = EnergyBucket(snap.bucket)
        # `rig` threaded through (Bible-integration Phase 1) so pick() can
        # apply requires{}/min_atmosphere_density capability gating -- a
        # no-op for all 8 pre-Phase-1 scenes (empty requires, 0.0 atm
        # requirement, always satisfied), real once Phase 5 adds looks that
        # actually declare requires{}.
        candidate = self._planned_scene(snap, rig) or self.catalog.pick(bucket, rig, exclude=self._current)
        self._skip_requested = False
        if candidate is self._current:
            # The only eligible look for this bucket is the one already
            # showing (the core catalog has exactly one for BUILD/DROP/PEAK,
            # scenes/library.py's CORE_SCENES) -- a crossfade into itself is
            # a no-op that would only reset the hold clock. Stay put.
            return
        self._next = candidate
        self._crossfade_start_pos = musical_bar_position(snap)
        self._crossfade_bars = crossfade_bars
        self.rotations += 1
        log.info("Scene rotate -> %s (bucket=%s%s)", self._next.name, bucket.value, ", hard cut" if crossfade_bars <= 0 else "")

    def _advance_crossfade(self, snap: ShowSnapshot) -> None:
        if self._next is None:
            return
        if self._progress(snap) >= 1.0:
            self._current = self._next
            self._next = None
            self._last_change_bar = snap.bar

    def _progress(self, snap: ShowSnapshot) -> float:
        if self._crossfade_bars <= 0.0:
            return 1.0  # hard cut
        elapsed = musical_bar_position(snap) - self._crossfade_start_pos
        return max(0.0, min(1.0, elapsed / self._crossfade_bars))

    def _render(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        if self._next is None:
            return self._current.render(rig, snap, 1.0)
        progress = self._progress(snap)
        out_c = self._current.render(rig, snap, 1.0 - progress)
        in_c = self._next.render(rig, snap, progress)
        return self._combine(out_c, in_c, progress, rig)

    @staticmethod
    def _combine(out_c: Contribution, in_c: Contribution, progress: float, rig: Rig) -> Contribution:
        """Blend two scene contributions: brightness/color/movement crossfade
        by real weighted interpolation; only laser channels switch at the
        midpoint (a laser mode/pattern value is a discrete enum -- landing on
        an undefined intermediate index mid-lerp is a real, physical risk,
        not just cosmetic).

        BUG FIX (2026-09-13, found via the first real --hardware --record
        session): the brightness/color branch used to take a raw per-channel
        `max(out, in)` -- the EXACT anti-pattern Bible-integration Phase 4
        fixed at the cross-ENGINE level (`merge/merge.py`'s `merge_
        contributions`, now genuine LTP-by-priority) but never touched HERE,
        the cross-SCENE blend within Phrase's own 2-bar crossfade. Confirmed
        live: crossfading from `Peak Full Rig` into `Outro Fade Wash`
        produced a color that was neither scene's real output and stayed
        frozen at that wrong value through the ENTIRE crossfade and past it
        (max() of two fixed/near-fixed values is itself fixed, so there was
        no visible fade at all, just an instant wrong-color jump) -- read
        live as "the program just cuts to a new color abruptly." Now a real
        weighted lerp between the two scenes' own values, matching
        `progress` -- still plain linear RGB, not perceptually-uniform Oklab
        (LIGHTING_BIBLE.md's own C3 correction already discloses this
        codebase hasn't adopted Oklab anywhere; not introduced here either,
        out of scope for a bug fix) -- but a genuine, smooth interpolation
        instead of a frozen wrong value.

        SECOND BUG FIX, same session, same real hardware evidence: MOVEMENT
        was grouped with LASER under the same "snap at the midpoint" rule,
        on the theory that "blending a pan value sweeps through center" was
        undesirable. Confirmed live this reads as far worse than a sweep:
        the moving head visibly stops dead for the first half of every
        crossfade, then instantly snaps to the new scene's position at the
        exact midpoint -- "it stops, then just cranks into position,"
        directly contradicting LIGHTING_BIBLE.md's own §6.6 slew-rate-limit
        intent (no instant jumps). Unlike a laser's discrete mode/pattern
        index, pan/tilt are continuous analog values with no "undefined
        intermediate" to land on -- every scene in this codebase already
        computes them as an ordinary continuous function, so lerping between
        two computed targets during a crossfade is no riskier than what a
        single scene's own sine sweep already does every tick. MOVEMENT now
        gets the same real interpolation as color/dimmer; LASER alone keeps
        the snap-at-midpoint."""
        result = Contribution()
        for ch in range(len(result.values)):
            ot, it = out_c.touched[ch], in_c.touched[ch]
            if not ot and not it:
                continue
            kind = rig.kind_at(ch)
            if kind == ChannelKind.LASER:
                if it and progress >= 0.5:
                    value = in_c.values[ch]
                elif ot:
                    value = out_c.values[ch]
                else:
                    value = in_c.values[ch]
            elif ot and it:
                value = round(out_c.values[ch] * (1.0 - progress) + in_c.values[ch] * progress)
            else:
                value = out_c.values[ch] if ot else in_c.values[ch]
            result.set(ch + 1, value)
        return result
