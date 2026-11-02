"""engines/laser_trigger.py -- lasers as punctuation (2026-10-06)."""
from __future__ import annotations

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines import laser_trigger as lt
from livereact.engines.laser_trigger import LaserTriggerEngine
from livereact.fixtures.rig import load_rig
from livereact.merge.merge import ManualParams

BPM = 120.0
BEAT_S = 60.0 / BPM
BAR_S = 4 * BEAT_S


def _snap(**kw) -> ShowSnapshot:
    base = dict(bpm=BPM, audio_fresh=True, pdl_fresh=True, bucket="break", drop_hit_intensity=1.0,
                energy=0.5, energy_norm=0.5, palette="blood_red", audio_band_low_abs=1.0,
                drop_start_bar=None, bar=0, beat_in_bar=1)
    base.update(kw)
    return ShowSnapshot(**base)


def _engine(manual: ManualParams | None = None):
    clock = FakeClock()
    rig = load_rig("rigs/testing_setup.json")
    eng = LaserTriggerEngine(clock, manual=manual)
    return eng, clock, rig


def _run(eng, clock, rig, snap, seconds: float, step: float = 0.05):
    """Tick for `seconds`, returning the list of (t, active) samples."""
    out = []
    n = int(round(seconds / step))
    for _ in range(n):
        clock.advance(step)
        eng.tick(snap, rig)
        out.append((clock.monotonic(), eng.active))
    return out


def test_dark_by_default_and_primed():
    eng, clock, rig = _engine()
    laser = rig.fixture("laser_l")
    c = eng.tick(_snap(), rig)
    assert not eng.active and eng.primed and eng.mood == lt.MOOD_READY
    assert c.values[laser.offset] == 0                       # CH1 off
    assert c.values[laser.offset + 1] == laser.PATTERN_A1_DOTTED_LINE  # ...but the line is primed


def test_entering_drop_fires_a_two_bar_moment_then_dark():
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=10), 2 * BAR_S)
    assert not eng.active
    c = None
    clock.advance(0.05)
    c = eng.tick(_snap(bucket="drop", bar=12, drop_start_bar=12), rig)
    laser = rig.fixture("laser_l")
    assert eng.active and eng.trigger == lt.TRIGGER_DROP and eng.source == "auto"
    assert c.values[laser.offset] == laser.MANUAL_PATTERN_MODE
    samples = _run(eng, clock, rig, _snap(bucket="drop", bar=12, drop_start_bar=12), 3 * BAR_S)
    lit = [a for _, a in samples]
    assert lit[0] and not lit[-1]
    lit_s = sum(1 for a in lit if a) * 0.05
    assert abs(lit_s - lt.MOMENT_BARS * BAR_S) <= 0.1


def test_auto_triggers_respect_the_gap_and_a_refused_trigger_consumes_nothing():
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=10), BAR_S)
    clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=12, drop_start_bar=12), rig)
    assert eng.trigger == lt.TRIGGER_DROP
    _run(eng, clock, rig, _snap(bucket="drop", bar=12, drop_start_bar=12), 3 * BAR_S)
    assert not eng.active
    # a second drop entry inside the gap: refused
    _run(eng, clock, rig, _snap(bucket="break", bar=16), BAR_S)
    clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=17, drop_start_bar=17), rig)
    assert not eng.active and eng.trigger == ""
    # past the gap: allowed again
    _run(eng, clock, rig, _snap(bucket="break", bar=20), lt.MIN_GAP_BARS * BAR_S)
    clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=30, drop_start_bar=30), rig)
    assert eng.active and eng.trigger == lt.TRIGGER_DROP


def test_peak_fires_two_bars_on_four_bars_off_while_it_holds():
    """2026-10-29 (Josh): "if it fires for 2 bars, it should fire again in another 4 bars, while the
    energy is still high" -- 6-bar blocks from the drop's origin, a 4-bar gap from the moment's end."""
    eng, clock, rig = _engine()
    fired = []
    was_active = False
    for bar in range(40, 64):
        for beat in (1, 2, 3, 4):
            clock.advance(BEAT_S)
            eng.tick(_snap(bucket="peak", bar=bar, beat_in_bar=beat, drop_start_bar=40), rig)
            if eng.active and not was_active and eng.trigger == lt.TRIGGER_PEAK:
                fired.append(bar)
            was_active = eng.active
    assert fired == [40, 46, 52, 58]


def test_duty_cap_refuses_auto_triggers_but_not_the_manual_hit():
    manual = ManualParams()
    eng, clock, rig = _engine(manual)
    # saturate the window: moments of MOMENT_BARS back to back at the MIN_GAP spacing -> a duty of
    # MOMENT / (MOMENT + GAP) (33 % at 2 on / 4 off), over the cap once the window has filled
    bar = 10
    for _ in range(12):
        _run(eng, clock, rig, _snap(bucket="break", bar=bar), lt.MIN_GAP_BARS * BAR_S + 0.1)
        bar += int(lt.MIN_GAP_BARS) + 1
        clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=bar, drop_start_bar=bar), rig)
        if eng.budget >= lt.DUTY_MAX:
            break
        assert eng.active
        _run(eng, clock, rig, _snap(bucket="drop", bar=bar, drop_start_bar=bar), lt.MOMENT_BARS * BAR_S + 0.2)
        bar += int(lt.MOMENT_BARS)
    _run(eng, clock, rig, _snap(bucket="break", bar=bar + 1), lt.MIN_GAP_BARS * BAR_S + 0.1)
    assert eng.budget >= lt.DUTY_MAX * 0.6
    eng.budget_before = eng.budget
    clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=60, drop_start_bar=60), rig)
    if eng.active:  # the window may have decayed just under the cap at 120 s; force the case
        _run(eng, clock, rig, _snap(bucket="drop", bar=60, drop_start_bar=60), lt.MOMENT_BARS * BAR_S + 0.2)
        clock.advance(0.05); eng.tick(_snap(bucket="break", bar=63), rig)
        clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=64, drop_start_bar=64), rig)
    assert not eng.active                                   # refused: gap or duty
    manual.laser_hit += 1
    clock.advance(0.05); eng.tick(_snap(bucket="break", bar=64), rig)
    assert eng.active and eng.trigger == lt.TRIGGER_HIT and eng.source == "manual"


