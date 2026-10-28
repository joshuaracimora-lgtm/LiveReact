"""Tests for the Reactive Layer engine."""

from __future__ import annotations

import pytest

from livereact.clock import FakeClock
from livereact.core.show_state import ShowSnapshot
from livereact.engines.palette import rgb as palette_rgb
from livereact.engines.reactive import (
    BUILD_STROBE_START, DROP_HELD_HIGH, DROP_HELD_LOW, PEAK_BOOST, STROBE_FLASH_DMX, STROBE_FLASH_START_DMX,
    STROBE_MAX,
    STROBE_TOGGLE_MAX_HZ, ReactiveLayerEngine, _StrobeClock, _drop_held_level,
)
from livereact.fixtures.profiles import WashBar
from livereact.fixtures.rig import load_rig
from livereact.scenes.catalog import bucket_for_energy


def _snap(**kw) -> ShowSnapshot:
    # drop_hit_intensity defaults to CEIL (1.0), not ShowSnapshot's own
    # production default (0.85, the FLOOR) -- 2026-09-14, when _strobe_rate
    # moved from chasing raw energy_norm to reusing this held-once value
    # (Josh's correction: strobe was reading as random flicker, not a real
    # strobe). Most existing tests only set `energy=` and implicitly want
    # "fully engaged" strobe behavior; CEIL matches that intent without
    # having to add drop_hit_intensity= to every one of them. Tests
    # exercising the FLOOR->CEIL gradient itself override it explicitly.
    base = dict(bpm=120.0, energy=0.8, fader_a=1.0, eq_low_a=1.0, eq_mid_a=0.2, eq_high_a=0.2,
                drop_hit_intensity=1.0)
    base.update(kw)
    # bucket_for_energy reads energy_norm, not raw energy (2026-09-07, found
    # live) -- default it to match energy so existing tests that only set
    # `energy=` still exercise the bucket they intend to, unless a test
    # explicitly passes energy_norm to test the distinction.
    base.setdefault("energy_norm", base["energy"])
    # snap.bucket is normally an ALREADY-DEBOUNCED value from metronome.py
    # (BUCKET_MIN_DWELL_S) -- these are unit tests of the engine directly, not
    # of the debounce, so default it to whatever bucket_for_energy would say
    # for this snapshot's own energy_norm, unless a test explicitly overrides
    # it to exercise a mismatch.
    base.setdefault("bucket", bucket_for_energy(base["energy_norm"]).value)
    return ShowSnapshot(**base)


def test_wash_color_is_the_snapshots_palette_entry_not_the_audio_bands():
    """2026-09-15 (third live capture): hue used to be re-derived from the
    dominant audio band. Now every fixture takes the phrase-frozen palette
    entry the metronome put on the snapshot (engines/palette.py) -- the
    bands can say whatever they like, tick to tick, and the color does not
    move."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    par = rig.fixture("par_l")  # ch1=dimmer, R,G,B at offset+1..offset+3
    r, g, b = palette_rgb("cold_steel")
    for bands in ((1.0, 0.2, 0.2), (0.2, 0.2, 1.0), (0.2, 1.0, 0.2)):
        c = eng.tick(_snap(bar=0, beat_phase=0.0, palette="cold_steel",
                           eq_low_a=bands[0], eq_mid_a=bands[1], eq_high_a=bands[2]), rig)
        assert (c.values[par.offset + 1], c.values[par.offset + 2], c.values[par.offset + 3]) == \
            (round(r * 255), round(g * 255), round(b * 255))


def test_moving_heads_take_the_palettes_spot_entry():
    """BUILD: the wash is white for the strobe and the heads carry the hue."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    mh = rig.fixture("mh_l")
    c = eng.tick(_snap(bar=0, beat_phase=0.0, bucket="build", energy=0.5,
                       palette="neutral_wash", palette_spot="hot_magenta"), rig)
    # The moving head is a color-WHEEL fixture (ch8 slot): compare against
    # what set_color itself would pick for the spot entry vs. the wash entry.
    from livereact.merge.merge import Contribution
    want, not_want = Contribution(), Contribution()
    mh.set_color(want, *palette_rgb("hot_magenta"))
    mh.set_color(not_want, *palette_rgb("neutral_wash"))
    assert want.values[mh.offset + 7] != not_want.values[mh.offset + 7]  # the test is meaningful
    assert c.values[mh.offset + 7] == want.values[mh.offset + 7]


def test_moving_heads_mirror_left_and_right():
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    c = eng.tick(_snap(bar=2, beat_in_bar=3, beat_phase=0.25), rig)
    mh_l, mh_r = rig.fixture("mh_l"), rig.fixture("mh_r")
    # Pan is mirrored around center (128), so the two heads sit on opposite sides.
    assert c.values[mh_l.offset] != c.values[mh_r.offset]


