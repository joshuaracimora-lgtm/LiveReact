"""Tests for the ShowEngine: publishing, per-engine isolation, freeze, blackout."""

from __future__ import annotations

import threading

from livereact.clock import FakeClock
from livereact.core.frame_buffer import UNIVERSE_SIZE, DmxFrameBuffer
from livereact.core.show_engine import ShowEngine
from livereact.core.show_state import ShowSnapshot, ShowState
from livereact.engines.reactive import ReactiveLayerEngine
from livereact.fixtures.rig import load_rig
from livereact.merge.merge import ManualParams


def _engine(engines, manual=None, state=None):
    rig = load_rig()
    show_state = state or ShowState()
    fb = DmxFrameBuffer()
    eng = ShowEngine(show_state, rig, fb, engines, manual or ManualParams(),
                     FakeClock(), threading.Event())
    return eng, fb, show_state


def test_publishes_a_lit_frame_when_energy_present():
    state = ShowState()
    state.publish(ShowSnapshot(energy=0.8, fader_a=1.0, eq_low_a=1.0, beat_phase=0.0))
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], state=state)
    frame = eng.tick()
    assert frame is not None and len(frame) == UNIVERSE_SIZE
    assert any(frame)  # not a blackout


class FailingEngine:
    name = "boom"

    def tick(self, snapshot, rig):
        raise RuntimeError("engine blew up")


def test_a_failing_engine_does_not_stop_the_show():
    state = ShowState()
    state.publish(ShowSnapshot(energy=0.8, fader_a=1.0, eq_low_a=1.0))
    eng, fb, _ = _engine([FailingEngine(), ReactiveLayerEngine(FakeClock())], state=state)
    frame = eng.tick()  # must not raise
    assert frame is not None
    assert any(frame)  # the reactive layer still produced output


def test_freeze_holds_the_last_frame():
    state = ShowState()
    state.publish(ShowSnapshot(energy=0.8, fader_a=1.0, eq_low_a=1.0))
    manual = ManualParams()
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], manual=manual, state=state)
    eng.tick()
    held = fb.latest()
    manual.freeze = True
    state.publish(ShowSnapshot(energy=0.1, fader_a=0.1))  # world changed
    assert eng.tick() is None       # nothing published
    assert fb.latest() == held      # writer keeps the old frame


def test_blackout_zeros_the_frame():
    state = ShowState()
    state.publish(ShowSnapshot(energy=1.0, fader_a=1.0, eq_low_a=1.0))
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], manual=ManualParams(blackout=True), state=state)
    assert eng.tick() == bytes(UNIVERSE_SIZE)


def test_no_music_detected_auto_blackouts_after_the_hold_time():
    """docs/14 §2, end-to-end through the real ShowEngine: no PDL, no audio ->
    auto-blackout engages after SilenceDetector's hold time, same as if the
    operator had hit the manual switch."""
    state = ShowState()
    state.publish(ShowSnapshot(energy=1.0, fader_a=1.0, eq_low_a=1.0, pdl_fresh=False, audio_fresh=False))
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], state=state)
    eng.silence.hold_s = 0.05  # keep the test fast
    assert any(eng.tick())  # lit at first — hold time hasn't elapsed yet

    eng.clock.advance(0.1)
    frame = eng.tick()
    assert frame == bytes(UNIVERSE_SIZE)
    assert eng.silence.active is True


def test_real_audio_confirms_playing_prevents_auto_blackout():
    """Same as above but with a real, non-silent audio reading -> must NOT
    auto-blackout, even with no PDL beats at all."""
    state = ShowState()
    state.publish(ShowSnapshot(energy=1.0, fader_a=1.0, eq_low_a=1.0,
                                pdl_fresh=False, audio_fresh=True, audio_energy=0.3))
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], state=state)
    eng.silence.hold_s = 0.05
    eng.tick()
    eng.clock.advance(0.1)
    frame = eng.tick()
    assert any(frame)
    assert eng.silence.active is False


