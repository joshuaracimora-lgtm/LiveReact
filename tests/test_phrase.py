"""Tests for the Phrase Engine: bar-boundary rotation, hold, suspend, crossfade."""

from __future__ import annotations

from livereact.core.show_state import ShowSnapshot
from livereact.engines.phrase import CROSSFADE_BARS, HOLD_BARS, ROTATION_BARS, PhraseEngine
from livereact.fixtures.rig import load_rig
from livereact.scenes.catalog import bucket_for_energy


def _snap(bar: int, **kw) -> ShowSnapshot:
    base = dict(bpm=124.0, energy=0.7, bar=bar, beat_in_bar=1, beat_phase=0.0,
                fader_a=1.0, eq_low_a=1.0)
    base.update(kw)
    # bucket_for_energy reads energy_norm, not raw energy (2026-09-07, found
    # live) -- default it to match energy so existing tests that only set
    # `energy=` still exercise the bucket they intend to.
    base.setdefault("energy_norm", base["energy"])
    # snap.bucket is normally an ALREADY-DEBOUNCED value from metronome.py --
    # default it from this snapshot's own energy_norm for the same reason.
    base.setdefault("bucket", bucket_for_energy(base["energy_norm"]).value)
    return ShowSnapshot(**base)


def test_rotation_fires_once_per_boundary_not_every_tick():
    rig = load_rig()
    eng = PhraseEngine()
    # Hold at bar 0 for many ticks: no rotation yet.
    for _ in range(5):
        eng.tick(_snap(0), rig)
    assert eng.rotations == 0
    # Reach the rotation cadence; ticking repeatedly on the same bar fires once.
    for _ in range(5):
        eng.tick(_snap(ROTATION_BARS), rig)
    assert eng.rotations == 1


def test_minimum_hold_blocks_early_rotation():
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(HOLD_BARS - 1), rig)
    assert eng.rotations == 0  # below the minimum hold


def test_per_look_min_hold_bars_overrides_the_catalog_wide_floor():
    """Gap fix, 2026-09-13: Scene.min_hold_bars ("per-look override of the
    catalog-wide HOLD_BARS," scenes/base.py's own docstring) was declared
    by dozens of Phase 5 scenes but never actually consulted here -- every
    look used the flat HOLD_BARS regardless. A look with a longer-than-
    default hold (BreakLowWash, min_hold_bars=16) must not rotate away just
    because the flat HOLD_BARS=4 floor has cleared."""
    from livereact.scenes.library import BreakLowWash
    rig = load_rig()
    eng = PhraseEngine()
    eng.force_scene(BreakLowWash())
    eng.skip()  # request a rotation as soon as it's legal
    eng.tick(_snap(HOLD_BARS), rig)  # past the flat default floor...
    assert eng.rotations == 0  # ...but not past BreakLowWash's own min_hold_bars=16
    eng.tick(_snap(16), rig)  # now past BreakLowWash's own floor
    assert eng.rotations == 1


def test_scene_without_min_hold_bars_falls_back_to_the_catalog_wide_floor():
    """A pre-Bible-integration scene (no min_hold_bars override, defaults to
    None) must still respect the flat HOLD_BARS floor, unchanged from
    before this fix."""
    from livereact.scenes.library import RisingTide
    rig = load_rig()
    eng = PhraseEngine()
    eng.force_scene(RisingTide())
    eng.skip()
    eng.tick(_snap(HOLD_BARS - 1), rig)
    assert eng.rotations == 0
    eng.tick(_snap(HOLD_BARS), rig)
    assert eng.rotations == 1


def test_rotation_suspended_during_transition_and_loop():
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(ROTATION_BARS, in_transition=True), rig)
    assert eng.rotations == 0
    eng.tick(_snap(ROTATION_BARS + 1, loop_active=True), rig)
    assert eng.rotations == 0
    # Flags cleared on a fresh bar -> it rotates.
    eng.tick(_snap(ROTATION_BARS + 2), rig)
    assert eng.rotations == 1


def test_combine_interpolates_color_instead_of_max_blending():
    """Bug fix, 2026-09-13 (found via the first real hardware session):
    _combine used to take a raw per-channel max() for color/dimmer, the
    exact anti-pattern Phase 4 fixed at the cross-engine level but never
    touched here -- confirmed live to freeze at a wrong, neither-scene
    color for an entire crossfade. Must now be a real weighted lerp."""
    from livereact.merge.merge import Contribution
    rig = load_rig()
    par = rig.fixture("par_l")

    out_c = Contribution()
    par.set_color(out_c, 0.0, 0.0, 0.0)   # outgoing: black (R=0)
    in_c = Contribution()
    par.set_color(in_c, 1.0, 0.0, 0.0)    # incoming: full red (R=255)

    r_ch = par.offset + 1  # ch2 = R on this Par layout (ch1 dimmer, 2/3/4 R/G/B)
    at_0 = PhraseEngine._combine(out_c, in_c, 0.0, rig)
    at_half = PhraseEngine._combine(out_c, in_c, 0.5, rig)
    at_1 = PhraseEngine._combine(out_c, in_c, 1.0, rig)

    assert at_0.values[r_ch] == 0
    assert at_1.values[r_ch] == 255
    assert 100 <= at_half.values[r_ch] <= 155  # genuinely ~halfway, not max()'d to 255 the instant `in_c` touches it