def test_strobe_off_unless_the_drop_is_armed_and_the_rise_is_on():
    """2026-09-17 (run 10): the pre-drop strobe is driven by the rise, not
    by the loudness classifier's BUILD -- a BUILD without an armed drop and
    a rise gets no strobe, however loud; a BREAK with both does."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    eng.tick(_snap(energy=1.0, beat_phase=0.0, bucket="break"), rig)
    assert eng.strobe_level == 0 and not eng.strobe_on
    eng.tick(_snap(energy=1.0, beat_phase=0.0, bucket="build"), rig)
    assert eng.strobe_level == 0  # a guessed build: no strobe
    eng.tick(_snap(energy=1.0, beat_phase=0.0, bucket="build", drop_armed=True), rig)
    assert eng.strobe_level == 0  # armed but nothing rising yet
    eng.tick(_snap(energy=1.0, beat_phase=0.0, bucket="build", rising=True, rise_index=1.5), rig)
    assert eng.strobe_level == 0  # rising but not armed
    eng.tick(_snap(energy=0.2, beat_phase=0.0, bucket="break", drop_armed=True, rising=True, rise_index=1.5), rig)
    assert eng.strobe_level > 0  # armed + rising: the classifier's bucket doesn't matter


def test_pre_drop_strobe_rate_follows_the_rise_intensity():
    """Josh, 2026-09-17: "when intensity is building... that should cue"
    -- the rate is the rise index: BUILD_STROBE_START x STROBE_MAX where the
    rise begins (index 1.0), STROBE_MAX at RISE_FULL, sustained (never
    burst-gated dark), in whatever bucket the classifier thinks it is."""
    from livereact.core.rise import RISE_FULL, RISE_ON
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    seen = []
    for i, idx in enumerate((RISE_ON, 1.7, 1.9, 2.1, 2.4, RISE_FULL, 3.0)):
        clock.advance(3.75)
        eng.tick(_snap(bar=i * 2, beat_in_bar=1, beat_phase=0.0, bucket="break",
                       drop_armed=True, rising=True, rise_index=idx), rig)
        seen.append(eng.strobe_level)
    assert seen[0] == pytest.approx(BUILD_STROBE_START * STROBE_MAX)
    assert all(a <= b for a, b in zip(seen, seen[1:]))
    assert seen[5] == pytest.approx(STROBE_MAX) and seen[6] == pytest.approx(STROBE_MAX)
    assert all(v > 0 for v in seen)


def test_build_strobe_runs_through_the_candidate_and_ends_on_the_commit():
    """session-20260915-212918, Josh: the strobe "ended before the drop" --
    the candidate flag fired on the riser's crescendo 0.8 s early. The
    strobe now runs until the bucket actually flips to DROP (the same tick
    the wash and the look hard-cut)."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    rise = dict(drop_armed=True, rising=True, rise_index=1.6)
    eng.tick(_snap(bar=0, beat_in_bar=1, beat_phase=0.0, bucket="build", **rise), rig)
    clock.advance(0.5)
    eng.tick(_snap(bar=0, beat_in_bar=2, beat_phase=0.0, bucket="build", **rise), rig)
    assert eng.strobe_level > 0
    clock.advance(0.5)
    eng.tick(_snap(bar=0, beat_in_bar=3, beat_phase=0.0, bucket="build", drop_candidate=True, **rise), rig)
    assert eng.strobe_level > 0  # still strobing through the candidate window
    clock.advance(0.5)
    eng.tick(_snap(bar=0, beat_in_bar=4, beat_phase=0.0, bucket="drop"), rig)  # the commit
    assert eng.strobe_level == 0 and not eng.strobe_on


def test_drop_tail_strobe_is_the_ceiling_regardless_of_hit_intensity():
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    _enter_drop(eng, rig, drop_hit_intensity=0.86)
    eng.tick(_snap(energy=1.0, bar=7, beat_in_bar=3, beat_phase=0.0, bucket="drop", drop_hit_intensity=0.86), rig)
    assert eng.strobe_level == pytest.approx(round(STROBE_MAX * 255) / 255)


# --- the one software strobe toggle, par + wash bar (2026-09-15, third live
# capture: par vs bar out of sync by construction, par toggle erratic while
# the rate ramped, flashes invisible early in the build) ----------------------

def _dimmers(c, rig):
    """(par_l, bar_l) ch1 dimmer bytes."""
    return c.values[rig.fixture("par_l").offset], c.values[rig.fixture("bar_l").offset]


def test_par_and_wash_bar_flash_on_the_same_frames_at_the_cap_byte():
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    bar = rig.fixture("bar_l")
    rise = dict(drop_armed=True, rising=True, rise_index=1.6)
    eng.tick(_snap(bar=0, beat_in_bar=1, beat_phase=0.0, bucket="build", **rise), rig)
    seen_on = seen_off = False
    for _ in range(120):  # 3s at 40 Hz, 2 Hz strobe at the rise's start
        clock.advance(1 / 40)
        c = eng.tick(_snap(bar=0, beat_in_bar=1, beat_phase=0.0, bucket="build", **rise), rig)
        par_d, bar_d = _dimmers(c, rig)
        assert par_d == bar_d, "par and wash bar must toggle on the same DMX frame"
        assert par_d in (0, STROBE_FLASH_START_DMX)  # a build's first bars flash at the start byte
        assert c.values[bar.offset + 1] == 0, "wash bar native ch2 strobe must never be written"
        seen_on |= par_d == STROBE_FLASH_START_DMX
        seen_off |= par_d == 0
    assert seen_on and seen_off


def test_build_flash_brightness_climbs_to_the_cap_and_drop_tail_uses_the_cap():
    """Josh, after the fourth live test: "it can reach a higher peak ... raise
    it to 180." The build's flash byte ramps STROBE_FLASH_START_DMX -> the
    cap alongside the rate ramp; DROP/PEAK tail bursts flash at the cap."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    from livereact.core.rise import RISE_FULL, RISE_ON
    arm = dict(drop_armed=True, rising=True)
    eng.tick(_snap(bar=0, beat_in_bar=1, beat_phase=0.0, bucket="build", rise_index=RISE_ON, **arm), rig)
    assert eng.strobe_flash_dmx == STROBE_FLASH_START_DMX
    clock.advance(3.75)
    eng.tick(_snap(bar=3, beat_in_bar=1, beat_phase=0.0, bucket="build", rise_index=1.9, **arm), rig)
    assert STROBE_FLASH_START_DMX < eng.strobe_flash_dmx < STROBE_FLASH_DMX
    clock.advance(3.75)
    eng.tick(_snap(bar=8, beat_in_bar=1, beat_phase=0.0, bucket="build", rise_index=2.3, **arm), rig)
    assert STROBE_FLASH_START_DMX < eng.strobe_flash_dmx < STROBE_FLASH_DMX
    clock.advance(3.75)
    eng.tick(_snap(bar=16, beat_in_bar=1, beat_phase=0.0, bucket="build", rise_index=RISE_FULL, **arm), rig)
    assert eng.strobe_flash_dmx == STROBE_FLASH_DMX
    eng2 = ReactiveLayerEngine(FakeClock())
    _enter_drop(eng2, rig)
    c = eng2.tick(_snap(energy=1.0, bar=7, beat_in_bar=3, beat_phase=0.0, bucket="drop"), rig)
    assert eng2.strobe_flash_dmx == STROBE_FLASH_DMX == 180
    assert _dimmers(c, rig)[0] in (0, STROBE_FLASH_DMX)


def test_strobe_toggle_stays_regular_while_the_rate_ramps():
    """The old `(t * hz) % 1` phase jumped every tick the rate changed --
    measured live as 0.1s and 0.7s on/off runs where ~0.3s was expected.
    Accumulated phase keeps every run within one frame of 0.5/hz even as hz
    climbs continuously from 1 Hz to 4 Hz."""
    clock = FakeClock()
    clock.advance(60.0)  # the failure mode needed a large t -- start there
    sc = _StrobeClock(clock)
    dt = 1 / 40
    steps = 40 * 16  # 16s ramp
    runs = []  # [on?, length_in_frames, sum_of_hz_over_the_run]
    prev = None
    for i in range(steps):
        rate = STROBE_MAX * (0.25 + 0.75 * i / steps)
        clock.advance(dt)
        on = sc.advance(rate)
        hz = _StrobeClock.hz_for(rate)
        if prev is None or on != prev[0]:
            runs.append([on, 1, hz])
        else:
            runs[-1][1] += 1
            runs[-1][2] += hz
        prev = runs[-1]
    assert len(runs) > 60  # 1->4 Hz over 16s: ~80 half-periods
    for on, frames, hz_sum in runs[1:-1]:  # ignore the partial first/last runs
        expected = 0.5 / (hz_sum / frames) / dt  # half period at the run's own mean hz
        assert abs(frames - expected) <= 1.0, (on, frames, expected)


def test_a_fresh_strobe_moment_leads_with_a_flash():
    clock = FakeClock()
    sc = _StrobeClock(clock)
    clock.advance(0.37)  # arbitrary phase of wall time
    assert sc.advance(0.0) is False
    clock.advance(1 / 40)
    assert sc.advance(STROBE_MAX) is True  # first ON frame, regardless of when it starts


def test_toggle_hz_never_exceeds_the_sustained_safety_ceiling():
    assert _StrobeClock.hz_for(STROBE_MAX) == STROBE_TOGGLE_MAX_HZ
    assert _StrobeClock.hz_for(1.0) == STROBE_TOGGLE_MAX_HZ  # over-range rates clamp, never exceed


# --- phrase-aligned DROP/PEAK strobe (2026-09-15) --------------------------
# The held-constant rate turned the burst gate into a 9.5s clock (measured:
# 21 bursts, all 1.4-1.5s, spaced 9.4-9.6s, random beat positions). Now the
# ambient strobe only fires in the last STROBE_PHRASE_TAIL_BEATS of every
# STROBE_PHRASE_BARS-bar phrase counted from the drop's own downbeat.

def _enter_drop(eng: ReactiveLayerEngine, rig, entry_bar: int = 0, **kw):
    """Tick once at the drop's downbeat so the engine records its phrase origin."""
    return eng.tick(_snap(energy=1.0, bar=entry_bar, beat_in_bar=1, beat_phase=0.0, bucket="drop", **kw), rig)


