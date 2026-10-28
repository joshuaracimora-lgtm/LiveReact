"""Tests for the scene catalog: rendering, distinctness, laser gating, buckets."""

from __future__ import annotations

import pytest

from livereact.core.show_state import ShowSnapshot
from livereact.fixtures.rig import load_rig
from livereact.scenes.base import SAFE_DEFAULT, EnergyBucket, Scene
from livereact.scenes.catalog import SceneCatalog, bucket_for_energy
from livereact.scenes.library import ALL_SCENES, DeepWashBreathe, DropBlackoutHit


def _snap(**kw) -> ShowSnapshot:
    base = dict(bpm=124.0, energy=0.7, fader_a=1.0, eq_low_a=1.0, eq_mid_a=0.4, eq_high_a=0.4)
    base.update(kw)
    return ShowSnapshot(**base)


def test_thirty_scenes_across_all_buckets():
    """Bible-integration Phase 2: OUTRO was a genuinely new state (see
    EnergyBucket's own docstring) with no scene at all until Phase 5's
    fourth wave -- now every bucket has at least one look. 27 = the
    original 8 MINUS 2 (Phase 6 retired FullSpectrumHit/BeamFanLaserTunnel
    -- see the comment above the "drop / peak energy" section header in
    scenes/library.py for why) MINUS 4 (the 2026-09-15 polish pass deleted
    the non-Bible Slow Color Cycle/Converge/Pulse & Drift/Locked Hold, see
    ALL_SCENES' own comment) + Phase 5's first wave (DropBlackoutHit,
    DropStrobeStab, BuildRiserWash) + second wave (PeakFullRig,
    PeakColorPulseClear, PeakAltHitsClear) + third wave (BreakLowWash,
    BreakSingleSpotSpecial, BreakCeilingDrift, BreakColorHoldClear) +
    fourth wave (OutroFadeWash, OutroSingleBeam) + fifth wave (ChaseWashLr
    -- AllDark, like SafeDefault, is deliberately NOT part of ALL_SCENES,
    see scenes/base.py) + sixth wave (BuildBeamRaise, DropBeamFan,
    DropLaserPunctuation, PeakBeamSweep, PeakLaserTunnel, MirrorCrossBeam
    -- all 6 always degrade on this rig, but are real ALL_SCENES members so
    SceneCatalog actually exercises the degrade-walk machinery when it
    rotates onto one of them) + seventh wave (DropColorSlamBigroom,
    DropWashSlamClear, DropGoboProjectionClear, DropBlinderClear -- closes
    out Phase 5's look-building backlog; DropGoboProjectionClear always
    degrades to nothing, same as the "no gobo hardware" gap it documents)
    + one gap-fix (PeakStrobeBlackout, found during a 2026-09-13 Phase 1-6
    re-audit -- a real Bible look that all 7 waves had missed) + one more
    build-out (BuildSnareAccent, 2026-09-14 -- depended on new snare-roll
    subdivision detection, core/snare_trigger.py, built this session)."""
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    assert len(ALL_SCENES) == 28   # + DropSnareRollRamp (2026-10-28, the plan's roll sections)
    for bucket in EnergyBucket:
        assert cat.bucket(bucket), f"{bucket} has no scenes"


def test_every_scene_renders_some_output():
    rig = load_rig()
    snap = _snap()
    for cls in ALL_SCENES:
        c = cls().render(rig, snap, 1.0)
        assert any(c.touched), f"{cls.__name__} rendered nothing"


def test_only_drop_or_peak_scenes_touch_laser_channels():
    """One-directional: a BREAK/BUILD/OUTRO scene must NEVER touch laser
    channels. The reverse isn't required -- Bible-integration Phase 5 added
    DROP scenes (DropBlackoutHit, DropStrobeStab) that deliberately don't
    use lasers at all (restraint, TIME-21 -- not every DROP look needs
    every fixture). PEAK was added to the allowed set in the sixth wave
    (atmosphere-gated stubs): LIGHTING_BIBLE.md's `peak_laser_tunnel` is a
    genuinely PEAK-only laser look, not `[DROP,PEAK]` like most other
    laser-using looks -- so this invariant could no longer stay DROP-only."""
    rig = load_rig()
    snap = _snap(energy=1.0)
    laser = rig.fixture("laser_l")
    for cls in ALL_SCENES:
        scene = cls()
        c = scene.render(rig, snap, 1.0)
        touched_laser = any(c.touched[laser.offset + i] for i in range(12))
        if touched_laser:
            assert scene.bucket in (EnergyBucket.DROP, EnergyBucket.PEAK), cls.__name__


def test_scenes_are_visually_distinct():
    rig = load_rig()
    snap = _snap()
    a = DeepWashBreathe().render(rig, snap, 1.0).values
    b = DropBlackoutHit().render(rig, snap, 1.0).values
    assert a != b


def test_bucket_thresholds():
    """Bible-integration Phase 2: BUILD_MAX moved 0.55 -> 0.80 (HOLD's old
    range folded into BUILD's upper tail); PEAK/OUTRO aren't reachable from
    a raw level at all (see EnergyBucket's docstring), so this stateless
    function only ever returns BREAK/BUILD/DROP."""
    assert bucket_for_energy(0.1) == EnergyBucket.BREAK
    assert bucket_for_energy(0.45) == EnergyBucket.BUILD
    assert bucket_for_energy(0.7) == EnergyBucket.BUILD
    assert bucket_for_energy(0.95) == EnergyBucket.DROP


def test_round_robin_avoids_repeating_current():
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    first = cat.pick(EnergyBucket.DROP)
    second = cat.pick(EnergyBucket.DROP, exclude=first)
    assert second is not first