# --- Venue-tab calibration pins (2026-09-18) ----------------------------------

def _pins(**kw):
    from livereact.merge.merge import CalibrationPins
    return CalibrationPins(**kw)


def test_pins_reach_the_published_frame_and_are_still_slew_limited():
    state = ShowState()
    state.publish(ShowSnapshot(energy=0.8, fader_a=1.0, eq_low_a=1.0))
    manual = ManualParams()
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], manual=manual, state=state)
    mh = eng.rig.fixture("mh_l")  # address 1: CH1/2 pan, CH3/4 tilt, CH6 dimmer
    manual.calibration = _pins(channels={1: 0, 2: 0, 6: 255})
    eng.tick()                     # establishes the slew reference at pan 0
    manual.calibration = _pins(channels={1: 255, 2: 255, 6: 255})
    eng.clock.advance(0.025)       # one 40 Hz frame of real time for the slew limiter
    frame = eng.tick()
    assert frame[mh.offset + 5] == 255            # the pinned dimmer got through
    assert 0 < frame[mh.offset + 0] < 255         # the pan jump was ramped, not slammed
    manual.calibration = None
    eng.tick()                                     # unpinned: the engines own the channel again


def test_pinned_laser_stays_dark_unless_armed_and_geometry_safe():
    from livereact.fixtures.profiles import Laser
    state = ShowState()
    state.publish(ShowSnapshot(energy=0.8, fader_a=1.0, eq_low_a=1.0))
    manual = ManualParams()
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], manual=manual, state=state)
    laser = eng.rig.fixture("laser_l")
    ch1 = laser.address
    pins = _pins(channels={ch1: Laser.MANUAL_PATTERN_MODE, ch1 + 1: Laser.PATTERN_A1_DOTTED_LINE}, laser_open=True)

    manual.calibration = pins                      # not armed
    assert eng.tick()[laser.offset] == 0
    manual.laser_enable = True                     # armed + gate opened by the pin
    assert eng.tick()[laser.offset] == Laser.MANUAL_PATTERN_MODE
    manual.calibration = _pins(channels={ch1: Laser.MANUAL_PATTERN_MODE}, laser_open=False)
    assert eng.tick()[laser.offset] == 0           # pinned but the gate is closed


def test_pinned_head_skips_the_home_remap():
    state = ShowState()
    state.publish(ShowSnapshot(energy=0.8, fader_a=1.0, eq_low_a=1.0))
    manual = ManualParams()
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], manual=manual, state=state)
    mh = eng.rig.fixture("mh_l")
    mh.home_pan, mh.home_tilt = 0.7, 0.3
    manual.calibration = _pins(channels={1: 255, 2: 255, 3: 0, 4: 0}, absolute_slots=frozenset({"mh_l"}))
    frame = eng.tick()                             # first tick: no slew reference yet -> passes through
    assert frame[mh.offset + 0] == 255 and frame[mh.offset + 2] == 0


def test_calibration_pins_hold_off_the_no_music_auto_blackout():
    """2026-10-05: an operator jogging a real head in the Venue tab (no music
    playing) must be able to SEE it -- the pins suppress the silence
    failsafe; the manual blackout switch still wins; the failsafe re-engages
    the instant the pins clear."""
    state = ShowState()
    state.publish(ShowSnapshot(energy=1.0, fader_a=1.0, eq_low_a=1.0, pdl_fresh=False, audio_fresh=False))
    manual = ManualParams()
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], manual=manual, state=state)
    mh = eng.rig.fixture("mh_l")
    eng.silence.hold_s = 0.05
    manual.calibration = _pins(channels={mh.offset + 6: 255})  # ch6 dimmer (1-based = offset+6)
    eng.tick()
    eng.clock.advance(0.1)
    frame = eng.tick()
    assert eng.silence.active is True              # the detector still knows there is no music
    assert frame[mh.offset + 5] == 255             # ...but the pinned head is lit
    manual.blackout = True
    assert eng.tick() == bytes(UNIVERSE_SIZE)      # the operator's kill switch still wins
    manual.blackout = False
    manual.calibration = None
    eng.clock.advance(0.025)
    assert eng.tick() == bytes(UNIVERSE_SIZE)      # pins gone: the failsafe is back