def test_drop_strobe_fires_only_in_the_tail_of_each_phrase():
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    _enter_drop(eng, rig, entry_bar=10)
    # bars 10..16 (phrase bars 0..6) -- silent on every beat, including beat 4
    for b in range(10, 17):
        for beat in (1, 2, 3, 4):
            eng.tick(_snap(energy=1.0, bar=b, beat_in_bar=beat, beat_phase=0.0, bucket="drop"), rig)
            assert eng.strobe_level == 0, (b, beat)
    # bar 17 (phrase bar 7): beats 1-2 still silent, beats 3-4 strobe
    for beat, expect in ((1, False), (2, False), (3, True), (4, True)):
        eng.tick(_snap(energy=1.0, bar=17, beat_in_bar=beat, beat_phase=0.0, bucket="drop"), rig)
        assert (eng.strobe_level > 0) is expect, beat


def test_drop_strobe_repeats_on_the_next_phrase_and_is_capped():
    """Cap value itself is Josh's explicit call (spec/FINDINGS.md SAFE-01/02/
    03/05/06 -- still an interim placeholder, still no real Hz calibration):
    raised 2026-09-14 from 0.15 to 0.75 after he watched a full 0-255 sweep
    live on the real wash bar -- import STROBE_MAX rather than hardcode it
    here, so this test can't silently drift from the real constant again."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _enter_drop(eng, rig)
    eng.tick(_snap(energy=1.0, bar=7, beat_in_bar=3, beat_phase=0.0, bucket="drop"), rig)
    assert 0 < eng.strobe_level <= STROBE_MAX + 1 / 255
    clock.advance(1.0)
    eng.tick(_snap(energy=1.0, bar=8, beat_in_bar=1, beat_phase=0.0, bucket="drop"), rig)  # tail over
    clock.advance(13.0)  # ~7 bars later at 128bpm, well past STROBE_MIN_SPACING_S
    eng.tick(_snap(energy=1.0, bar=15, beat_in_bar=3, beat_phase=0.0, bucket="drop"), rig)
    assert eng.strobe_level > 0


def test_peak_keeps_the_phrase_origin_of_the_drop_it_escalated_from():
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    _enter_drop(eng, rig, entry_bar=3)
    eng.tick(_snap(energy=1.0, bar=6, beat_in_bar=1, beat_phase=0.0, bucket="peak"), rig)  # escalates
    eng.tick(_snap(energy=1.0, bar=8, beat_in_bar=4, beat_phase=0.0, bucket="peak"), rig)
    assert eng.strobe_level == 0   # bar 8 is phrase bar 5, not a tail
    eng.tick(_snap(energy=1.0, bar=10, beat_in_bar=4, beat_phase=0.0, bucket="peak"), rig)
    assert eng.strobe_level > 0    # bar 10 is phrase bar 7 -- counted from bar 3, not 6


# --- strobe burst-duration cap + minimum spacing (LIGHTING_BIBLE.md §13,
# 2026-09-08 audit; spacing CONFIRMED BUGGY then fixed same-day evening via a
# real --hardware --record capture, see [[livereact-project]] memory and
# STROBE_MIN_SPACING_S's own comment in reactive.py for the full story) ---
# Previously a sustained loud section could hold the strobe on indefinitely --
# no burst-duration limit at all. Capped at STROBE_MAX_BURST_S, then a gap --
# but the FIRST gap value (1.5s, sized to match the burst duration, not to
# space separate moments apart) let a sustained loud section re-trigger
# almost immediately, confirmed live as a continuous on/off "stutter," not a
# rare accent. STROBE_MIN_SPACING_S (8.0s) replaces it, and now applies
# whether a moment ends by hitting the cap OR by energy simply dropping first
# -- both used to be handled differently, which was itself part of the bug.


def _tail(**kw):
    """A snapshot sitting in a drop's phrase tail (phrase bar 7, beat 3)
    when the drop was entered at bar 0 -- the ambient strobe WANTS to fire
    here, so these tests exercise the burst gate alone. Overridable."""
    base = dict(energy=1.0, bar=7, beat_in_bar=3, beat_phase=0.0, bucket="drop")
    base.update(kw)
    return _snap(**base)


def test_strobe_cuts_off_after_the_max_burst_duration():
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _enter_drop(eng, rig)
    # held in the tail throughout -- would strobe continuously without the gate
    eng.tick(_tail(), rig)
    assert eng.strobe_level > 0  # engaged at the start of the burst
    clock.advance(1.5)  # == STROBE_MAX_BURST_S
    eng.tick(_tail(), rig)
    assert eng.strobe_level == 0  # forced dark once the burst limit is hit


def test_strobe_stays_off_for_the_minimum_spacing_then_resumes():
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _enter_drop(eng, rig)
    eng.tick(_tail(), rig)
    clock.advance(1.5)
    eng.tick(_tail(), rig)  # burst cap trips here
    clock.advance(5.0)  # still well within STROBE_MIN_SPACING_S (8.0s)
    eng.tick(_tail(), rig)
    assert eng.strobe_level == 0  # still gapped, even though the tail condition holds
    clock.advance(3.1)  # now past the 8.0s minimum spacing
    eng.tick(_tail(), rig)
    assert eng.strobe_level > 0  # allowed to strobe again


def test_strobe_ending_naturally_also_earns_the_minimum_spacing():
    """THE ACTUAL CONFIRMED BUG (2026-09-08 real capture): a moment ending
    naturally (energy drops below threshold before hitting the duration cap)
    used to earn NO spacing at all -- only hitting the cap did -- so a
    sustained kick-heavy passage could re-trigger on literally the next tick.
    Replayed against real captured energy_norm: 11 separate bursts crammed
    into 36 real seconds. Both paths must now enforce the same spacing."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _enter_drop(eng, rig)
    eng.tick(_tail(), rig)
    clock.advance(0.5)  # well under the 1.5s cap
    # Leaving the phrase tail (here: the bucket exits entirely) is what ends
    # a moment "naturally" now, not a raw energy drop.
    eng.tick(_tail(bucket="break"), rig)  # bucket exits, strobe naturally off
    clock.advance(0.1)  # nowhere near STROBE_MIN_SPACING_S (8.0s)
    _enter_drop(eng, rig)  # a fresh drop lands immediately...
    eng.tick(_tail(), rig)
    assert eng.strobe_level == 0  # ...but the gate still owes spacing from the last moment
    clock.advance(8.0)  # now past the minimum spacing
    eng.tick(_tail(), rig)
    assert eng.strobe_level > 0  # allowed to strobe again