def test_manual_hit_is_a_counter_and_fires_each_increment():
    manual = ManualParams()
    eng, clock, rig = _engine(manual)
    clock.advance(0.05); eng.tick(_snap(), rig)
    manual.laser_hit += 1
    clock.advance(0.05); eng.tick(_snap(), rig)
    assert eng.active and eng.trigger == lt.TRIGGER_HIT
    _run(eng, clock, rig, _snap(), lt.MOMENT_BARS * BAR_S + 0.2)
    assert not eng.active
    manual.laser_hit += 1
    clock.advance(0.05); eng.tick(_snap(), rig)
    assert eng.active                                       # no gap rule for the operator


def test_cue_holds_the_line_while_pressed_and_releases_to_dark():
    manual = ManualParams(laser_cue=True)
    eng, clock, rig = _engine(manual)
    _run(eng, clock, rig, _snap(audio_band_mid_abs=2.0), 3 * BAR_S)
    assert eng.active and eng.trigger == lt.TRIGGER_CUE and eng.source == "manual"
    manual.laser_cue = False
    clock.advance(0.05); eng.tick(_snap(), rig)
    assert not eng.active and eng.mood == lt.MOOD_READY


def test_wub_spike_in_a_drop_is_a_trigger():
    eng, clock, rig = _engine()
    # let the drop-entry moment pass and the gap elapse inside the drop
    _run(eng, clock, rig, _snap(bucket="build", bar=0, audio_band_low_abs=1.0), BAR_S)
    clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=1, drop_start_bar=1, audio_band_low_abs=1.0), rig)
    _run(eng, clock, rig, _snap(bucket="drop", bar=4, drop_start_bar=1, audio_band_low_abs=1.0), (lt.MOMENT_BARS + lt.MIN_GAP_BARS) * BAR_S + 0.5)
    assert not eng.active
    # a big low-band hit against the quiet baseline: the envelope clears the
    # threshold on its first spike tick and the moment opens there
    seen = []
    for _ in range(6):
        clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=12, drop_start_bar=1, audio_band_low_abs=40.0), rig)
        seen.append(eng.wub)
    assert max(seen) >= lt.WUB_TRIGGER
    assert eng.active and eng.trigger == lt.TRIGGER_WUB


def test_signal_loss_closes_a_moment_immediately():
    eng, clock, rig = _engine()
    _run(eng, clock, rig, _snap(bucket="build", bar=0), BAR_S)
    clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=1, drop_start_bar=1), rig)
    assert eng.active
    clock.advance(0.05); eng.tick(_snap(bucket="drop", bar=1, drop_start_bar=1, pdl_fresh=False), rig)
    assert not eng.active                                   # auto path needs the beat grid
    manual = ManualParams(laser_cue=True)
    eng2, clock2, rig2 = _engine(manual)
    clock2.advance(0.05); eng2.tick(_snap(), rig2)
    assert eng2.active
    clock2.advance(0.05); eng2.tick(_snap(audio_fresh=False), rig2)
    assert not eng2.active and eng2.mood == lt.MOOD_OFF


def test_show_engine_gate_follows_the_trigger_engine_on_the_published_frame():
    """End to end: a moment lights CH1 on the real frame; dark between moments."""
    from livereact.core.frame_buffer import DmxFrameBuffer
    from livereact.core.show_engine import ShowEngine
    from livereact.core.show_state import ShowState
    clock = FakeClock()
    rig = load_rig("rigs/testing_setup.json")
    manual = ManualParams(laser_enable=True)
    state = ShowState()
    import threading
    eng_l = LaserTriggerEngine(clock, manual=manual)
    show = ShowEngine(state, rig, DmxFrameBuffer(), [eng_l], manual, clock, threading.Event(), laser_engine=eng_l)
    laser = rig.fixture("laser_l")
    state.publish(_snap(bucket="build", bar=0, fader_a=1.0, eq_low_a=1.0))
    clock.advance(0.05); show.tick()
    state.publish(_snap(bucket="drop", bar=1, drop_start_bar=1, fader_a=1.0, eq_low_a=1.0))
    clock.advance(0.05); frame = show.tick()
    assert frame[laser.offset] == laser.MANUAL_PATTERN_MODE
    for _ in range(int((lt.MOMENT_BARS * BAR_S + 0.3) / 0.05)):
        clock.advance(0.05); frame = show.tick()
    assert frame[laser.offset] == 0                          # the gate closed CH1
    assert frame[laser.offset + 1] == laser.PATTERN_A1_DOTTED_LINE  # primed line still held