def test_a_planned_whiteout_cue_overrides_the_wash_and_heads_to_full_white():
    """2026-10-29: look-note cues ("drums cut ... straight to white out") reach the published frame."""
    state = ShowState()
    quiet = dict(energy=0.1, energy_norm=0.1, bucket="break", fader_a=1.0, beat_phase=0.0, audio_fresh=True, pdl_fresh=True)
    state.publish(ShowSnapshot(**quiet))
    eng, fb, _ = _engine([ReactiveLayerEngine(FakeClock())], state=state)
    before = eng.tick()
    state.publish(ShowSnapshot(**quiet, planned_look="whiteout", palette="blood_red", planned_flash_white=1.0))
    frame = eng.tick()
    rig = eng.rig
    bar = rig.fixture("bar_l")
    assert frame[bar.offset] == 255                       # ch1 dimmer full (a break wash sat near 15 %)
    assert tuple(frame[bar.offset + 2:bar.offset + 6]) == (255, 255, 255, 255)   # R G B W: a bright song -> white
    assert before[bar.offset] < 128
    # D-36: a dark song's hold is its own colour at full, not white
    from livereact.engines.palette import rgb as palette_rgb
    state.publish(ShowSnapshot(**quiet, planned_look="whiteout", palette="blood_red", planned_flash_white=0.0))
    tinted = eng.tick()
    r, g, b = (round(v * 255) for v in palette_rgb("blood_red"))
    assert tinted[bar.offset] == 255 and tuple(tinted[bar.offset + 2:bar.offset + 5]) == (r, g, b)
    assert tinted[bar.offset + 5] == 0                    # no white zone on a pure colour
    mh = rig.fixture("mh_r")
    assert frame[mh.offset + 5] == 255                    # head dimmer (ch6) full
    assert all(v == 0 for v in frame[rig.fixture("laser_l").offset: rig.fixture("laser_l").offset + 12])  # lasers untouched (unarmed)
    state.publish(ShowSnapshot(**quiet))                  # the cue ends: back to the quiet break
    after = eng.tick()
    assert after[bar.offset] < 128


def test_a_strobe_flash_is_painted_in_the_flash_colour_over_the_phrase_look():
    """D-36: COLOR is LTP by priority, so the flash colour is applied after the merge."""
    from livereact.engines.palette import rgb as palette_rgb
    from livereact.engines.phrase import PhraseEngine
    from livereact.scenes.catalog import SceneCatalog
    state = ShowState()
    roll = dict(bucket="drop", energy=0.8, audio_fresh=True, pdl_fresh=True, plan_active=True, planned_strobe_allowed=True,
                planned_roll_progress=1.0, drop_start_bar=10, bar=10, beat_in_bar=1, bpm=135.0, palette="blood_red",
                classifier_bucket="drop")
    state.publish(ShowSnapshot(**roll, beat_phase=0.05, planned_flash_white=0.5))
    react = ReactiveLayerEngine(FakeClock())
    eng, fb, _ = _engine([PhraseEngine(SceneCatalog(full=True)), react], state=state)
    frame = eng.tick()
    assert react.strobe_on
    bar = eng.rig.fixture("bar_l")
    want = tuple(round(v * 255) for v in react.flash_rgb)
    assert tuple(frame[bar.offset + 2:bar.offset + 5]) == want
    assert frame[bar.offset + 5] == round(min(react.flash_rgb) * 255)
    plain = tuple(round(v * 255) for v in palette_rgb("blood_red"))
    assert want != plain                                   # lifted toward white, not the plain wash colour