# --- build_snare_accent (rebuilt 2026-09-15 night): RollRiseDetector drives
# the accent -- see core/snare_trigger.py for the real-capture numbers.

from livereact.engines.reactive import STROBE_ROLL_FLASH_DMX  # noqa: E402


def _build_snap(high: float, **kw) -> ShowSnapshot:
    base = dict(audio_fresh=True, bucket="build", energy=0.5, energy_norm=0.5, beat_in_bar=1,
                beat_phase=0.0, bar=0, audio_band_high_abs=high)
    base.update(kw)
    return _snap(**base)


def _run_build(eng, rig, clock, high, seconds, dt=0.02, **kw):
    for _ in range(int(seconds / dt)):
        eng.tick(_build_snap(high, **kw), rig)
        clock.advance(dt)


def test_a_roll_rise_in_a_build_pushes_the_strobe_to_max_and_the_flash_to_full():
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _run_build(eng, rig, clock, high=0.12, seconds=3.0)   # early build: ramp well below the ceiling
    assert not eng.snare_roll_active
    assert eng.strobe_level < STROBE_MAX - 1e-6
    assert eng.strobe_flash_dmx < STROBE_ROLL_FLASH_DMX
    _run_build(eng, rig, clock, high=0.45, seconds=1.5)   # the roll (ARM_S is 1.0 s)
    assert eng.snare_roll_active
    assert abs(eng.strobe_level - round(STROBE_MAX * 255) / 255) < 1e-6 or eng.strobe_level == STROBE_MAX
    assert eng.strobe_flash_dmx == STROBE_ROLL_FLASH_DMX


def test_a_density_roll_at_a_flat_level_pushes_the_strobe_to_max_too():
    """2026-09-17: run 5's roll never moved the high band's level; its onset
    rate doubled. The density path must drive the same accent."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _run_build(eng, rig, clock, high=0.4, seconds=3.0, audio_high_onset_rate=4.3)
    assert not eng.snare_roll_active
    _run_build(eng, rig, clock, high=0.4, seconds=1.5, audio_high_onset_rate=8.5)
    assert eng.snare_roll_active
    assert eng.roll_path == "density"
    assert eng.strobe_flash_dmx == STROBE_ROLL_FLASH_DMX


def test_the_roll_accent_runs_through_the_candidate_and_ends_on_the_commit():
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _run_build(eng, rig, clock, high=0.12, seconds=3.0)
    _run_build(eng, rig, clock, high=0.45, seconds=1.5)
    assert eng.snare_roll_active
    eng.tick(_build_snap(0.45, drop_candidate=True), rig)  # the candidate is up, the roll is still going
    assert eng.strobe_level > 0 and eng.strobe_flash_dmx == STROBE_ROLL_FLASH_DMX
    _run_build(eng, rig, clock, high=0.10, seconds=0.4, bucket="drop")  # the commit; the roll ends as the level falls
    assert not eng.snare_roll_active


def test_a_roll_in_a_drop_is_an_accent_too():
    """Josh, 2026-09-17: no bucket may veto the accent (the classifier gets
    sections wrong). In a drop the roll's max-rate strobe wins the merge."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _run_build(eng, rig, clock, high=0.4, seconds=3.0, bucket="drop", energy_norm=0.9, audio_high_onset_rate=4.1)
    _run_build(eng, rig, clock, high=0.4, seconds=1.5, bucket="drop", energy_norm=0.9, audio_high_onset_rate=8.5)
    assert eng.snare_roll_active
    assert eng.strobe_flash_dmx == STROBE_ROLL_FLASH_DMX