def test_combine_only_one_side_touching_is_unaffected_by_interpolation():
    """A channel only one side writes to (the overwhelmingly common case --
    most scenes never touch wash dimmer at all) must still just pass that
    value through untouched, same as before this fix."""
    from livereact.merge.merge import Contribution
    rig = load_rig()
    par = rig.fixture("par_l")

    out_c = Contribution()
    par.set_color(out_c, 0.5, 0.5, 0.5)
    in_c = Contribution()  # touches nothing

    result = PhraseEngine._combine(out_c, in_c, 0.3, rig)
    r_ch = par.offset + 1  # ch2 = R on this Par layout
    assert result.values[r_ch] == out_c.values[r_ch]


def test_combine_interpolates_movement_instead_of_snapping_at_midpoint():
    """Bug fix, 2026-09-13 (found via the second real hardware session):
    MOVEMENT used to be grouped with LASER under "snap at the midpoint" --
    confirmed live this reads as the moving head stopping dead for half the
    crossfade, then instantly cranking to the new position. Pan/tilt are
    continuous, not discrete like a laser mode index, so they now get a
    real lerp same as color."""
    from livereact.merge.merge import Contribution
    rig = load_rig()
    mh = rig.fixture("mh_l")

    out_c = Contribution()
    mh.set_position(out_c, 0.2, 0.5)   # outgoing: pan far left
    in_c = Contribution()
    mh.set_position(in_c, 0.8, 0.5)    # incoming: pan far right

    pan_ch = mh.offset  # ch1 = pan
    at_0 = PhraseEngine._combine(out_c, in_c, 0.0, rig)
    at_half = PhraseEngine._combine(out_c, in_c, 0.5, rig)
    at_1 = PhraseEngine._combine(out_c, in_c, 1.0, rig)

    assert at_0.values[pan_ch] == round(0.2 * 255)
    assert at_1.values[pan_ch] == round(0.8 * 255)
    # NOT a snap: the midpoint must be a real, distinct, in-between value --
    # not equal to either endpoint, and close to their numeric midpoint.
    assert at_0.values[pan_ch] < at_half.values[pan_ch] < at_1.values[pan_ch]
    assert abs(at_half.values[pan_ch] - round(0.5 * 255)) <= 2


def test_combine_still_snaps_laser_at_the_midpoint():
    """Laser mode/pattern channels are discrete enums -- landing on an
    undefined intermediate value mid-lerp is a real risk, not cosmetic.
    Must NOT be affected by the movement fix above."""
    from livereact.merge.merge import Contribution
    rig = load_rig()
    laser = rig.fixture("laser_l")

    out_c = Contribution()
    out_c.set(laser.offset + 1, 10)   # some pattern-select channel/value
    in_c = Contribution()
    in_c.set(laser.offset + 1, 200)

    ch = laser.offset  # 0-indexed for .values
    just_before_mid = PhraseEngine._combine(out_c, in_c, 0.49, rig)
    just_after_mid = PhraseEngine._combine(out_c, in_c, 0.51, rig)
    assert just_before_mid.values[ch] == 10   # still outgoing
    assert just_after_mid.values[ch] == 200   # snapped straight to incoming, no in-between value


def test_crossfade_completes_and_promotes_next_scene():
    rig = load_rig()
    eng = PhraseEngine()
    start = eng._current
    eng.tick(_snap(ROTATION_BARS), rig)          # begins crossfade
    assert eng._next is not None
    # Advance past the crossfade window (in bars).
    eng.tick(_snap(ROTATION_BARS + CROSSFADE_BARS, beat_in_bar=1, beat_phase=0.0), rig)
    assert eng._next is None
    assert eng._current is not start             # promoted to the new scene


def test_skip_forces_rotation_after_minimum_hold():
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0), rig)
    eng.skip()
    eng.tick(_snap(HOLD_BARS), rig)  # earlier than ROTATION_BARS, but past min hold
    assert eng.rotations == 1


