"""Laser FAN profile (2026-10-28, for the 2026-10-31 event): the Junior Crib
fan, fired by the music instead of the Show tab's buttons.

Josh: "the laser fan (ones we had at Junior crib) ... we should just stick
with the pattern we have now, and we can switch up the colors based on the
frequency of the sound that triggers the lasers", then: "make sure lasers
are also only cued AFTER the drop, and shouldnt necessarily fire after every
drop".

WHEN: engines/laser_trigger.py's moments (dark by default, 2-bar moments,
MIN_GAP_BARS spacing, DUTY_MAX duty, J hit / K cue exempt), with the auto
triggers narrowed by the criteria below. Every auto trigger is already
inside DROP/PEAK (the drop has landed); on top of that:

  plan    a track plan that forbids the laser here (intro / outro) refuses it,
          and so does one whose section here is not a drop (rekordbox says
          build / verse while the energy already reads DROP: not after the drop)
  hit     the drop must be a big one: drop_hit_intensity >= DROP_MIN_HIT
          (first drops measured 1.0, second drops 0.88-0.97 on the
          2026-09-15/16 captures), so softer drops pass without a laser

Each refused or fired auto trigger is logged in ``candidates`` (time, bar,
trigger, hit, the three band envelopes, the color it would get, and the
verdict) -- tools/agent/laser_report.py turns that into the per-drop table
Josh reviews to tune these criteria. First-guess numbers, not measured.

WHAT: the Junior Crib scene bytes verbatim (FAN_SCENE, laser_show.py's
"Rainbow", the manual profile's default), except CH12: the show's palette.
Josh, 2026-10-28: "approve D-23 and have the lasers follow the palette".
D-23 (docs/13_DECISION_LOG.md) ties the colour scheme to the track's overall
frequency profile from the rekordbox colour waveform (high = bright, low =
dark, middle = blue; section_classes.json palette_by_profile), so the lasers
take the snapshot's palette through engines/laser.py's PALETTE_TO_LASER --
the colour the wash and spots are showing -- picked when a moment opens and
held for the whole moment. Simulated on 30 tracks before the switch: lasers
on the lights' palette matched the other fixtures on 70 of 71 bursts; a
colour picked from the live audio alone clashed on 22.

WHICH SCENE (2026-10-29): a track's look notes (rekordbox/look_notes.py) can name the
look -- ``laser_scene`` for every moment of the track, ``laser_cues`` for one kind of
moment (big_moment / drop / peak / wub / hit / cue). The planner carries them into the
plan, the metronome into the snapshot (planned_laser_scene / planned_laser_cues), and a
moment picks its scene when it opens, held to the end like the colour: the trigger's
cue, else the section's scene (the big moment's cue or the track's), else Rainbow. The
scenes themselves live in scenes/laser_scenes.json (engines/laser_scenes.py: the laser's
channel dictionary describes, checks and composes them). A scene marked colour
"palette" still takes CH12 from the show palette; "fixed" keeps its own byte.
"""
from __future__ import annotations

from ..clock import Clock
from ..core.show_state import ShowSnapshot
from ..fixtures.profiles import Laser
from ..scenes.base import EnergyBucket
from ..fixtures.rig import Rig
from ..merge.merge import Contribution, ManualParams
from .envelope import NORMALIZE_TAU_S, BandEnvelope
from .laser import laser_color_for_palette
from .laser_feature import laser_color_name
from .laser_scenes import LaserScene, get_scene
from .laser_show import LASER_SCENES
from .laser_trigger import (AUTO_TRIGGERS, MIN_GAP_BARS, MOOD_READY, TRIGGER_CUE, TRIGGER_DROP, TRIGGER_PEAK,
                            LaserTriggerEngine)

FAN_SCENE_NAME = "Rainbow"
FAN_SCENE = LASER_SCENES[FAN_SCENE_NAME]
DROP_MIN_HIT = 0.95              # a drop must hit at least this hard to fire the lasers
SECTION_END_GUARD_BEATS = 4      # no new auto moment in the last bar of a planned section (D-35)