def test_a_roll_in_a_break_is_an_accent_too():
    """Run 7 (2026-09-17): the build read BREAK, the roll was gated out."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    _run_build(eng, rig, clock, high=0.4, seconds=3.0, bucket="break", energy_norm=0.1, audio_high_onset_rate=4.1)
    _run_build(eng, rig, clock, high=0.4, seconds=1.5, bucket="break", energy_norm=0.1, audio_high_onset_rate=8.5)
    assert eng.snare_roll_active
    assert eng.strobe_flash_dmx == STROBE_ROLL_FLASH_DMX


# --- bucket-driven wash/par model (LIGHTING_BIBLE.md §5/§9.1, 2026-09-08) ---
# Replaces kick-follow as wash/par's default (see reactive.py's retirement
# note): DROP is a held constant, BUILD ramps structurally from bar position,
# BREAKDOWN/HOLD get a slow non-pulsing "breathing" envelope.


def test_drop_bucket_holds_a_constant_intensity_regardless_of_beat():
    """peak_full_rig/drop_blackout_hit: "reactivity: static, kick_follow:
    false" -- DROP must NOT vary with beat_phase or kick data at all,
    EXCEPT the deliberate beat-4 accent flash (Josh's own directed design
    call, 2026-09-13, see BEAT4_FLASH_PEAK/_beat4_flash) -- both ticks here
    are on beat_in_bar 1 (the `_snap` default), so the flash never engages
    and this invariant holds exactly as before."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    bar = rig.fixture("bar_l")
    on_beat = eng.tick(_snap(energy=0.9, beat_phase=0.0, audio_band_low_abs=50.0, drop_hit_intensity=0.9), rig)
    late_beat = eng.tick(_snap(energy=0.9, beat_phase=0.9, audio_band_low_abs=0.0, drop_hit_intensity=0.9), rig)
    assert on_beat.values[bar.offset] == late_beat.values[bar.offset] > 0
    assert eng.wash_level == pytest.approx(_drop_held_level(0.9))  # held, not the raw hit


def test_drop_and_peak_flash_on_beat_four_but_not_other_beats():
    """Josh, 2026-09-13 (after two real hardware sessions): DROP/PEAK wash
    was "relatively static" and read as dull with only the moving head
    beating -- explicit ask: flash on beat 4, not every beat, not
    overkill. HTP-style: only ever raises the level above the held base,
    never dims it."""
    rig = load_rig()
    bar = rig.fixture("bar_l")

    for bucket, kwargs in [("drop", dict(drop_hit_intensity=0.5)), ("peak", dict(drop_hit_intensity=0.5))]:
        eng = ReactiveLayerEngine(FakeClock())
        beat1 = eng.tick(_snap(energy=0.9, bucket=bucket, beat_in_bar=1, beat_phase=0.0, **kwargs), rig)
        beat3 = eng.tick(_snap(energy=0.9, bucket=bucket, beat_in_bar=3, beat_phase=0.0, **kwargs), rig)
        beat4_start = eng.tick(_snap(energy=0.9, bucket=bucket, beat_in_bar=4, beat_phase=0.0, **kwargs), rig)
        beat4_late = eng.tick(_snap(energy=0.9, bucket=bucket, beat_in_bar=4, beat_phase=0.9, **kwargs), rig)

        assert beat1.values[bar.offset] == beat3.values[bar.offset]  # untouched on non-4 beats
        assert beat4_start.values[bar.offset] > beat1.values[bar.offset], bucket  # flash raises it
        assert beat4_start.values[bar.offset] > beat4_late.values[bar.offset], bucket  # decays across beat 4 itself
        assert beat4_late.values[bar.offset] >= beat1.values[bar.offset], bucket  # HTP -- never dips below the held base


def test_beat_four_flash_is_a_visible_excursion_not_a_sliver():
    """FOUND LIVE (recordings/session-20260915-114303): mean wash-bar
    brightness on beat 4 was 0.965 vs 0.964 on beats 1-3 -- the flash
    was mathematically invisible against a held level of 0.92-1.0.
    Now the held level leaves headroom (DROP_HELD_*) and the flash peaks
    at full, falling back to held over BEAT4_FLASH_DECAY_PHASE of the
    beat, so it's still clearly above held a third of the way in."""
    rig = load_rig()
    bar = rig.fixture("bar_l")
    for hit in (0.85, 1.0):  # quietest and loudest confirmed drops alike
        eng = ReactiveLayerEngine(FakeClock())
        held = eng.tick(_snap(energy=1.0, bucket="drop", beat_in_bar=1, beat_phase=0.0, drop_hit_intensity=hit), rig)
        peak = eng.tick(_snap(energy=1.0, bucket="drop", beat_in_bar=4, beat_phase=0.0, drop_hit_intensity=hit), rig)
        third = eng.tick(_snap(energy=1.0, bucket="drop", beat_in_bar=4, beat_phase=0.2, drop_hit_intensity=hit), rig)
        assert peak.values[bar.offset] == 255, hit
        assert peak.values[bar.offset] - held.values[bar.offset] >= 90, hit   # >=35% of full scale
        assert third.values[bar.offset] - held.values[bar.offset] >= 40, hit  # still clearly up, 1/3 in


def test_held_drop_level_stays_at_or_under_the_strobe_brightness_cap():
    """A strobe burst clamps ch1 to WashBar.STROBE_BRIGHTNESS_CAP_DMX at
    finalize (Josh, 2026-09-14). With the held level above that byte, each
    burst read live as a ~45% brightness CUT ("they just strobe and then
    turn off really quick"). The held level's top of range must render at
    or near the cap so a burst is never a visible dim."""
    rig = load_rig()
    bar = rig.fixture("bar_l")
    eng = ReactiveLayerEngine(FakeClock())
    c = eng.tick(_snap(energy=1.0, bucket="drop", beat_in_bar=1, beat_phase=0.0, drop_hit_intensity=1.0), rig)
    assert c.values[bar.offset] <= WashBar.STROBE_BRIGHTNESS_CAP_DMX + 2