def test_pick_without_a_rig_skips_capability_gating_entirely():
    """Backward compatibility: existing callers that don't pass `rig` (the
    default, None) get byte-identical behavior to before this parameter
    existed -- confirmed by exercising a scene with an impossible
    `requires` that would fail any real gate check."""
    class _Impossible(Scene):
        name, bucket = "Impossible", EnergyBucket.DROP
        requires = frozenset({"a_capability_no_rig_has"})
    cat = SceneCatalog([_Impossible()])
    assert isinstance(cat.pick(EnergyBucket.DROP), _Impossible)  # no rig -> gate never runs


def test_pick_with_a_rig_filters_out_ineligible_scenes():
    class _NeedsHaze(Scene):
        name, bucket = "Needs Haze", EnergyBucket.DROP
        requires = frozenset({"laser"})
        min_atmosphere_density = 0.6
    class _Plain(Scene):
        name, bucket = "Plain", EnergyBucket.DROP
    rig = load_rig()  # has lasers, but atm is always 0.0 -- no haze hardware
    cat = SceneCatalog([_NeedsHaze(), _Plain()])
    for _ in range(5):  # round-robin would otherwise eventually return _NeedsHaze
        assert isinstance(cat.pick(EnergyBucket.DROP, rig), _Plain)


def test_pick_walks_one_degrade_hop_when_ineligible():
    class _Fallback(Scene):
        name, bucket = "Fallback", EnergyBucket.DROP
    class _NeedsHaze(Scene):
        name, bucket = "Needs Haze", EnergyBucket.DROP
        requires = frozenset({"laser"})
        min_atmosphere_density = 0.6
        degrade_to = _Fallback
    rig = load_rig()
    cat = SceneCatalog([_NeedsHaze(), _Fallback()])
    picked = cat.pick(EnergyBucket.DROP, rig)
    assert isinstance(picked, _Fallback)


def test_pick_falls_back_to_round_robin_if_nothing_is_eligible_even_degraded():
    """Never hard-fail selection (this project's standing "selection never
    fails" guarantee) -- even with zero eligible scenes anywhere, pick()
    still returns something rather than raising."""
    class _NeedsHaze(Scene):
        name, bucket = "Needs Haze", EnergyBucket.DROP
        min_atmosphere_density = 1.0
    rig = load_rig()
    cat = SceneCatalog([_NeedsHaze()])
    assert cat.pick(EnergyBucket.DROP, rig) is not None


def test_pick_excludes_a_scene_that_conflicts_with_the_current_one():
    """Bible-integration Phase 4, LOOK-06 + Open Question #4's approved
    default: a conflicting look is simply never selected while the
    conflicting one is active -- no forced pre-emption of the current look."""
    class _A(Scene):
        name, bucket = "A", EnergyBucket.DROP
        conflicts = frozenset({"B"})
    class _B(Scene):
        name, bucket = "B", EnergyBucket.DROP
    cat = SceneCatalog([_A(), _B()])
    a = cat.bucket(EnergyBucket.DROP)[0]
    for _ in range(5):  # round-robin would otherwise eventually return B
        assert cat.pick(EnergyBucket.DROP, exclude=a).name == "A"


def test_pick_conflict_check_is_bidirectional():
    """The Bible declares conflicts[] one-sidedly per look -- checked both
    ways so a look doesn't need to declare the conflict on both sides."""
    class _A(Scene):
        name, bucket = "A", EnergyBucket.DROP
        conflicts = frozenset({"B"})  # only A declares the conflict
    class _B(Scene):
        name, bucket = "B", EnergyBucket.DROP
    cat = SceneCatalog([_A(), _B()])
    b = cat.bucket(EnergyBucket.DROP)[1]
    for _ in range(5):
        assert cat.pick(EnergyBucket.DROP, exclude=b).name == "B"  # A excluded even though B didn't declare it


def test_pick_never_hard_fails_even_if_everything_conflicts():
    class _A(Scene):
        name, bucket = "A", EnergyBucket.DROP
        conflicts = frozenset({"B"})
    class _B(Scene):
        name, bucket = "B", EnergyBucket.DROP
        conflicts = frozenset({"A"})
    cat = SceneCatalog([_A(), _B()])
    a = cat.bucket(EnergyBucket.DROP)[0]
    assert cat.pick(EnergyBucket.DROP, exclude=a) is not None  # never fails to select something


# --- Bible-integration Phase 5, first wave: DropBlackoutHit, DropStrobeStab, BuildRiserWash

def test_drop_blackout_hit_alternates_blood_red_and_cold_steel_by_drop_cycle():
    from livereact.scenes.library import BLOOD_RED, COLD_STEEL, DropBlackoutHit
    rig = load_rig()
    par = rig.fixture("par_l")
    even = DropBlackoutHit().render(rig, _snap(drop_cycle=2), 1.0)
    odd = DropBlackoutHit().render(rig, _snap(drop_cycle=3), 1.0)
    r, g, b = (round(v * 255) for v in BLOOD_RED)
    assert (even.values[par.offset + 1], even.values[par.offset + 2], even.values[par.offset + 3]) == (r, g, b)
    r, g, b = (round(v * 255) for v in COLD_STEEL)
    assert (odd.values[par.offset + 1], odd.values[par.offset + 2], odd.values[par.offset + 3]) == (r, g, b)


def test_drop_blackout_hit_color_is_static_not_audio_reactive():
    """"reactivity: static all, kick_follow:false -- the cut IS the effect."""
    from livereact.scenes.library import DropBlackoutHit
    rig = load_rig()
    quiet = DropBlackoutHit().render(rig, _snap(energy=0.1, drop_cycle=0, eq_low_a=0.9, eq_mid_a=0.05, eq_high_a=0.05), 1.0)
    loud = DropBlackoutHit().render(rig, _snap(energy=1.0, drop_cycle=0, eq_low_a=0.05, eq_mid_a=0.05, eq_high_a=0.9), 1.0)
    par = rig.fixture("par_l")
    assert quiet.values[par.offset + 1:par.offset + 4] == loud.values[par.offset + 1:par.offset + 4]


