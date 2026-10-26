"""Metronome shadow-mode song structure: deck status + rekordbox phrase tables -> snapshot
fields, with the bucket classifier provably untouched."""

from __future__ import annotations

import queue
import threading

from livereact.clock import FakeClock
from livereact.core.messages import AudioLevelMessage, BeatMessage, DeckStatusMessage, TrackPhraseMessage
from livereact.core.metronome import ACTIVE_DECK_SWITCH_S, DECK_STATUS_STALE_AFTER, Metronome
from livereact.core.show_state import ShowState
from livereact.rekordbox.pssi import MOOD_HIGH, Phrase, PhraseTable


def _metronome(clock: FakeClock) -> Metronome:
    return Metronome(queue.Queue(), ShowState(), clock, threading.Event())


def _table() -> PhraseTable:
    # intro 1-32, up 33-64, chorus 65-128, down 129-160, outro 161-192 (end 193)
    spec = [(1, 1, 1, "intro"), (2, 33, 2, "up"), (3, 65, 5, "chorus"), (4, 129, 3, "down"), (5, 161, 6, "outro")]
    phrases = []
    for i, (idx, beat, kind, label) in enumerate(spec):
        end = spec[i + 1][1] if i + 1 < len(spec) else 193
        phrases.append(Phrase(index=idx, beat=beat, end_beat=end, kind=kind, label=label))
    return PhraseTable(mood=MOOD_HIGH, end_beat=193, bank=0, phrases=tuple(phrases))


def _status(t, *, player=1, track_id=7, beat_count=1, playing=True, on_air=True, master=False, bpm=120.0, pitch=1.0):
    return DeckStatusMessage(timestamp=t, player=player, track_id=track_id, loaded_player=1, slot="sd",
                             analyze_type="rekordbox", play_state="playing" if playing else "paused",
                             playing=playing, on_air=on_air, master=master, sync=False, bpm=bpm, pitch=pitch,
                             beat_count=beat_count, beat_in_bar=1, source_ip="x")


def _phrases(t, track_id=7, table=None):
    return TrackPhraseMessage(timestamp=t, player=1, track_id=track_id, title="Song", bpm=120.0, table=table, slot="sd")


def test_defaults_without_any_deck_status():
    m = _metronome(FakeClock())
    snap = m.tick()
    assert snap.active_deck is None and not snap.deck_status_fresh
    assert snap.track_id == 0 and snap.phrase_source == "" and snap.section_kind == ""
    assert snap.beats_to_next_section == -1 and snap.track_beat == 0


def test_status_and_table_populate_section_fields():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_phrases(0.0, table=_table()))
    m.queue.put(_status(0.0, beat_count=40))
    snap = m.tick()
    assert snap.active_deck == 1 and snap.deck_status_fresh and snap.track_id == 7 and snap.track_title == "Song"
    assert snap.phrase_source == "rekordbox" and snap.phrase_label == "up" and snap.section_kind == "build"
    assert snap.next_section_kind == "drop" and snap.beats_to_next_section == 25 and snap.track_beat == 40


def test_track_beat_extrapolates_between_status_packets_and_counts_down():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_phrases(0.0, table=_table()))
    m.queue.put(_status(0.0, beat_count=60, bpm=120.0))   # 2 beats/s
    assert m.tick().beats_to_next_section == 5
    clock.advance(0.5)                                      # +1 beat, no new packet
    snap = m.tick()
    assert snap.track_beat == 61 and snap.beats_to_next_section == 4
    clock.advance(0.4)
    m.queue.put(_status(0.9, beat_count=65))               # the boundary beat belongs to the chorus
    snap = m.tick()
    assert snap.phrase_label == "chorus" and snap.section_kind == "drop" and snap.next_section_kind == "breakdown"
    assert snap.beats_to_next_section == 64


def test_paused_deck_does_not_extrapolate():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_phrases(0.0, table=_table()))
    m.queue.put(_status(0.0, beat_count=60, playing=False))
    m.tick()
    clock.advance(0.9)
    assert m.tick().track_beat == 60


def test_stale_status_clears_fields_but_keeps_deck():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_phrases(0.0, table=_table()))
    m.queue.put(_status(0.0, beat_count=40))
    assert m.tick().phrase_source == "rekordbox"
    clock.advance(DECK_STATUS_STALE_AFTER + 0.01)
    snap = m.tick()
    assert snap.active_deck == 1 and not snap.deck_status_fresh
    assert snap.phrase_source == "" and snap.section_kind == "" and snap.beats_to_next_section == -1