def test_build_bucket_ramps_from_bar_position_not_audio():
    """build_riser_wash: "the ramp IS the build; drive it from bar position,
    not audio" -- climbs over BUILD_RAMP_BARS (8) regardless of kick data.
    Checks eng.wash_level (the continuous pre-gamma-curve value) directly --
    the actual DMX byte goes through a nonlinear curve (LIGHTING_BIBLE.md
    §3.7), so a naive linear byte comparison would be testing the wrong
    thing (found via testing)."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())

    eng.tick(_snap(energy=0.4, bar=0, beat_in_bar=1, beat_phase=0.0,
                    audio_band_low_abs=0.0), rig)
    start = eng.wash_level
    eng.tick(_snap(energy=0.4, bar=4, beat_in_bar=1, beat_phase=0.0,
                    audio_band_low_abs=999.0), rig)  # audio ignored
    mid = eng.wash_level
    eng.tick(_snap(energy=0.4, bar=8, beat_in_bar=1, beat_phase=0.0,
                    audio_band_low_abs=0.0), rig)
    full = eng.wash_level

    assert start == 0.2                    # BUILD_LOW
    assert mid == pytest.approx(0.575)     # halfway: 0.2 + 0.5*(0.95-0.2)
    assert full == 0.95                    # BUILD_HIGH
    assert start < mid < full


def test_build_ramp_restarts_on_a_fresh_entry():
    """Leaving BUILD and re-entering later must restart the ramp from
    BUILD_LOW, not continue from wherever it left off. Re-entry now crosses
    the bucket-change CROSSFADE (2026-09-07, see WASH_CROSSFADE_TAU_S in
    reactive.py) since this is a DROP->BUILD transition, not a DROP entry --
    so the immediate value blends toward BUILD_LOW rather than snapping,
    settling there once WASH_TRANSITION_WINDOW_S has passed."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)

    eng.tick(_snap(energy=0.4, bar=0, beat_in_bar=1, beat_phase=0.0), rig)
    eng.tick(_snap(energy=0.4, bar=8, beat_in_bar=1, beat_phase=0.0), rig)  # ramp completes
    eng.tick(_snap(energy=0.9, bar=9, beat_in_bar=1, beat_phase=0.0, drop_hit_intensity=0.9), rig)  # a drop happens
    assert eng.wash_level == pytest.approx(_drop_held_level(0.9))  # entering DROP always snaps instantly

    clock.advance(0.05)
    eng.tick(_snap(energy=0.4, bar=20, beat_in_bar=1, beat_phase=0.0), rig)  # BUILD re-entered
    just_after = eng.wash_level
    assert 0.2 < just_after < 0.9  # blending toward BUILD_LOW, not yet snapped either way

    clock.advance(2.0)  # past WASH_TRANSITION_WINDOW_S -- blend has settled
    eng.tick(_snap(energy=0.4, bar=20, beat_in_bar=1, beat_phase=0.0), rig)
    assert eng.wash_level == 0.2  # back to BUILD_LOW, not still at BUILD_HIGH/DROP


def test_breakdown_bucket_breathes_slowly_toward_its_range():
    """break_low_wash: "a break has no transients to chase, so only a slow
    envelope earns reactivity" -- rises gradually, never instantly, and
    never leaves the Bible's stated 15-35% range."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)

    eng.tick(_snap(energy=0.25, beat_phase=0.0), rig)
    first = eng.wash_level
    for _ in range(50):
        clock.advance(0.5)
        eng.tick(_snap(energy=0.25, beat_phase=0.0), rig)
    settled = eng.wash_level

    assert first < settled  # rose over time, didn't jump instantly
    for value in (first, settled):
        assert 0.15 <= value <= 0.35


def test_non_drop_bucket_change_crossfades_instead_of_snapping():
    """The generalized fix (2026-09-07 real --hardware --record capture, see
    [[livereact-project]] memory): ANY bucket change other than entering
    DROP or PEAK should blend, not snap. Bible-integration Phase 2 (HOLD
    removed): reworked to use BUILD (ramped up to its ceiling) -> BREAK as
    the large-gap pair, since HOLD no longer exists as a bucket at all."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)

    # settle into BUILD, ramped all the way to its ceiling (BUILD_HIGH=0.95)
    eng.tick(_snap(energy=0.5, bar=0, beat_in_bar=1, beat_phase=0.0, bucket="build"), rig)
    for bar in range(1, 9):
        clock.advance(0.5)
        eng.tick(_snap(energy=0.5, bar=bar, beat_in_bar=1, beat_phase=0.0, bucket="build"), rig)
    # one more tick right after the loop's last advance, so _last_bucket_tick_t
    # reflects "now" -- otherwise the upcoming small clock.advance(0.05) would
    # actually hand the crossfade the loop's full unconsumed 0.5s dt instead
    eng.tick(_snap(energy=0.5, bar=8, beat_in_bar=1, beat_phase=0.0, bucket="build"), rig)
    settled_build = eng.wash_level
    assert settled_build >= 0.9  # ramped to (near) BUILD_HIGH=0.95

    # drop straight to BREAK
    clock.advance(0.05)
    eng.tick(_snap(energy=0.15, beat_phase=0.0, bucket="break"), rig)
    just_after = eng.wash_level
    assert just_after > 0.5  # still blending down, not snapped into BREAK's own range yet

    clock.advance(2.0)  # past WASH_TRANSITION_WINDOW_S -- blend has settled
    eng.tick(_snap(energy=0.15, beat_phase=0.0, bucket="break"), rig)
    assert eng.wash_level <= 0.35  # now genuinely settled into BREAK's range


def test_old_hold_range_energy_now_ramps_like_build():
    """Bible-integration Phase 2, Open Question #1's default: HOLD (the old
    0.55-0.80 energy range, no Bible equivalent) is REMOVED -- that range now
    folds into BUILD's upper tail, so it should follow BUILD's bar-position
    ramp, not a fixed breathing envelope. energy=0.65 used to mean "HOLD";
    it's squarely inside BUILD's range now (0.30-0.80)."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())

    eng.tick(_snap(energy=0.65, bar=0, beat_in_bar=1, beat_phase=0.0, bucket="build"), rig)
    start = eng.wash_level
    eng.tick(_snap(energy=0.65, bar=8, beat_in_bar=1, beat_phase=0.0, bucket="build"), rig)
    full = eng.wash_level

    assert start == 0.2   # BUILD_LOW, at entry -- not a breathing-envelope value
    assert full == 0.95   # BUILD_HIGH, after a full ramp -- confirms it's really following BUILD


def test_peak_holds_a_constant_intensity_above_drop():
    """Bible-integration Phase 2 (+ 2026-09-11 correction): PEAK is
    genuinely new, reached only by escalating out of a sustained DROP
    (core/metronome.py) -- static like DROP, with a small additive boost on
    top of that SAME drop's captured hit intensity ("more committed than
    the drop" scales with how big the drop already was)."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())
    eng.tick(_snap(energy=1.0, bucket="peak", drop_hit_intensity=0.9), rig)
    assert eng.wash_level == pytest.approx(_drop_held_level(0.9) + PEAK_BOOST)
    assert eng.wash_level > _drop_held_level(0.9)  # brighter than the drop it escalated from


