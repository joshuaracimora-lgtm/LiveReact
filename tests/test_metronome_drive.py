"""Drive mode: the track plan decides the bucket on the live beat counter; count-in, restraint,
palette pinning, and the fallback to the classifier when the plan loses authority."""

from __future__ import annotations

import queue
import threading

from livereact.clock import FakeClock
from livereact.core.messages import AudioLevelMessage, BeatMessage, DeckStatusMessage, TrackPhraseMessage
from livereact.core.metronome import DECK_STATUS_STALE_AFTER, PLAN_HOLD_AFTER_STALE_S, Metronome
from livereact.core.show_state import ShowState
from livereact.engines import palette as palette_mod
from livereact.rekordbox.planner import plan_track
from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
from livereact.scenes.base import EnergyBucket

BPM = 120.0
BEAT_S = 60.0 / BPM


def _table(spec=None, end=241):
    # intro 1-17, up 17-49, chorus 49-81, down 81-113, up 113-145, chorus 145-209, outro 209-241
    spec = spec or [(1, 1, "intro"), (17, 2, "up"), (49, 5, "chorus"), (81, 3, "down"), (113, 2, "up"),
                    (145, 5, "chorus"), (209, 6, "outro")]
    phrases = []
    for i, (beat, kind, label) in enumerate(spec):
        nxt = spec[i + 1][0] if i + 1 < len(spec) else end
        phrases.append(Phrase(index=i + 1, beat=beat, end_beat=nxt, kind=kind, label=label))
    return PhraseTable(mood=MOOD_HIGH, end_beat=end, bank=0, phrases=tuple(phrases))


class Sim:
    """Drive a Metronome beat by beat with a synthetic XDJ status stream + audio."""

    def __init__(self, drive=True, plan=True, track_id=7, table=None):
        self.clock = FakeClock()
        self.m = Metronome(queue.Queue(), ShowState(), self.clock, threading.Event())
        self.m.phrases_mode = "drive" if drive else "shadow"
        self.table = table or _table()
        self.plan = plan_track(self.table, track_id=track_id, bpm=BPM) if plan else None
        self.m.queue.put(TrackPhraseMessage(timestamp=0.0, player=1, track_id=track_id, title="T", bpm=BPM,
                                            table=self.table, plan=self.plan, slot="sd"))
        self.track_id = track_id
        self.beat = 0
        self.beat_in_bar = 0
        self.looping = False
        self.level = 0.2
        self.snaps = []

    def status(self, *, playing=True, beat=None, looping=None):
        looping = self.looping if looping is None else looping
        play_state = "looping" if (playing and looping) else ("playing" if playing else "paused")
        return DeckStatusMessage(timestamp=self.clock.monotonic(), player=1, track_id=self.track_id, loaded_player=1,
                                 slot="sd", analyze_type="rekordbox", play_state=play_state,
                                 playing=playing, on_air=True, master=True, sync=False, bpm=BPM, pitch=1.0,
                                 beat_count=self.beat if beat is None else beat, beat_in_bar=self.beat_in_bar or 1,
                                 source_ip="x", looping=playing and looping)

    def loop(self, start, end, beats, level=None):
        """Cycle the deck's beat counter over start..end (inclusive) for `beats` beats with the
        loop flag set, the way the XZ reports a loop; release leaves the counter at the loop end."""
        self.looping = True
        length = end - start + 1
        snap = None
        for i in range(beats):
            self.beat = start + (i % length) - 1   # step_beat adds one
            snap = self.step_beat(level)
        self.looping = False
        self.beat = end
        return snap

    def step_beat(self, level=None, send_status=True):
        """Advance one beat: emit the beat packet, a status packet, audio, and tick 5x (10 Hz)."""
        self.beat += 1
        self.beat_in_bar = (self.beat - 1) % 4 + 1
        if level is not None:
            self.level = level
        t = self.clock.monotonic()
        self.m.queue.put(BeatMessage(timestamp=t, beat_in_bar=self.beat_in_bar, bpm=BPM, is_master=True, player=1))
        for i in range(5):
            t = self.clock.monotonic()
            if send_status and i % 2 == 0:
                self.m.queue.put(self.status())
            self.m.queue.put(AudioLevelMessage(timestamp=t, rms=self.level, band_low=0.5, band_mid=0.3, band_high=0.2,
                                               band_low_abs=self.level, band_mid_abs=self.level * 0.5,
                                               band_high_abs=self.level * 0.4))
            snap = self.m.tick()
            self.snaps.append(snap)
            self.clock.advance(BEAT_S / 5)
        return snap

    def run_to(self, beat, level=None, send_status=True):
        snap = None
        while self.beat < beat:
            snap = self.step_beat(level, send_status)
        return snap