def test_drop_blackout_hit_conflicts_with_every_build_scene():
    from livereact.scenes.library import ALL_SCENES, DropBlackoutHit
    build_scene_names = {cls().name for cls in ALL_SCENES if cls().bucket == EnergyBucket.BUILD}
    assert build_scene_names <= DropBlackoutHit.conflicts


def test_drop_strobe_stab_does_not_touch_the_strobe_channel_itself():
    """The strobe stab itself belongs to ReactiveLayerEngine's own strobe
    channel (a separate DMX byte) -- this scene only provides color context."""
    from livereact.scenes.library import DropStrobeStab
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = DropStrobeStab().render(rig, _snap(), 1.0)
    assert not c.touched[bar.offset + 1]  # ch2 = strobe, untouched by this scene


def test_build_riser_wash_does_not_set_wash_intensity():
    """paint_wash deliberately never sets intensity -- that's owned
    exclusively by ReactiveLayerEngine's own bar-position ramp."""
    from livereact.scenes.library import BuildRiserWash
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = BuildRiserWash().render(rig, _snap(), 1.0)
    assert not c.touched[bar.offset]  # ch1 = dimmer, untouched


# --- Bible-integration Phase 5, second wave: PeakFullRig, PeakColorPulseClear, PeakAltHitsClear

def test_peak_full_rig_spots_at_full_intensity():
    from livereact.scenes.library import PeakFullRig
    rig = load_rig()
    mh = rig.fixture("mh_l")
    c = PeakFullRig().render(rig, _snap(), 1.0)
    assert c.values[mh.offset + 5] == 255  # ch6 dimmer, "spot 100%"


def test_peak_full_rig_fan_completes_one_cycle_every_four_bars():
    from livereact.scenes.library import PeakFullRig
    rig = load_rig()
    mh = rig.fixture("mh_l")
    scene = PeakFullRig()
    at_0 = scene.render(rig, _snap(bar=0, beat_in_bar=1, beat_phase=0.0), 1.0).values[mh.offset]
    at_4 = scene.render(rig, _snap(bar=4, beat_in_bar=1, beat_phase=0.0), 1.0).values[mh.offset]
    assert abs(at_0 - at_4) <= 1  # a full period_bars=4 cycle returns to the same pan (+-1 byte fp rounding)


def test_peak_color_pulse_clear_only_pulses_on_the_downbeat():
    from livereact.scenes.library import PeakColorPulseClear
    rig = load_rig()
    bar = rig.fixture("bar_l")
    scene = PeakColorPulseClear()
    on_beat = scene.render(rig, _snap(beat_in_bar=1, beat_phase=0.0), 1.0)
    off_beat = scene.render(rig, _snap(beat_in_bar=2, beat_phase=0.0), 1.0)
    assert on_beat.values[bar.offset] > 0
    assert off_beat.values[bar.offset] == 0


def test_peak_color_pulse_clear_decays_across_the_downbeat():
    from livereact.scenes.library import PeakColorPulseClear
    rig = load_rig()
    bar = rig.fixture("bar_l")
    scene = PeakColorPulseClear()
    early = scene.render(rig, _snap(beat_in_bar=1, beat_phase=0.0), 1.0).values[bar.offset]
    late = scene.render(rig, _snap(beat_in_bar=1, beat_phase=0.9), 1.0).values[bar.offset]
    assert early > late


def test_peak_color_pulse_clear_is_actually_visible_through_reactives_htp_hold():
    """Verified, not just theorized: DIMMER merges by HTP (max), so this
    scene's pulse could in principle be masked by ReactiveLayerEngine's own
    PEAK hold -- confirmed it genuinely shows through for realistic
    drop_hit_intensity values, only disappearing once the hit itself is
    already near-maximum (both sides already at the ceiling)."""
    from livereact.clock import FakeClock
    from livereact.core.show_state import ShowSnapshot
    from livereact.engines.reactive import ReactiveLayerEngine
    from livereact.merge.merge import merge_contributions
    from livereact.scenes.library import PeakColorPulseClear
    rig = load_rig()
    bar = rig.fixture("bar_l")
    scene = PeakColorPulseClear()

    def _merged_at(hit: float, beat_in_bar: int) -> int:
        reactive = ReactiveLayerEngine(FakeClock())
        snap = ShowSnapshot(energy=1.0, energy_norm=1.0, bucket="peak", drop_hit_intensity=hit,
                             beat_in_bar=beat_in_bar, beat_phase=0.0, fader_a=1.0, eq_low_a=0.5)
        c_r = reactive.tick(snap, rig)
        c_r.priority = reactive.priority
        c_p = scene.render(rig, snap, 1.0)
        c_p.priority = 50
        return merge_contributions([c_r, c_p], rig)[bar.offset]

    assert _merged_at(0.85, beat_in_bar=1) > _merged_at(0.85, beat_in_bar=3)  # a real, typical hit: pulse visible


def test_peak_alt_hits_clear_alternates_by_bar_on_a_single_fixture():
    from livereact.scenes.library import PeakAltHitsClear
    rig = load_rig()
    mh = rig.fixture("mh_l")
    scene = PeakAltHitsClear()
    bar0 = scene.render(rig, _snap(bar=0), 1.0).values[mh.offset]
    bar1 = scene.render(rig, _snap(bar=1), 1.0).values[mh.offset]
    bar2 = scene.render(rig, _snap(bar=2), 1.0).values[mh.offset]
    assert bar0 != bar1  # snaps between the two positions each bar
    assert bar0 == bar2  # and returns on the next even bar -- true alternation, not drift