# Latched override looks (2026-10-31, AEPI rig, Josh: "a rainbow green and white laser override
# button ... when I click this button, I have to reque the automatic lasers again if I want them
# back"). Only rigs listing them in `laser_overrides` (fixtures/rig.py) show the buttons, and only
# those rigs' engines honour ManualParams.laser_override / laser_auto_off. Each is the Junior Crib
# fan scene with CH12 fixed: Rainbow keeps the QLC+ scene's own 255 (colour follows the painting --
# the multicolour fan Josh saw at Junior Crib), Green / White the verified single-colour bytes.
# Lit like a held K cue: still behind the arm switch and LASER-12 (dark without music).
OVERRIDE_COLORS = {"Rainbow": get_scene(FAN_SCENE_NAME).values[11],
                   "Green": Laser.COLOR_GREEN, "White": Laser.COLOR_WHITE}


class LaserFanEngine(LaserTriggerEngine):
    """LaserTriggerEngine's timing, the Junior Crib scene, the show's palette as its colour."""

    def __init__(self, clock: Clock, manual: ManualParams | None = None) -> None:
        super().__init__(clock, manual=manual)
        self._mid = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self._high = BandEnvelope(clock, normalize_tau_s=NORMALIZE_TAU_S)
        self.mid = 0.0
        self.high = 0.0
        self._picked_for: float | None = None    # the moment start the colour was picked for
        self._noted: set[tuple[str, int]] = set()  # (trigger, bar) already in candidates
        self._pending: dict | None = None        # this tick's candidate, verdict still open
        self.candidates: list[dict] = []
        self._after_roll = ""                    # a drop edge seen inside a planned roll, fired when it ends
        self.color = self.color_l = self.color_r = Laser.COLOR_WHITE
        self.color_name = "white"
        self._ch12 = Laser.COLOR_WHITE
        # 2026-10-29: a track's look notes pick the scene (rekordbox/look_notes.py laser_scene /
        # laser_cues -> planner -> snapshot planned_laser_scene / planned_laser_cues). Chosen when a
        # moment opens and held for the whole moment, like the colour.
        self.scene_name = FAN_SCENE_NAME
        self._scene: LaserScene = get_scene(FAN_SCENE_NAME)
        self._offered: tuple[str, ...] = ()      # this tick's rig.laser_overrides

    def _override(self) -> str:
        """The latched override look, when this rig offers it ("" = none)."""
        name = str(getattr(self.manual, "laser_override", "")) if self.manual is not None else ""
        return name if name in self._offered and name in OVERRIDE_COLORS else ""

    def _auto_off(self) -> bool:
        """An override was pressed and Resume auto has not been: no automatic moments."""
        return bool(self._offered and getattr(self.manual, "laser_auto_off", False))

    @property
    def manual_cued(self) -> bool:
        return super().manual_cued or bool(self._override())

    def _refusal(self, snap: ShowSnapshot) -> str:
        if snap.plan_active and not snap.planned_laser_allowed:
            return "plan forbids"
        if snap.plan_active and snap.section_kind and snap.section_kind != "drop":
            return f"plan says {snap.section_kind}"   # the energy reads DROP, the track's phrases don't yet
        if float(snap.drop_hit_intensity) < DROP_MIN_HIT:
            return "soft drop"
        if snap.plan_active and 0 <= snap.beats_to_next_section < SECTION_END_GUARD_BEATS:
            return "section ends"   # D-35: a moment opened here would run into the next section
        return ""

    def _auto_trigger(self, now: float, snap: ShowSnapshot) -> str:
        if self._auto_off():
            self._pending = None
            self._after_roll = ""
            return ""
        fired = super()._auto_trigger(now, snap)
        self._pending = None
        # A planned snare roll is the start of the drop (D-21), but the fan belongs to the bass hit
        # after it (2026-10-28, Drop Snare Roll Ramp): a drop/peak edge inside the roll waits for its end.
        if snap.planned_roll_progress >= 0.0:
            if fired in (TRIGGER_DROP, TRIGGER_PEAK) and not self.active:
                self._after_roll = fired
            return ""
        if self._after_roll:
            if not fired and EnergyBucket(snap.bucket) in (EnergyBucket.DROP, EnergyBucket.PEAK):
                fired = self._after_roll
            self._after_roll = ""
        if not fired or self.active:
            return fired   # lit already: the base class refuses it; nothing to log
        key = (fired, int(snap.bar))
        new = key not in self._noted
        if new:
            self._noted.add(key)
            self._pending = {"t": round(now, 2), "bar": int(snap.bar), "trigger": fired,
                             "bucket": str(snap.bucket).upper(), "hit": round(float(snap.drop_hit_intensity), 3),
                             "low": round(self.wub, 3), "mid": round(self.mid, 3), "high": round(self.high, 3),
                             "palette": snap.palette, "color": laser_color_name(laser_color_for_palette(snap.palette))}
        why = self._refusal(snap)
        if why:
            self._close_pending(why)
            return ""
        return fired

    def _auto_allowed(self, now: float, snap: ShowSnapshot) -> bool:
        ok = super()._auto_allowed(now, snap)
        if not ok:
            spaced = now - self._last_end < MIN_GAP_BARS * self._bar_s(snap)
            self._close_pending("too soon after the last" if spaced else "duty limit")
        else:
            self._close_pending("fired")
        return ok

    def _close_pending(self, verdict: str) -> None:
        # drop and peak edges are always logged; a wub only when it fires (it re-arms every tick)
        if self._pending is not None and (verdict == "fired" or self._pending["trigger"] in (TRIGGER_DROP, TRIGGER_PEAK)):
            self._pending["verdict"] = verdict
            self.candidates.append(self._pending)
        self._pending = None

    def tick(self, snap: ShowSnapshot, rig: Rig) -> Contribution:
        # the envelopes must be current before the base class reads triggers this tick
        self.mid = self._mid.update(snap.audio_band_mid_abs, snap.audio_fresh)
        self.high = self._high.update(snap.audio_band_high_abs, snap.audio_fresh)
        self._offered = tuple(getattr(rig, "laser_overrides", ()))
        return super().tick(snap, rig)

    def _scene_for(self, snap: ShowSnapshot, trigger: str) -> LaserScene:
        """The look notes' pick for this moment: the trigger's own cue, else the section's scene
        (the big moment's cue or the track's laser_scene), else the fan's Rainbow."""
        name = ""
        if snap.plan_active:
            name = dict(snap.planned_laser_cues).get(trigger, "") or snap.planned_laser_scene
        try:
            return get_scene(name) if name else get_scene(FAN_SCENE_NAME)
        except ValueError:            # a scene renamed after the notes were written: the default, never dark
            return get_scene(FAN_SCENE_NAME)

    def _write(self, c: Contribution, snap: ShowSnapshot, rig: Rig) -> None:
        if (self.active and self.trigger in AUTO_TRIGGERS and snap.plan_active
                and (not snap.planned_laser_allowed
                     or EnergyBucket(snap.bucket) not in (EnergyBucket.DROP, EnergyBucket.PEAK))):
            # a moment running into the plan's laser-free edge, or out of the drop it belongs to
            # (D-35, run 4: a wub moment opened a beat before the build and lit 2 bars of it), stops here
            self._end_moment(self.clock.monotonic())
            self.mood, self.primed, self.source = MOOD_READY, True, ""
        look = self._override()
        if self.active and look and self.trigger == TRIGGER_CUE:
            # every tick, so switching Green -> White on the panel lands at once
            self._picked_for = self._moment_start
            self._scene = get_scene(FAN_SCENE_NAME)
            self.scene_name = f"{FAN_SCENE_NAME} ({look})"
            self._ch12 = OVERRIDE_COLORS[look]
            self.color_name = look.lower()
        elif self.active and self._picked_for != self._moment_start:
            self._picked_for = self._moment_start
            self._scene = self._scene_for(snap, self.trigger)
            self.scene_name = self._scene.name
            # CH12: the show palette (D-23) unless the scene fixes its own colour
            self._ch12 = laser_color_for_palette(snap.palette) if self._scene.follows_palette else self._scene.values[11]
            self.color_name = laser_color_name(self._ch12)
            if self.candidates and self.candidates[-1].get("verdict") == "fired":
                self.candidates[-1]["scene"] = self.scene_name
        elif not self.active:
            # primed between moments with the section's scene, so the unit already holds the
            # right pattern when CH1 goes to manual (the 2026-09-16 priming finding)
            self._scene = self._scene_for(snap, "")
            self.scene_name = self._scene.name
        ch12 = self._ch12
        self.color = self.color_l = self.color_r = ch12
        values = self._scene.values[:11] + (ch12,)
        for fx in rig.by_role("laser"):
            fx.set_scene(c, values, lit=self.active)