def test_plan_drives_sections_on_the_beat_counter():
    sim = Sim()
    snap = sim.run_to(10, level=0.1)
    assert snap.plan_active and snap.bucket == "break" and snap.section_kind == "intro"
    assert snap.planned_scene == "Deep Wash Breathe" and not snap.planned_laser_allowed and not snap.planned_strobe_allowed
    snap = sim.run_to(20, level=0.1)                   # 'up' -> BUILD shown even with quiet audio
    assert snap.bucket == "build" and snap.classifier_bucket == "build" and snap.build_cue_source == "rekordbox"
    assert snap.planned_scene == "Rising Tide" and snap.planned_strobe_allowed
    snap = sim.run_to(48, level=0.1)
    assert snap.bucket == "build" and snap.drop_armed and snap.drop_arm_source == "rekordbox"
    assert snap.beats_to_drop == 1 and snap.drop_pending            # the pre-drop blackout bridge
    snap = sim.run_to(49, level=0.1)                   # the chorus downbeat: DROP, whatever the audio says
    assert snap.bucket == "drop" and snap.classifier_bucket == "drop" and not snap.drop_pending
    assert not snap.drop_armed and snap.planned_scene == "Drop Strobe Stab" and not snap.planned_big_moment
    assert snap.drop_hit_intensity < 0.95                           # a regular drop: no laser fan
    snap = sim.run_to(90, level=0.1)
    assert snap.bucket == "break" and snap.section_kind == "breakdown"
    snap = sim.run_to(145, level=0.1)
    assert snap.bucket == "drop" and snap.planned_big_moment and snap.drop_hit_intensity >= 0.95
    snap = sim.run_to(215, level=0.1)
    assert snap.bucket == "outro" and not snap.planned_laser_allowed


def test_planned_outro_after_a_build_enters_as_break():
    # up -> outro: BUILD -> OUTRO is not a legal move (TIME-13; OUTRO is a version of BREAK, D-22),
    # and the one-tick BREAK the old override showed first hid it. The outro enters as BREAK, and
    # never shows a BREAK run shorter than a tick's worth before OUTRO either.
    spec = [(1, 1, "intro"), (17, 2, "up"), (49, 5, "chorus"), (113, 2, "up"), (145, 6, "outro")]
    sim = Sim(table=_table(spec, end=209))
    sim.run_to(150, level=0.1)
    buckets = [s.bucket for s in sim.snaps]
    pairs = {(a, b) for a, b in zip(buckets, buckets[1:]) if a != b}
    assert ("build", "outro") not in pairs
    assert buckets[-1] == "break" and sim.snaps[-1].section_kind == "outro"


def test_regular_drop_never_escalates_to_peak_but_the_big_moment_may():
    sim = Sim()
    sim.run_to(48, level=0.1)
    snap = sim.run_to(80, level=0.99)                  # loud for 31 beats inside the first (regular) chorus
    assert snap.bucket == "drop" and snap.classifier_bucket == "drop"
    assert all(s.bucket != "peak" for s in sim.snaps)
    sim.run_to(144, level=0.1)
    snap = sim.run_to(208, level=0.99)                  # the big moment: PEAK allowed
    assert snap.bucket in ("drop", "peak") and any(s.bucket == "peak" for s in sim.snaps[-100:])


def test_palette_is_pinned_per_track():
    sim = Sim(track_id=7)
    sim.run_to(60, level=0.1)
    pal = {s.palette for s in sim.snaps if s.bucket == "drop"}
    assert len(pal) == 1
    expected = palette_mod.pick("drop", 7, 0, 0)[0]
    assert pal == {expected}
    sim.run_to(150, level=0.1)
    assert {s.palette for s in sim.snaps if s.bucket == "drop"} == {expected}   # the same hook colour again
    other = Sim(track_id=8)
    other.run_to(60, level=0.1)
    assert other.snaps[-1].palette == palette_mod.pick("drop", 8, 0, 0)[0]