# --- Bible-integration Phase 5, third wave: BreakLowWash, BreakSingleSpotSpecial, BreakCeilingDrift, BreakColorHoldClear

def test_break_low_wash_does_not_set_wash_intensity():
    """paint_wash deliberately never sets intensity -- the 15-35% breathing
    envelope is owned exclusively by ReactiveLayerEngine's own BREAK branch,
    same pattern as BuildRiserWash."""
    from livereact.scenes.library import BreakLowWash
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = BreakLowWash().render(rig, _snap(), 1.0)
    assert not c.touched[bar.offset]  # ch1 = dimmer, untouched


def test_break_low_wash_uses_the_snapshots_palette_entry():
    """Was a fixed cold_steel until 2026-09-15; now the phrase-frozen BREAK
    palette entry on the snapshot (engines/palette.py) -- same hue for the
    whole phrase, different across songs."""
    from livereact.engines.palette import rgb as palette_rgb
    from livereact.scenes.library import BreakLowWash
    rig = load_rig()
    par = rig.fixture("par_l")
    for name in ("cold_steel", "uv_violet"):
        c = BreakLowWash().render(rig, _snap(palette=name), 1.0)
        r, g, b = (round(v * 255) for v in palette_rgb(name))
        assert (c.values[par.offset + 1], c.values[par.offset + 2], c.values[par.offset + 3]) == (r, g, b)


def test_default_catalog_is_the_core_one_look_per_bucket_set():
    """2026-09-15 (Josh, third live test): "a lot of different scenes, even
    when the buildups were pretty similar" -> one look per bucket by
    default; the full library stays reachable via full=True."""
    from livereact.scenes.library import ALL_SCENES, CORE_SCENES
    core = SceneCatalog()
    assert [type(s) for b in EnergyBucket for s in core.bucket(b)] == \
        sorted(CORE_SCENES, key=lambda cls: list(EnergyBucket).index(cls.bucket))
    assert len(core.bucket(EnergyBucket.BUILD)) == 1
    assert len(core.bucket(EnergyBucket.DROP)) == 1
    assert len(core.bucket(EnergyBucket.PEAK)) == 1
    assert len(core.bucket(EnergyBucket.BREAK)) == 2
    assert sum(len(SceneCatalog(full=True).bucket(b)) for b in EnergyBucket) == len(ALL_SCENES)


def test_scene_hue_does_not_move_with_the_audio_bands():
    """The measured bug: every BUILD/PEAK look re-picked hue from the
    instantaneous dominant band each tick (6-10 flips/s live). Now the bands
    can flip freely and the rendered wash color is identical."""
    from livereact.scenes.library import PeakFullRig, RisingTide
    rig = load_rig()
    par = rig.fixture("par_l")
    for scene in (RisingTide(), PeakFullRig()):
        colors = set()
        for bands in ((1.0, 0.2, 0.2), (0.2, 1.0, 0.2), (0.2, 0.2, 1.0)):
            c = scene.render(rig, _snap(palette="hot_magenta", eq_low_a=bands[0], eq_mid_a=bands[1],
                                        eq_high_a=bands[2]), 1.0)
            colors.add((c.values[par.offset + 1], c.values[par.offset + 2], c.values[par.offset + 3]))
        assert len(colors) == 1, scene.name


def test_break_low_wash_conflicts_with_peak_strobe_blackout():
    """peak_strobe_blackout isn't built yet -- this just confirms the
    Bible's conflicts[] pairing is declared, ready for when it is."""
    from livereact.scenes.library import BreakLowWash
    assert "Peak Strobe Blackout" in BreakLowWash.conflicts


def test_break_single_spot_special_degrades_to_break_low_wash():
    from livereact.scenes.library import BreakLowWash, BreakSingleSpotSpecial
    assert BreakSingleSpotSpecial.degrade_to is BreakLowWash


def test_break_single_spot_special_spot_level_is_static_not_audio_reactive():
    from livereact.scenes.library import BreakSingleSpotSpecial
    rig = load_rig()
    mh = rig.fixture("mh_l")
    quiet = BreakSingleSpotSpecial().render(rig, _snap(energy=0.0), 1.0)
    loud = BreakSingleSpotSpecial().render(rig, _snap(energy=1.0), 1.0)
    assert quiet.values[mh.offset + 5] == loud.values[mh.offset + 5]  # ch6 dimmer -- "reactivity: static all"


def test_break_ceiling_drift_tilt_completes_one_cycle_every_eight_bars():
    from livereact.scenes.library import BreakCeilingDrift
    rig = load_rig()
    mh = rig.fixture("mh_l")
    scene = BreakCeilingDrift()
    at_0 = scene.render(rig, _snap(bar=0, beat_in_bar=1, beat_phase=0.0), 1.0).values[mh.offset + 2]
    at_8 = scene.render(rig, _snap(bar=8, beat_in_bar=1, beat_phase=0.0), 1.0).values[mh.offset + 2]
    assert abs(at_0 - at_8) <= 1  # a full period_bars=8 cycle returns to the same tilt (+-1 byte fp rounding)


def test_break_ceiling_drift_stays_within_the_declared_tilt_amplitude():
    from livereact.scenes.library import BreakCeilingDrift
    rig = load_rig()
    mh = rig.fixture("mh_l")
    scene = BreakCeilingDrift()
    tilts = [
        scene.render(rig, _snap(bar=b, beat_in_bar=1, beat_phase=0.0), 1.0).values[mh.offset + 2]
        for b in range(9)
    ]
    lo, hi = (0.5 - BreakCeilingDrift.TILT_AMP) * 255, (0.5 + BreakCeilingDrift.TILT_AMP) * 255
    assert all(lo - 1 <= t <= hi + 1 for t in tilts)