def test_peak_intensity_scales_with_the_drop_it_escalated_from():
    """2026-09-11 correction: PEAK isn't a fixed ceiling -- a bigger drop
    escalates to a brighter PEAK than a smaller one."""
    rig = load_rig()
    quiet_peak = ReactiveLayerEngine(FakeClock())
    quiet_peak.tick(_snap(energy=1.0, bucket="peak", drop_hit_intensity=0.85), rig)
    loud_peak = ReactiveLayerEngine(FakeClock())
    loud_peak.tick(_snap(energy=1.0, bucket="peak", drop_hit_intensity=1.0), rig)
    assert loud_peak.wash_level > quiet_peak.wash_level
    assert loud_peak.wash_level == pytest.approx(DROP_HELD_HIGH + PEAK_BOOST)  # top of the held range, plus the boost


def test_drop_intensity_reflects_the_actual_hit_not_a_fixed_constant():
    """2026-09-11 correction: a bigger real hit renders brighter than a
    smaller one, both still classified DROP."""
    rig = load_rig()
    quiet_drop = ReactiveLayerEngine(FakeClock())
    quiet_drop.tick(_snap(energy=0.85, bucket="drop", drop_hit_intensity=0.85), rig)
    loud_drop = ReactiveLayerEngine(FakeClock())
    loud_drop.tick(_snap(energy=1.0, bucket="drop", drop_hit_intensity=1.0), rig)
    assert loud_drop.wash_level > quiet_drop.wash_level
    # 2026-09-15: rendered onto the DROP_HELD_* range, not the raw hit --
    # the raw 0.85-1.0 left no headroom for the beat-4 accent (see reactive.py)
    assert quiet_drop.wash_level == pytest.approx(DROP_HELD_LOW)
    assert loud_drop.wash_level == pytest.approx(DROP_HELD_HIGH)


def test_outro_fades_down_from_bar_position_not_audio():
    """outro_fade_wash: "wash 40->15%" -- a one-way bar-position-driven
    decay, same mechanism shape as BUILD's ramp, just descending."""
    rig = load_rig()
    eng = ReactiveLayerEngine(FakeClock())

    eng.tick(_snap(energy=0.1, bar=0, beat_in_bar=1, beat_phase=0.0, bucket="outro"), rig)
    start = eng.wash_level
    eng.tick(_snap(energy=0.1, bar=16, beat_in_bar=1, beat_phase=0.0, bucket="outro"), rig)
    full = eng.wash_level

    assert start == 0.40           # OUTRO_HIGH, at entry
    assert abs(full - 0.15) < 1e-9  # OUTRO_LOW, after a full fade (OUTRO_FADE_BARS=16)


# --- Synth Sync (Josh, 2026-09-15): wash follows the mid band; strobes override it (2026-09-19) ---
# See engines/reactive.py's SYNTH_* block.

from livereact.engines.reactive import SYNTH_CEILING, SYNTH_FLOOR  # noqa: E402
from livereact.merge.merge import ManualParams  # noqa: E402


def _synth_engine(on: bool = True):
    manual = ManualParams(synth_sync=on)
    return ReactiveLayerEngine(FakeClock(), manual=manual), manual


def _mid_snap(mid: float, **kw) -> ShowSnapshot:
    base = dict(audio_fresh=True, audio_band_mid_abs=mid, energy=0.9, bucket="drop",
                drop_hit_intensity=1.0, beat_in_bar=1, beat_phase=0.0, bar=0)
    base.update(kw)
    return _snap(**base)


def _settle_mid(eng, rig, level=1.0, ticks=300, dt=0.02, **kw):
    for _ in range(ticks):
        eng.tick(_mid_snap(level, **kw), rig)
        eng.clock.advance(dt)


def test_synth_sync_sits_at_the_floor_on_a_steady_mid_band():
    rig = load_rig()
    eng, _ = _synth_engine()
    _settle_mid(eng, rig, level=1.0)
    eng.tick(_mid_snap(1.0), rig)
    assert eng.synth_sync_active
    assert abs(eng.wash_level - SYNTH_FLOOR) < 0.05  # normalized envelope idles just above the floor


def test_synth_sync_a_stab_lifts_the_wash_toward_the_ceiling():
    rig = load_rig()
    eng, _ = _synth_engine()
    _settle_mid(eng, rig, level=1.0)
    c = eng.tick(_mid_snap(6.0), rig)  # a chord stab: 6x the mids' baseline
    assert eng.wash_level > 0.7  # one attack step at 60 Hz-ish dt lands ~0.75; the ceiling needs a second tick
    assert eng.wash_level <= SYNTH_CEILING
    par = rig.fixture("par_l")
    assert c.values[par.offset] > 120  # the dimmer byte actually moved (gamma curve, see _intensity_to8)


def test_synth_sync_lets_the_build_strobe_and_the_drop_tail_burst_override_it():
    # Josh, 2026-09-19: Synth Sync stays on for whole sets now, so the
    # automated strobes must cut through it (they used to be zeroed).
    rig = load_rig()
    eng, _ = _synth_engine()
    plain = ReactiveLayerEngine(FakeClock())
    # armed + rising: the sustained pre-drop strobe runs, same rate as without Synth Sync
    build = dict(bucket="build", energy=0.5, drop_armed=True, rising=True, rise_index=1.8)
    a = eng.tick(_mid_snap(1.0, **build), rig)
    plain.tick(_mid_snap(1.0, **build), rig)
    assert eng.synth_sync_active
    assert eng.strobe_level > 0 and eng.strobe_level == plain.strobe_level
    par = rig.fixture("par_l")
    # the wash dimmer is on the strobe clock (flash byte or 0), not the synth envelope
    assert a.values[par.offset] in (0, eng.strobe_flash_dmx)
    # DROP phrase tail (bar 7 of the phrase, beat 3): the ambient burst fires too
    eng.tick(_mid_snap(1.0, bucket="drop", bar=7, beat_in_bar=3, drop_start_bar=0), rig)
    assert eng.strobe_level > 0


def test_synth_sync_off_is_byte_identical_to_an_engine_that_never_had_it():
    rig = load_rig()
    with_toggle, manual = _synth_engine(on=False)
    plain = ReactiveLayerEngine(FakeClock())
    for mid in (1.0, 4.0, 1.0, 6.0):
        a = with_toggle.tick(_mid_snap(mid), rig)
        b = plain.tick(_mid_snap(mid), rig)
        assert bytes(a.values) == bytes(b.values)
    assert not with_toggle.synth_sync_active