def test_no_table_means_no_phrase_source_but_status_still_shown():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_phrases(0.0, table=None))
    m.queue.put(_status(0.0, beat_count=40))
    snap = m.tick()
    assert snap.deck_status_fresh and snap.track_id == 7 and snap.track_beat == 40
    assert snap.phrase_source == "" and snap.phrase_label == "" and snap.beats_to_next_section == -1


def test_no_beat_count_means_track_beat_zero():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_phrases(0.0, table=_table()))
    m.queue.put(_status(0.0, beat_count=None))
    snap = m.tick()
    assert snap.track_beat == 0 and snap.phrase_source == ""


def test_active_deck_prefers_on_air_and_holds_through_a_crossfade():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_phrases(0.0, track_id=7, table=_table()))
    m.queue.put(_status(0.0, player=2, track_id=8, beat_count=10, on_air=False))
    m.queue.put(_status(0.0, player=1, track_id=7, beat_count=40, on_air=True))
    assert m.tick().active_deck == 1
    # deck 2 goes on air while deck 1 is still on air: no switch at all
    for i in range(1, 6):
        clock.advance(0.2)
        t = clock.monotonic()
        m.queue.put(_status(t, player=1, track_id=7, beat_count=40 + i, on_air=True))
        m.queue.put(_status(t, player=2, track_id=8, beat_count=10 + i, on_air=True))
        assert m.tick().active_deck == 1
    # deck 1 drops off air but keeps playing: deck 2 takes over only after the hysteresis
    t_off = clock.monotonic()
    while clock.monotonic() - t_off < ACTIVE_DECK_SWITCH_S - 0.3:
        clock.advance(0.2)
        t = clock.monotonic()
        m.queue.put(_status(t, player=1, track_id=7, beat_count=50, on_air=False))
        m.queue.put(_status(t, player=2, track_id=8, beat_count=20, on_air=True))
        assert m.tick().active_deck == 1
    clock.advance(0.5)
    t = clock.monotonic()
    m.queue.put(_status(t, player=1, track_id=7, beat_count=50, on_air=False))
    m.queue.put(_status(t, player=2, track_id=8, beat_count=20, on_air=True))
    assert m.tick().active_deck == 2


def test_active_deck_switches_immediately_when_current_stops():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_status(0.0, player=1, track_id=7, beat_count=40, on_air=True))
    m.queue.put(_status(0.0, player=2, track_id=8, beat_count=10, on_air=False))
    assert m.tick().active_deck == 1
    clock.advance(0.2)
    m.queue.put(_status(0.2, player=1, track_id=7, beat_count=40, playing=False, on_air=True))
    m.queue.put(_status(0.2, player=2, track_id=8, beat_count=11, on_air=False))
    assert m.tick().active_deck == 2


def test_new_track_on_active_deck_resets_drop_cycle():
    clock = FakeClock()
    m = _metronome(clock)
    m.queue.put(_status(0.0, track_id=7, beat_count=40))
    m.tick()
    m.drop_cycle = 3
    seed = m._track_seed
    clock.advance(0.2)
    m.queue.put(_status(0.2, track_id=9, beat_count=1))
    m.tick()
    assert m.drop_cycle == 0 and m._track_seed == seed + 1


def _audio(t, level):
    return AudioLevelMessage(timestamp=t, rms=level, band_low=0.5, band_mid=0.3, band_high=0.2,
                             band_low_abs=level, band_mid_abs=level * 0.6, band_high_abs=level * 0.4)


def test_shadow_mode_never_changes_the_bucket_sequence():
    """Same beats + same audio -> same classifier output whether or not phrase data arrives."""
    def run(with_phrases: bool) -> list[str]:
        clock = FakeClock()
        m = _metronome(clock)
        if with_phrases:
            m.queue.put(_phrases(0.0, table=_table()))
        out = []
        beat = 1
        for i in range(600):                       # 60 s at 10 Hz
            t = clock.monotonic()
            if i % 5 == 0:                          # 120 bpm
                m.queue.put(BeatMessage(timestamp=t, beat_in_bar=beat, bpm=120.0, is_master=True, player=1))
                beat = beat % 4 + 1
            level = 0.15 if i < 200 else (0.5 if i < 350 else 0.95)
            m.queue.put(_audio(t, level))
            if with_phrases and i % 2 == 0:
                m.queue.put(_status(t, beat_count=1 + i // 5, master=True))
            snap = m.tick()
            out.append(snap.bucket + "/" + snap.classifier_bucket)
            clock.advance(0.1)
        return out

    assert run(False) == run(True)