def test_break_color_hold_clear_is_static_color_and_position():
    """"Fully static" -- neither color nor position should move with energy/bar."""
    from livereact.scenes.library import DEEP_BLUE, BreakColorHoldClear
    rig = load_rig()
    par = rig.fixture("par_l")
    mh = rig.fixture("mh_l")
    a = BreakColorHoldClear().render(rig, _snap(bar=0, energy=0.1), 1.0)
    b = BreakColorHoldClear().render(rig, _snap(bar=5, energy=1.0), 1.0)
    r, g, bl = (round(v * 255) for v in DEEP_BLUE)
    assert (a.values[par.offset + 1], a.values[par.offset + 2], a.values[par.offset + 3]) == (r, g, bl)
    assert a.values[par.offset + 1:par.offset + 4] == b.values[par.offset + 1:par.offset + 4]
    assert a.values[mh.offset] == b.values[mh.offset]  # pan static too


# --- Bible-integration Phase 5, fourth wave: OutroFadeWash, OutroSingleBeam

def test_outro_fade_wash_does_not_set_wash_intensity():
    """paint_wash deliberately never sets intensity -- the 40%->15%
    descending fade is owned exclusively by ReactiveLayerEngine's own OUTRO
    branch, same pattern as BreakLowWash/BuildRiserWash."""
    from livereact.scenes.library import OutroFadeWash
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = OutroFadeWash().render(rig, _snap(), 1.0)
    assert not c.touched[bar.offset]  # ch1 = dimmer, untouched


def test_outro_fade_wash_uses_warm_white():
    from livereact.scenes.library import WARM_WHITE, OutroFadeWash
    rig = load_rig()
    par = rig.fixture("par_l")
    c = OutroFadeWash().render(rig, _snap(), 1.0)
    r, g, b = (round(v * 255) for v in WARM_WHITE)
    assert (c.values[par.offset + 1], c.values[par.offset + 2], c.values[par.offset + 3]) == (r, g, b)


def test_outro_single_beam_does_not_touch_wash_at_all():
    """Unlike every other scene in this file, OutroSingleBeam deliberately
    skips paint_wash entirely -- a single last beam should not fight its own
    "dark 80%" intent by lighting the wash. See the class docstring."""
    from livereact.scenes.library import OutroSingleBeam
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = OutroSingleBeam().render(rig, _snap(), 1.0)
    assert not any(c.touched[bar.offset:bar.offset + bar.channel_count])


def test_outro_single_beam_lights_only_the_first_spot():
    """"One spot," literally -- this rig only has one, so this mostly
    documents intent, but the slicing itself (`rig.by_role("spot")[:1]`) is
    the behavior under test, not just its effect on this specific rig."""
    from livereact.scenes.library import OutroSingleBeam
    rig = load_rig()
    mh = rig.fixture("mh_l")
    c = OutroSingleBeam().render(rig, _snap(), 1.0)
    assert c.touched[mh.offset + 5]  # ch6 dimmer -- the one spot got lit


def test_outro_single_beam_is_static_not_audio_reactive():
    from livereact.scenes.library import OutroSingleBeam
    rig = load_rig()
    mh = rig.fixture("mh_l")
    quiet = OutroSingleBeam().render(rig, _snap(energy=0.0), 1.0)
    loud = OutroSingleBeam().render(rig, _snap(energy=1.0), 1.0)
    assert quiet.values[mh.offset + 5] == loud.values[mh.offset + 5]  # ch6 dimmer -- "reactivity: static all"


def test_safe_default_renders_static_neutral_wash_no_motion_no_laser():
    """LOOK-08: safe_default is static 40% neutral_wash, no mover motion,
    no laser, no fog."""
    from livereact.fixtures.profiles import _intensity_to8
    rig = load_rig()
    snap = _snap()
    c = SAFE_DEFAULT.render(rig, snap, 1.0)
    par = rig.fixture("par_l")
    assert c.values[par.offset] == _intensity_to8(0.40)  # neutral 40% intensity, gamma+floor curve
    laser = rig.fixture("laser_l")
    assert not any(c.touched[laser.offset + i] for i in range(12))  # laser untouched
    mh = rig.fixture("mh_l")
    assert not c.touched[mh.offset]  # pan untouched -- no motion commanded


# --- Bible-integration Phase 5, fifth wave: AllDark, ChaseWashLr

def test_all_dark_zeroes_every_intensity_channel_no_motion_no_laser():
    """all_dark: "roles all 0" -- every wash/spot dimmer to zero, nothing
    else touched (same "leave position/laser alone" restraint as
    SafeDefault, not part of ALL_SCENES/the rotation pool)."""
    from livereact.scenes.base import ALL_DARK
    rig = load_rig()
    snap = _snap()
    c = ALL_DARK.render(rig, snap, 1.0)
    par = rig.fixture("par_l")
    bar = rig.fixture("bar_l")
    mh = rig.fixture("mh_l")
    assert c.values[par.offset] == 0
    assert c.values[bar.offset] == 0
    assert c.values[mh.offset + 5] == 0  # ch6 dimmer
    laser = rig.fixture("laser_l")
    assert not any(c.touched[laser.offset + i] for i in range(12))  # laser untouched
    assert not c.touched[mh.offset]  # pan untouched -- no motion commanded


def test_all_dark_not_in_rotation_pool():
    from livereact.scenes.library import ALL_SCENES
    assert "All Dark" not in {cls().name for cls in ALL_SCENES}


def test_chase_wash_lr_alternates_left_and_right_out_of_phase():
    """"scaling{2:L then R}" -- with the period_bars=2 chase, L and R should
    be at opposite points in their cycle at any given moment, not moving
    together."""
    from livereact.scenes.library import ChaseWashLr
    rig = load_rig()
    par_l = rig.fixture("par_l")  # side="left"
    par_r = rig.fixture("par_r")  # side="right"
    scene = ChaseWashLr()
    # bar=0, beat_in_bar=3 -> musical_bar_position=0.5, a quarter into the
    # period_bars=2 cycle -- NOT a zero-crossing (unlike bar=0/beat=1, where
    # L and R's opposite-phase sine values both happen to cross zero at the
    # same instant and misleadingly look equal despite being out of phase).
    c = scene.render(rig, _snap(bar=0, beat_in_bar=3, beat_phase=0.0), 1.0)
    assert c.values[par_l.offset] != c.values[par_r.offset]


