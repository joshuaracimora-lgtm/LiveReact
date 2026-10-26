"""Drive mode: the track plan decides the bucket on the live beat counter; count-in, restraint,
palette pinning, and the fallback to the classifier when the plan loses authority."""

from __future__ import annotations

import queue
import threading

from livereact.clock import FakeClock
from livereact.core.messages import AudioLevelMessage, BeatMessage, DeckStatusMessage, TrackPhraseMessage
from livereact.core.metronome import DECK_STATUS_STALE_AFTER, Metronome
from livereact.core.show_state import ShowState
from livereact.engines import palette as palette_mod
from livereact.rekordbox.planner import plan_track
from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable
from livereact.scenes.base import EnergyBucket

BPM = 120.0
BEAT_S = 60.0 / BPM


def _table():
    # intro 1-17, up 17-49, chorus 49-81, down 81-113, up 113-145, chorus 145-209, outro 209-241
    spec = [(1, 1, "intro"), (17, 2, "up"), (49, 5, "chorus"), (81, 3, "down"), (113, 2, "up"),
            (145, 5, "chorus"), (209, 6, "outro")]
    phrases = []
    for i, (beat, kind, label) in enumerate(spec):
        nxt = spec[i + 1][0] if i + 1 < len(spec) else 241
        phrases.append(Phrase(index=i + 1, beat=beat, end_beat=nxt, kind=kind, label=label))
    return PhraseTable(mood=MOOD_HIGH, end_beat=241, bank=0, phrases=tuple(phrases))


class Sim:
    """Drive a Metronome beat by beat with a synthetic XDJ status stream + audio."""

    def __init__(self, drive=True, plan=True, track_id=7):
        self.clock = FakeClock()
        self.m = Metronome(queue.Queue(), ShowState(), self.clock, threading.Event())
        self.m.phrases_mode = "drive" if drive else "shadow"
        self.table = _table()
        self.plan = plan_track(self.table, track_id=track_id, bpm=BPM) if plan else None
        self.m.queue.put(TrackPhraseMessage(timestamp=0.0, player=1, track_id=track_id, title="T", bpm=BPM,
                                            table=self.table, plan=self.plan))
        self.track_id = track_id
        self.beat = 0
        self.beat_in_bar = 0
        self.level = 0.2
        self.snaps = []

    def status(self, *, playing=True, beat=None):
        return DeckStatusMessage(timestamp=self.clock.monotonic(), player=1, track_id=self.track_id, loaded_player=1,
                                 slot="sd", analyze_type="rekordbox", play_state="playing" if playing else "paused",
                                 playing=playing, on_air=True, master=True, sync=False, bpm=BPM, pitch=1.0,
                                 beat_count=self.beat if beat is None else beat, beat_in_bar=self.beat_in_bar or 1,
                                 source_ip="x")

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
    for _ in range(int(DECK_STATUS_STALE_AFTER / (BEAT_S / 5)) + 2):
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