def test_bucket_change_forces_rotation_after_minimum_hold():
    """2026-09-09 hardware capture (see [[livereact-project]] memory):
    scene rotation and bucket classification were two fully independent
    clocks -- a bucket could jump straight into DROP while the active
    scene (picked for a totally different energy state) kept running for
    up to a full ROTATION_BARS before catching up, measured as long as
    24s/14 bars on a real drop and directly producing "still blue during
    the drop." A bucket change should now prompt a rotation check on its
    own, same as an explicit skip -- still gated by HOLD_BARS, not an
    override of it."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)  # low energy: establishes a "breakdown" baseline bucket
    assert eng.rotations == 0
    # Energy jumps straight to a drop-level bucket, past the minimum hold
    # but nowhere near ROTATION_BARS -- should still rotate, unlike before.
    eng.tick(_snap(HOLD_BARS, energy=1.0), rig)
    assert eng.rotations == 1


def test_downward_family_change_also_bypasses_the_hold():
    """Reversed 2026-09-15 (third live capture, replayed): a DROP -> BREAK
    change that respected the outgoing drop look's 8-bar hold played a
    whole 8s break under the red drop look. Any bucket FAMILY change is
    already debounced upstream (core/metronome.py's CUT_EXIT_CONFIRM_S), so
    it rotates on the next bar regardless of the hold."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=1.0), rig)  # drop baseline
    eng.tick(_snap(HOLD_BARS - 1, energy=0.2), rig)  # falls to break, still inside the hold
    assert eng.rotations == 1
    assert eng._next is not None and eng._next.bucket.value == "break"


def test_peak_falling_back_to_drop_does_not_rotate():
    """PEAK -> DROP is an overlay flip on the same drop (same family, rank
    down): swapping looks on it was churn, and a crossfade that outlived
    the drop put the DROP look over the break that followed (replayed
    2026-09-15)."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)   # break baseline
    eng.tick(_snap(0, energy=1.0, beat_in_bar=3), rig)   # drop (hard cut)
    eng.tick(_snap(1, energy=1.0, bucket="peak"), rig)  # escalates: rank up -> rotates to the PEAK look
    assert eng.rotations == 2
    for bar in range(2, 5):
        eng.tick(_snap(bar, energy=1.0, bucket="peak"), rig)
    assert eng._current.bucket.value == "peak"
    eng.tick(_snap(5, energy=1.0, bucket="drop"), rig)  # back to plain DROP
    for bar in range(6, 6 + 2 * ROTATION_BARS):  # ...and the scheduler must not swap it either
        eng.tick(_snap(bar, energy=1.0, bucket="drop"), rig)
    assert eng.rotations == 2
    assert eng._current.bucket.value == "peak" and eng._next is None


def test_drop_entry_cuts_on_the_tick_it_commits_not_the_next_bar():
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)
    eng.tick(_snap(0, energy=0.2, beat_in_bar=2), rig)
    # bucket flips mid-bar (beat 3 of the SAME bar) -- no bar boundary
    eng.tick(_snap(0, energy=1.0, beat_in_bar=3, beat_phase=0.4), rig)
    assert eng.rotations == 1
    assert eng._current.bucket.value == "drop" and eng._next is None


def test_upward_bucket_change_bypasses_the_outgoing_looks_hold():
    """2026-09-15 (recordings/session-20260915-121503): an 11s build ran
    entirely under `Break Ceiling Drift` and the drop after it played its
    first 13s under the same break look, because the outgoing look's
    min_hold_bars re-gated a bucket change the metronome had already
    debounced. An escalation (into BUILD, or into DROP/PEAK) now rotates on
    the next bar boundary regardless of the hold -- and a DROP/PEAK
    escalation is a HARD CUT (2026-09-15, third capture): the drop look is
    `_current` on that same tick, no crossfade pending."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)  # break baseline
    eng.tick(_snap(1, energy=1.0), rig)  # -> drop, one bar in, well inside the hold
    assert eng.rotations == 1
    assert eng._next is None and eng._current.bucket.value == "drop"


def test_build_escalation_still_crossfades():
    """Only DROP/PEAK cut hard; BREAK -> BUILD keeps the normal crossfade."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)
    eng.tick(_snap(1, energy=0.5), rig)  # -> build
    assert eng.rotations == 1
    assert eng._next is not None and eng._next.bucket.value == "build"
    assert eng._current.bucket.value == "break"


def test_escalation_mid_crossfade_finishes_it_and_rotates_same_bar():
    """Third capture (session-20260915-125433): BUILD committed while a
    scheduled BREAK->BREAK crossfade was in flight, so the build look waited
    out the fade plus a bar. Now the in-flight fade completes instantly and
    the escalation rotates on the very bar it was seen."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)
    # the scheduler no longer rotates inside a break (see the break-hold tests
    # below); a skip (DJ, or the settle after a mix) still starts a crossfade there
    eng.skip()
    eng.tick(_snap(ROTATION_BARS, energy=0.2), rig)  # break -> break crossfade starts
    assert eng._next is not None and eng._next.bucket.value == "break"
    eng.tick(_snap(ROTATION_BARS + 1, energy=0.5), rig)  # -> build, mid-crossfade
    assert eng.rotations == 2
    assert eng._next is not None and eng._next.bucket.value == "build"


def test_single_look_bucket_does_not_rotate_into_itself():
    """The core catalog has exactly one DROP look: the scheduled
    ROTATION_BARS cadence must not crossfade it into itself."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)
    eng.tick(_snap(1, energy=1.0), rig)  # -> drop (hard cut)
    drop_scene = eng._current
    assert eng.rotations == 1
    for bar in range(2, 2 + 3 * ROTATION_BARS):
        eng.tick(_snap(bar, energy=1.0), rig)
    assert eng.rotations == 1
    assert eng._current is drop_scene and eng._next is None


def test_bucket_change_rotation_picks_a_scene_for_the_new_bucket():
    """Not just that it rotates sooner -- the scene it rotates TO must
    actually match the bucket that triggered it (LIGHTING_BIBLE.md's own
    per-bucket scene pools), since `_begin_rotation` reads `snap.bucket`
    fresh at fire time."""
    rig = load_rig()
    eng = PhraseEngine()
    eng.tick(_snap(0, energy=0.2), rig)
    eng.tick(_snap(HOLD_BARS, energy=1.0), rig)  # -> drop bucket (hard cut, so already current)
    assert eng._current.bucket.value == "drop"


def test_drop_scene_emits_laser_channels_through_the_engine():
    """FullSpectrumHit (the original scene this test used) was retired in
    Phase 6 -- DropLaserPunctuation is a real, still-current DROP-bucket
    laser look, and a cleaner example besides (laser-only, no wash/spot
    noise to also account for)."""
    rig = load_rig()
    from livereact.scenes.library import DropLaserPunctuation

    eng = PhraseEngine()
    eng.force_scene(DropLaserPunctuation())
    laser = rig.fixture("laser_l")
    c = eng.tick(_snap(2, energy=1.0), rig)
    assert any(c.touched[laser.offset + i] for i in range(12))


def test_break_look_holds_for_the_whole_break_without_scheduled_rotation():
    """A scheduled swap inside a break started a look that needs 16 bars
    (TIME-02) in a break that ended first -- on the ear-verified tracks it was
    cut by the drop after 0.1-8.8 bars every time (SWEETER 32.8/203.7 s,
    messy 133.7 s). LIGHTING_BIBLE.md §9.1: a break is slow with a static hue,
    so the break look holds until the bucket moves."""
    from livereact.scenes.library import BreakLowWash
    rig = load_rig()
    eng = PhraseEngine()
    eng.force_scene(BreakLowWash())
    for bar in range(0, 3 * ROTATION_BARS + 1):
        eng.tick(_snap(bar, energy=0.2, bucket="break"), rig)
    assert eng.rotations == 0 and eng._current.name == BreakLowWash().name


def test_break_look_also_holds_under_a_promoted_outro():
    """OUTRO is in the break family: an OUTRO promoted under a BREAK look (often
    a mid-track break, messy 136.1 s) keeps the look instead of swapping in an
    outro look the next drop cuts short."""
    from livereact.scenes.library import BreakLowWash
    rig = load_rig()
    eng = PhraseEngine()
    eng.force_scene(BreakLowWash())
    eng.tick(_snap(0, energy=0.2, bucket="break"), rig)
    for bar in range(1, 3 * ROTATION_BARS + 1):
        eng.tick(_snap(bar, energy=0.2, bucket="outro"), rig)
    assert eng.rotations == 0


def test_skip_still_rotates_a_break_look():
    """The hold is on the scheduler only: a DJ skip (or the settle after a mix)
    still changes the break look once its own min_hold_bars has passed."""
    from livereact.scenes.library import BreakLowWash
    rig = load_rig()
    eng = PhraseEngine()
    eng.force_scene(BreakLowWash())
    eng.tick(_snap(0, energy=0.2, bucket="break"), rig)
    eng.skip()
    eng.tick(_snap(16, energy=0.2, bucket="break"), rig)
    assert eng.rotations == 1


def test_break_look_still_leaves_when_the_bucket_leaves_the_break():
    from livereact.scenes.library import BreakLowWash
    rig = load_rig()
    eng = PhraseEngine()
    eng.force_scene(BreakLowWash())
    for bar in range(0, ROTATION_BARS + 4):
        eng.tick(_snap(bar, energy=0.2, bucket="break"), rig)
    eng.tick(_snap(ROTATION_BARS + 4, energy=1.0, bucket="drop"), rig)
    assert eng.rotations == 1 and eng._current.bucket.value == "drop"