def test_chase_wash_lr_completes_one_cycle_every_two_bars():
    from livereact.scenes.library import ChaseWashLr
    rig = load_rig()
    par = rig.fixture("par_l")
    scene = ChaseWashLr()
    at_0 = scene.render(rig, _snap(bar=0, beat_in_bar=1, beat_phase=0.0), 1.0).values[par.offset]
    at_2 = scene.render(rig, _snap(bar=2, beat_in_bar=1, beat_phase=0.0), 1.0).values[par.offset]
    assert abs(at_0 - at_2) <= 1  # a full period_bars=2 cycle returns to the same level (+-1 byte fp rounding)


def test_chase_wash_lr_does_not_touch_spots():
    from livereact.scenes.library import ChaseWashLr
    rig = load_rig()
    mh = rig.fixture("mh_l")
    c = ChaseWashLr().render(rig, _snap(), 1.0)
    assert not any(c.touched[mh.offset:mh.offset + mh.channel_count])


# --- Bible-integration Phase 5, sixth wave: BuildBeamRaise, DropBeamFan,
# DropLaserPunctuation, PeakBeamSweep, PeakLaserTunnel, MirrorCrossBeam --
# all 6 always degrade on this rig (rig.atm is permanently 0.0, no haze
# hardware exists), so the tests here are about the DEGRADE behavior, not
# about these looks ever actually winning a live pick().

def test_all_six_atmosphere_gated_scenes_are_always_ineligible_on_this_rig():
    from livereact.scenes.catalog import SceneCatalog
    from livereact.scenes.library import (
        BuildBeamRaise, DropBeamFan, DropLaserPunctuation,
        MirrorCrossBeam, PeakBeamSweep, PeakLaserTunnel,
    )
    rig = load_rig()
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    for cls in (BuildBeamRaise, DropBeamFan, DropLaserPunctuation, PeakBeamSweep, PeakLaserTunnel, MirrorCrossBeam):
        scene = next(s for s in cat.bucket(cls.bucket) if type(s) is cls)
        assert not cat._eligible(scene, rig), f"{cls.__name__} should never be eligible on this rig"


def test_build_bucket_pick_never_surfaces_build_beam_raise_directly():
    """BuildBeamRaise always degrades to BuildRiserWash -- round-robining
    through BUILD with a rig should never actually return it by name."""
    from livereact.scenes.catalog import SceneCatalog
    rig = load_rig()
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    seen = {cat.pick(EnergyBucket.BUILD, rig=rig).name for _ in range(20)}
    assert "Build Beam Raise" not in seen


def test_drop_bucket_pick_never_surfaces_beam_fan_or_laser_punctuation_directly():
    """Both always degrade (DropBeamFan -> DropBlackoutHit; DropLaser
    Punctuation -> DropBeamFan, which is ALSO ineligible -- so it gets
    dropped from the eligible set entirely, a genuine recursive-degrade,
    one-hop-only case, not just a single successful hop like the others)."""
    from livereact.scenes.catalog import SceneCatalog
    rig = load_rig()
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    seen = {cat.pick(EnergyBucket.DROP, rig=rig).name for _ in range(30)}
    assert "Drop Beam Fan" not in seen
    assert "Drop Laser Punctuation" not in seen


def test_peak_bucket_pick_never_surfaces_the_three_atmosphere_gated_looks_directly():
    """PeakBeamSweep -> PeakFullRig (single successful hop). PeakLaserTunnel
    and MirrorCrossBeam both degrade to PeakBeamSweep, which is ALSO
    ineligible -- both get dropped from the eligible set entirely (the same
    recursive-degrade, one-hop-only case as DropLaserPunctuation's)."""
    from livereact.scenes.catalog import SceneCatalog
    rig = load_rig()
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    seen = {cat.pick(EnergyBucket.PEAK, rig=rig).name for _ in range(30)}
    assert "Peak Beam Sweep" not in seen
    assert "Peak Laser Tunnel" not in seen
    assert "Mirror Cross Beam" not in seen


def test_drop_laser_punctuation_conflicts_with_every_break_scene():
    from livereact.scenes.library import ALL_SCENES, DropLaserPunctuation
    break_scene_names = {cls().name for cls in ALL_SCENES if cls().bucket == EnergyBucket.BREAK}
    assert break_scene_names <= DropLaserPunctuation.conflicts


def test_drop_laser_punctuation_touches_only_laser_channels():
    from livereact.scenes.library import DropLaserPunctuation
    rig = load_rig()
    bar = rig.fixture("bar_l")
    mh = rig.fixture("mh_l")
    c = DropLaserPunctuation().render(rig, _snap(energy=1.0), 1.0)
    assert not any(c.touched[bar.offset:bar.offset + bar.channel_count])
    assert not any(c.touched[mh.offset:mh.offset + mh.channel_count])