def test_losing_status_hands_back_to_the_classifier():
    sim = Sim()
    snap = sim.run_to(60, level=0.1)
    assert snap.bucket == "drop" and snap.plan_active
    # a short gap (under the grace period) keeps the plan's authority and the pinned palette
    for _ in range(3):
        sim.step_beat(level=0.1, send_status=False)
    snap = sim.snaps[-1]
    assert snap.plan_active and not snap.deck_status_fresh and snap.bucket == "drop"
    for _ in range(int((DECK_STATUS_STALE_AFTER + PLAN_HOLD_AFTER_STALE_S) / BEAT_S) + 2):
        sim.step_beat(level=0.1, send_status=False)
    snap = sim.snaps[-1]
    assert not snap.plan_active and not snap.deck_status_fresh
    assert snap.bucket in ("drop", "break", "outro")   # the classifier continues from the committed state
    # quiet audio for a long time: the classifier eventually leaves DROP on its own
    snap = sim.run_to(140, level=0.05, send_status=False)
    assert snap.bucket != "drop"


def test_shadow_mode_ignores_the_plan():
    sim = Sim(drive=False)
    snap = sim.run_to(20, level=0.1)
    assert not snap.plan_active and snap.bucket == "break"   # quiet audio stays BREAK; plan not applied
    assert snap.planned_scene == "Rising Tide"               # ...but the plan is still visible
    snap = sim.run_to(49, level=0.1)
    assert snap.bucket == "break" and snap.classifier_bucket == "break"


def test_no_plan_means_shadow_behaviour_even_in_drive():
    sim = Sim(drive=True, plan=False)
    snap = sim.run_to(49, level=0.1)
    assert not snap.plan_active and snap.bucket == "break" and snap.phrase_source == "rekordbox"


def test_dj_arm_is_not_overridden_by_the_plan():
    sim = Sim()
    sim.run_to(30, level=0.1)
    sim.m.arm_drop()
    snap = sim.run_to(40, level=0.1)
    assert snap.drop_armed and snap.drop_arm_source == "dj"
    snap = sim.run_to(49, level=0.1)
    assert snap.bucket == "drop"
    assert EnergyBucket(snap.classifier_bucket) == EnergyBucket.DROP


def test_plan_reapplies_its_section_when_status_returns():
    sim = Sim()
    snap = sim.run_to(60, level=0.1)
    assert snap.bucket == "drop"
    # status drops out inside the first chorus (49-81): authority is lost and the plan forgets
    # which section it applied, so it re-commits instead of waiting for the next boundary
    for _ in range(10):                                   # 5 s: past the grace period
        sim.step_beat(level=0.05, send_status=False)
    assert not sim.snaps[-1].plan_active and sim.m._plan_section_key is None
    sim.m._base_committed = EnergyBucket.BREAK            # pretend the classifier moved the bucket meanwhile
    sim.m._bucket_committed = EnergyBucket.BREAK
    snap = sim.run_to(sim.beat + 2, level=0.05)
    assert snap.plan_active and snap.bucket == "drop" and snap.section_kind == "drop" and snap.track_beat < 81


def test_cued_decks_do_not_flap_the_active_deck():
    sim = Sim()
    t = sim.clock.monotonic()
    for i in range(20):
        sim.m.queue.put(DeckStatusMessage(timestamp=t, player=1 + i % 2, track_id=7 + i % 2, loaded_player=1, slot="sd",
                                          analyze_type="rekordbox", play_state="cued", playing=False, on_air=False,
                                          master=False, sync=False, bpm=BPM, pitch=1.0, beat_count=1, beat_in_bar=1,
                                          source_ip="x"))
        sim.m.tick()
        sim.clock.advance(0.1)
        t = sim.clock.monotonic()
    decks = {s.active_deck for s in [sim.m.show_state.snapshot()]}
    assert decks == {1}            # deterministic: lowest player when nobody plays, and it stays put
    assert sim.m._active_deck == 1