def test_synth_sync_falls_back_to_the_bucket_level_without_real_audio():
    rig = load_rig()
    eng, _ = _synth_engine()
    plain = ReactiveLayerEngine(FakeClock())
    snap = _mid_snap(4.0, audio_fresh=False)
    eng.tick(snap, rig)
    plain.tick(snap, rig)
    assert not eng.synth_sync_active
    assert eng.wash_level == plain.wash_level


def test_synth_sync_leaves_the_moving_head_alone():
    rig = load_rig()
    eng, _ = _synth_engine()
    plain = ReactiveLayerEngine(FakeClock())
    _settle_mid(eng, rig, level=1.0)
    _settle_mid(plain, rig, level=1.0)
    a = eng.tick(_mid_snap(4.0), rig)
    b = plain.tick(_mid_snap(4.0), rig)
    mh = rig.fixture("mh_l")
    assert a.values[mh.offset + 5] == b.values[mh.offset + 5]  # ch6 dimmer unchanged


def test_synth_sync_can_be_toggled_mid_set():
    rig = load_rig()
    eng, manual = _synth_engine(on=False)
    eng.tick(_mid_snap(1.0), rig)
    assert not eng.synth_sync_active
    manual.synth_sync = True
    eng.tick(_mid_snap(1.0), rig)
    assert eng.synth_sync_active
    manual.synth_sync = False
    eng.tick(_mid_snap(1.0), rig)
    assert not eng.synth_sync_active


# --- break breath (Josh, 2026-09-16): a low-depth synth envelope on the break wash

from livereact.engines.reactive import BREAK_SYNTH_DEPTH  # noqa: E402


def test_a_break_breathes_to_the_mids_by_at_most_the_depth():
    rig = load_rig()
    eng, _ = _synth_engine(on=False)
    plain = ReactiveLayerEngine(FakeClock())
    kw = dict(bucket="break", energy=0.2, energy_norm=0.2)
    _settle_mid(eng, rig, level=1.0, **kw)
    _settle_mid(plain, rig, level=1.0, **kw)
    base = plain.tick(_mid_snap(1.0, **kw), rig) and plain.wash_level
    eng.tick(_mid_snap(6.0, **kw), rig)  # a pad swell / stab in the mids
    assert base < eng.wash_level <= base + BREAK_SYNTH_DEPTH + 1e-6


def test_the_break_breath_is_not_applied_in_a_drop_or_under_synth_sync():
    rig = load_rig()
    eng, manual = _synth_engine(on=False)
    plain = ReactiveLayerEngine(FakeClock())
    _settle_mid(eng, rig, level=1.0)
    _settle_mid(plain, rig, level=1.0)
    eng.tick(_mid_snap(6.0), rig)     # bucket="drop" by default
    plain.tick(_mid_snap(6.0), rig)
    assert eng.wash_level == plain.wash_level
    manual.synth_sync = True
    eng.tick(_mid_snap(6.0, bucket="break"), rig)
    assert eng.synth_sync_active       # Synth Sync owns the wash outright, no extra breath on top


def test_the_strobe_stops_the_instant_the_roll_ends_and_stays_off_until_the_hit():
    """Run 11 (2026-09-17): the roll ended 2-3 s before each hit and the rise
    strobe filled the gap; Josh wants that gap silent -- the breath."""
    clock = FakeClock()
    rig = load_rig()
    eng = ReactiveLayerEngine(clock)
    arm = dict(bucket="break", drop_armed=True, rising=True, rise_index=2.2, audio_band_high_abs=0.4)
    _run_build(eng, rig, clock, high=0.4, seconds=3.0, audio_high_onset_rate=4.1, **arm)
    assert eng.strobe_level > 0                         # the rise strobe
    _run_build(eng, rig, clock, high=0.4, seconds=1.5, audio_high_onset_rate=8.5, **arm)
    assert eng.snare_roll_active                        # the roll: max
    _run_build(eng, rig, clock, high=0.4, seconds=0.6, audio_high_onset_rate=4.1, **arm)
    assert not eng.snare_roll_active
    assert eng.strobe_level == 0 and not eng.strobe_on  # the breath: nothing, though still armed + rising
    eng.tick(_snap(bucket="drop", drop_armed=False), rig)
    assert eng.strobe_level == 0                        # the hit: the drop look owns it


def test_a_planned_snare_roll_strobes_rising_with_a_ceiling_from_the_planned_hit():
    """Josh, 2026-10-28: the strobe runs through the planner's roll lead-in (it is part of the drop,
    where the phrase-tail rule kept it dark), escalating to the end, a regular drop's roll visibly
    under the big moment's (STRUCT-02 / STRUCT-08)."""
    from livereact.engines.reactive import ROLL_HIT_FLOOR, STROBE_ROLL_FLASH_DMX

    def ramp(hit):
        clock = FakeClock()
        rig, eng = load_rig(), ReactiveLayerEngine(clock)
        rates, flashes = [], []
        for i, p in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
            clock.advance(1.0)
            eng.tick(_snap(bar=10 + i, beat_in_bar=1, beat_phase=0.0, bucket="drop", plan_active=True,
                           planned_roll_progress=p, planned_hit=hit, drop_start_bar=10), rig)
            rates.append(eng.strobe_level)
            flashes.append(eng.strobe_flash_dmx)
        return rates, flashes

    big, big_flash = ramp(1.0)
    regular, _ = ramp(0.92)
    soft, _ = ramp(ROLL_HIT_FLOOR)
    assert all(v > 0 for v in big) and all(a < b for a, b in zip(big, big[1:]))
    assert big[-1] == pytest.approx(STROBE_MAX) and big_flash[-1] == STROBE_ROLL_FLASH_DMX
    assert soft[-1] < regular[-1] < big[-1]
    assert round(big[-1] * 255) - round(regular[-1] * 255) >= 20          # STRUCT-08's visible step
    assert big[-1] <= STROBE_MAX                                          # never past the 4 Hz line


def test_no_roll_strobe_where_the_plan_forbids_the_strobe():
    clock = FakeClock()
    rig, eng = load_rig(), ReactiveLayerEngine(clock)
    clock.advance(1.0)
    eng.tick(_snap(bar=10, beat_in_bar=1, bucket="drop", plan_active=True, planned_roll_progress=0.5,
                   planned_hit=1.0, planned_strobe_allowed=False), rig)
    assert eng.strobe_level == 0.0