def test_peak_beam_sweep_uses_a_real_triangle_wave_not_a_sine():
    """"movement{sweep, triangle...}" -- a triangle wave is piecewise-linear,
    so equally-spaced bar samples across a quarter-period should show a
    constant slope; a sine would not."""
    from livereact.scenes.library import PeakBeamSweep
    rig = load_rig()
    mh = rig.fixture("mh_l")
    scene = PeakBeamSweep()

    def pan_at(bar: float) -> float:
        return scene.render(rig, _snap(bar=bar, beat_in_bar=1, beat_phase=0.0), 1.0).values[mh.offset]

    # 0.0/0.2/0.4 (not 0.0/0.25/0.5) -- deliberately avoids landing exactly on
    # a *.5 DMX-byte boundary, where round-half-to-even would add up to 1
    # byte of noise to each step and mask the real per-step equality.
    p0, p1, p2 = pan_at(0.0), pan_at(0.2), pan_at(0.4)
    assert abs((p1 - p0) - (p2 - p1)) <= 1  # constant slope across the rising quarter-period (+-1 byte rounding)


# --- Bible-integration Phase 5, seventh (final) wave: DropColorSlamBigroom,
# DropWashSlamClear, DropGoboProjectionClear, DropBlinderClear

def test_drop_color_slam_bigroom_splits_wash_and_spot_hues():
    """Wash (Par/WashBar) has real RGB channels; the moving head only has a
    single discrete color-wheel byte (ch8) -- `rgb_to_color_wheel` picks the
    nearest slot to SUNSET_SPOT, so the two fixtures are checked differently,
    not by decomposing the MH into 3 nonexistent RGB channels."""
    from livereact.fixtures.profiles import _to8, rgb_to_color_wheel
    from livereact.scenes.library import SUNSET_SPOT, SUNSET_WASH, DropColorSlamBigroom
    rig = load_rig()
    par = rig.fixture("par_l")
    mh = rig.fixture("mh_l")
    c = DropColorSlamBigroom().render(rig, _snap(beat_in_bar=1, beat_phase=0.0), 1.0)
    wr, wg, wb = (round(v * 255) for v in SUNSET_WASH)
    assert (c.values[par.offset + 1], c.values[par.offset + 2], c.values[par.offset + 3]) == (wr, wg, wb)
    sr, sg, sb = SUNSET_SPOT
    expected_wheel_slot = rgb_to_color_wheel(_to8(sr), _to8(sg), _to8(sb))
    assert c.values[mh.offset + 7] == expected_wheel_slot  # ch8 = color wheel


def test_drop_color_slam_bigroom_pulses_wash_dimmer_only_on_the_downbeat():
    """"audio:[wash.dimmer emphasis on first beat of each bar only]" --
    same mechanic as PeakColorPulseClear's own bar-downbeat pulse."""
    from livereact.scenes.library import DropColorSlamBigroom
    rig = load_rig()
    par = rig.fixture("par_l")
    scene = DropColorSlamBigroom()
    on_beat = scene.render(rig, _snap(beat_in_bar=1, beat_phase=0.0), 1.0)
    off_beat = scene.render(rig, _snap(beat_in_bar=2, beat_phase=0.0), 1.0)
    assert on_beat.values[par.offset] > 0
    assert off_beat.values[par.offset] == 0


def test_drop_wash_slam_clear_is_static_blood_red():
    from livereact.scenes.library import BLOOD_RED, DropWashSlamClear
    rig = load_rig()
    par = rig.fixture("par_l")
    quiet = DropWashSlamClear().render(rig, _snap(energy=0.1), 1.0)
    loud = DropWashSlamClear().render(rig, _snap(energy=1.0), 1.0)
    r, g, b = (round(v * 255) for v in BLOOD_RED)
    assert (quiet.values[par.offset + 1], quiet.values[par.offset + 2], quiet.values[par.offset + 3]) == (r, g, b)
    assert quiet.values[par.offset + 1:par.offset + 4] == loud.values[par.offset + 1:par.offset + 4]


def test_drop_gobo_projection_clear_is_always_ineligible_and_has_no_degrade_target():
    """"gobo" is a capability token that exists nowhere in this codebase --
    always ineligible on any rig it can currently describe, and unlike the
    atm-gated stubs, no degrade_to means it's just dropped, not substituted."""
    from livereact.scenes.catalog import SceneCatalog
    from livereact.scenes.library import DropGoboProjectionClear
    rig = load_rig()
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    scene = next(s for s in cat.bucket(EnergyBucket.DROP) if type(s) is DropGoboProjectionClear)
    assert not cat._eligible(scene, rig)
    assert DropGoboProjectionClear.degrade_to is None


def test_drop_bucket_pick_never_surfaces_gobo_projection_directly():
    from livereact.scenes.catalog import SceneCatalog
    rig = load_rig()
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    seen = {cat.pick(EnergyBucket.DROP, rig=rig).name for _ in range(30)}
    assert "Drop Gobo Projection Clear" not in seen


def test_drop_blinder_clear_is_eligible_and_reachable_via_round_robin():
    """Unlike DropGoboProjectionClear, this one genuinely satisfies its
    (rig-specific) requires -- "wash or blinder" resolves to "wash" alone
    on a rig with no blinder fixture, so it should actually surface."""
    from livereact.scenes.catalog import SceneCatalog
    from livereact.scenes.library import DropBlinderClear
    rig = load_rig()
    cat = SceneCatalog(full=True)  # the whole library, not the core default
    scene = next(s for s in cat.bucket(EnergyBucket.DROP) if type(s) is DropBlinderClear)
    assert cat._eligible(scene, rig)
    seen = {cat.pick(EnergyBucket.DROP, rig=rig).name for _ in range(30)}
    assert "Drop Blinder Clear" in seen


def test_drop_blinder_clear_does_not_set_wash_intensity():
    """No reactivity{} override given by the Bible for this look -- wash
    intensity stays owned by ReactiveLayerEngine, same default as most
    scenes in this file."""
    from livereact.scenes.library import DropBlinderClear
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = DropBlinderClear().render(rig, _snap(), 1.0)
    assert not c.touched[bar.offset]


# --- gap fix, 2026-09-13: PeakStrobeBlackout (missed by all 7 Phase 5 waves)