def test_early_roll_drop_survives_an_intermediate_build_section():
    # plan: ... down(break) 81-113, up(build) 113-145, chorus 145: a roll start at beat 120 (25 beats before
    # the chorus) lands the drop; the build boundary must not take it away
    sim = Sim()
    sim.run_to(118, level=0.1)
    assert sim.snaps[-1].bucket == "build"
    n0 = len(sim.snaps)                       # the first chorus (beat 49) already happened; count from here
    sim.m._roll_start_pending = True          # what core/roll_period.py's detector raises
    snap = sim.run_to(121, level=0.1)
    assert snap.bucket == "drop" and snap.classifier_bucket == "drop"
    snap = sim.run_to(144, level=0.1)          # still inside the planned build: the drop look holds
    assert snap.bucket == "drop" and not snap.drop_pending
    snap = sim.run_to(150, level=0.1)          # the chorus boundary confirms, no second cut
    assert snap.bucket == "drop" and snap.planned_big_moment
    commits = [i for i in range(n0, len(sim.snaps)) if sim.snaps[i].bucket == "drop" and sim.snaps[i - 1].bucket != "drop"]
    assert len(commits) == 1


def test_regular_drop_never_peaks_and_overlay_bookkeeping_is_untouched():
    sim = Sim()
    sim.run_to(48, level=0.1)
    exposed_since = []
    for _ in range(31):
        sim.step_beat(level=0.99)
        exposed_since.append(sim.m._exposed_since)
    assert all(s.bucket == "drop" for s in sim.snaps[-150:])
    assert len(set(exposed_since)) == 1       # no PEAK->DROP demotions resetting the exposure clock


def test_loop_inside_a_section_holds_it_and_the_countdown():
    sim = Sim()
    sim.run_to(28, level=0.1)                           # in 'up' (build), 21 beats before the drop
    snap = sim.loop(25, 28, 24, level=0.1)              # a 4-beat loop, six passes
    assert snap.loop_active and snap.plan_active and snap.bucket == "build"
    held = sim.snaps[-24 * 5:]
    assert all(s.loop_active and s.section_kind == "build" for s in held)
    assert len({s.beats_to_drop for s in held}) == 1 and not any(s.drop_pending for s in held)
    assert held[0].track_beat == held[-1].track_beat   # the pinned beat, not the cycling counter
    snap = sim.run_to(48, level=0.1)                    # released: the countdown resumes live
    assert not snap.loop_active and snap.beats_to_drop == 1 and snap.drop_armed and snap.drop_pending
    snap = sim.run_to(49, level=0.1)
    assert snap.bucket == "drop" and not snap.drop_pending


def test_loop_across_the_drop_boundary_does_not_retrigger_the_drop():
    sim = Sim()
    sim.run_to(46, level=0.1)                           # armed count-in, drop at 49
    snap = sim.loop(45, 52, 32, level=0.1)              # an 8-beat loop straddling the chorus downbeat
    held = sim.snaps[-32 * 5:]
    assert all(s.bucket == "build" and s.beats_to_drop == held[0].beats_to_drop for s in held)
    assert not any(s.drop_pending for s in held)
    assert snap.loop_active and snap.drop_armed and snap.drop_arm_source == "rekordbox"
    snap = sim.run_to(53, level=0.1)                    # release lands inside the chorus: DROP now
    assert not snap.loop_active and snap.bucket == "drop" and snap.section_kind == "drop"
    assert snap.drop_hit_intensity < 0.95 and not snap.planned_big_moment


def test_loop_on_the_last_beat_does_not_hold_the_blackout_bridge():
    sim = Sim()
    snap = sim.run_to(48, level=0.1)
    assert snap.drop_pending and snap.beats_to_drop == 1
    snap = sim.loop(48, 48, 8, level=0.1)               # a 1-beat loop on the bridge beat
    assert snap.loop_active and snap.bucket == "build" and not snap.drop_pending
    assert all(not s.drop_pending for s in sim.snaps[-8 * 5:])
    snap = sim.run_to(49, level=0.1)
    assert snap.bucket == "drop"


def test_loop_pin_clears_when_status_goes_stale():
    sim = Sim()
    sim.run_to(28, level=0.1)
    sim.loop(25, 28, 8, level=0.1)
    sim.looping = True
    sim.beat = 28
    for _ in range(int((DECK_STATUS_STALE_AFTER + PLAN_HOLD_AFTER_STALE_S) / (BEAT_S / 5)) + 3):
        sim.step_beat(0.1, send_status=False)
    snap = sim.snaps[-1]
    assert not snap.loop_active and not snap.plan_active
    sim.looping = False
    sim.beat = 40
    snap = sim.run_to(49, level=0.1)                    # status back, no loop: the plan resumes
    assert snap.plan_active and snap.bucket == "drop"