def test_peak_strobe_blackout_is_wash_only_no_spot():
    """requires{blinder or wash, native strobe} -- no spot at all, unlike
    every other PEAK scene in this file."""
    from livereact.scenes.library import PeakStrobeBlackout
    rig = load_rig()
    mh = rig.fixture("mh_l")
    c = PeakStrobeBlackout().render(rig, _snap(), 1.0)
    assert not any(c.touched[mh.offset:mh.offset + mh.channel_count])


def test_peak_strobe_blackout_does_not_touch_the_strobe_channel_itself():
    """Same restraint as DropStrobeStab -- the actual strobe stays owned by
    ReactiveLayerEngine's own channel, this scene supplies color only."""
    from livereact.scenes.library import PeakStrobeBlackout
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = PeakStrobeBlackout().render(rig, _snap(), 1.0)
    assert not c.touched[bar.offset + 1]  # ch2 = strobe, untouched


def test_peak_strobe_blackout_conflicts_with_break_low_wash_bidirectionally():
    """peak_strobe_blackout doesn't declare its own conflicts[] -- BreakLowWash
    already names it, and SceneCatalog._conflicts checks both directions."""
    from livereact.scenes.catalog import SceneCatalog
    from livereact.scenes.library import BreakLowWash, PeakStrobeBlackout
    assert SceneCatalog._conflicts(PeakStrobeBlackout(), BreakLowWash())


# --- build_snare_accent, 2026-09-14 (depended on new snare-roll subdivision detection)

def test_build_snare_accent_renders_wash_and_spot():
    """Same shape as BuildRiserWash's own color/context-only role -- this
    look paints wash + spot color, the actual accent flashes are owned by
    ReactiveLayerEngine's own strobe channel (see class docstring)."""
    from livereact.scenes.library import BuildSnareAccent
    rig = load_rig()
    bar = rig.fixture("bar_l")
    mh = rig.fixture("mh_l")
    c = BuildSnareAccent().render(rig, _snap(), 1.0)
    assert any(c.touched[bar.offset:bar.offset + bar.channel_count])
    assert any(c.touched[mh.offset:mh.offset + mh.channel_count])


def test_build_snare_accent_does_not_touch_the_strobe_channel_itself():
    """Same restraint as DropStrobeStab/PeakStrobeBlackout -- the actual
    strobe stays owned by ReactiveLayerEngine's own channel, this scene
    supplies color only."""
    from livereact.scenes.library import BuildSnareAccent
    rig = load_rig()
    bar = rig.fixture("bar_l")
    c = BuildSnareAccent().render(rig, _snap(), 1.0)
    assert not c.touched[bar.offset + 1]  # ch2 = strobe, untouched


def test_build_snare_accent_conflicts_with_drop_blackout_hit():
    """DropBlackoutHit.conflicts names every BUILD-bucket look by hand
    ("conflicts[all build looks]") -- BuildSnareAccent must be in that set,
    not just added to ALL_SCENES (see test_drop_blackout_hit_conflicts_
    with_every_build_scene for the generic version of this check)."""
    from livereact.scenes.catalog import SceneCatalog
    from livereact.scenes.library import BuildSnareAccent
    assert SceneCatalog._conflicts(DropBlackoutHit(), BuildSnareAccent())


# --- named targets via real geometry (2026-09-18) ------------------------------

def _rig_with_calibrated_head():
    from livereact.fixtures.geometry import HeadCalibration, Point3, Venue
    from livereact.fixtures.rig import load_rig
    rig = load_rig()
    rig.venue = Venue(9.0, 12.0, 3.6, Venue.default_targets(9.0, 12.0, 3.6))
    for mh in rig.by_role("spot"):
        mh.position = Point3(1.5 if mh.side == "left" else 7.5, 0.6, 3.0)
        mh.calibration = HeadCalibration(target="dj_booth", pan01=0.5, tilt01=0.5)
    rig.refresh_geometry()
    return rig


def test_break_single_spot_special_without_a_venue_keeps_the_old_guess():
    from livereact.fixtures.rig import load_rig
    from livereact.scenes.library import BreakSingleSpotSpecial

    rig = load_rig()
    c = BreakSingleSpotSpecial().render(rig, ShowSnapshot(energy=0.3))
    mh = rig.fixture("mh_l")
    assert c.values[mh.offset + 0] == 128            # pan 0.5
    assert c.values[mh.offset + 2] == int(0.70 * 65535) >> 8
    assert not c.absolute_movement


def test_break_single_spot_special_aims_at_the_real_dj_booth_when_calibrated():
    from livereact.fixtures.geometry import aim
    from livereact.fixtures.profiles import _read16
    from livereact.scenes.library import BreakSingleSpotSpecial

    rig = _rig_with_calibrated_head()
    c = BreakSingleSpotSpecial().render(rig, ShowSnapshot(energy=0.3))
    mh = rig.fixture("mh_l")
    pan01, tilt01, ok = aim(mh.position, mh.facing_deg, rig.venue.targets["dj_booth"], mh.calibration,
                            mh.PAN_RANGE_DEG, mh.TILT_RANGE_DEG)
    assert ok
    assert _read16(c.values, mh.offset + 0) == pytest.approx(pan01, abs=1e-4)
    assert _read16(c.values, mh.offset + 2) == pytest.approx(tilt01, abs=1e-4)
    assert mh.offset in c.absolute_movement           # finalize will skip the home remap


def test_outro_single_beam_uses_the_ceiling_bounce_target_when_calibrated():
    from livereact.scenes.library import OutroSingleBeam

    rig = _rig_with_calibrated_head()
    c = OutroSingleBeam().render(rig, ShowSnapshot(energy=0.2))
    mh = rig.fixture("mh_l")
    assert mh.offset in c.absolute_movement
    assert c.values[mh.offset + 2] > 128              # the ceiling target is above a 3 m head: tilted up
