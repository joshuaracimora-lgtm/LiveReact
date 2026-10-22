"""Tests for the rig loader: the packaged default rig, role/side lookups, the
channel-kind map, boot universe, and overlap/overflow validation."""

from __future__ import annotations

import pytest

from livereact.fixtures.profiles import ChannelKind
from livereact.fixtures.rig import RigError, _rig_from_dict, load_rig


def test_default_rig_loads_with_eight_fixtures_and_74_channels():
    rig = load_rig()
    assert len(rig.fixtures) == 8
    assert rig.channel_count() == 74


def test_rig_capabilities_are_the_union_of_every_fixture_plus_roles():
    """Bible-integration Phase 1: Rig.capabilities() backs LIGHTING_BIBLE.md's
    requires{} gating -- confirmed against the packaged 8-fixture rig (2
    spots, 2 lasers, 2 pars, 2 wash bars)."""
    rig = load_rig()
    caps = rig.capabilities()
    assert caps >= {"pan_tilt", "native_strobe_channel", "wheel_discrete",  # from the 2 MovingHeads
                     "laser", "rgb",                                        # from lasers/pars/bars
                     "wash", "spot"}                                        # role tokens


def test_rig_atm_is_always_zero_no_haze_hardware_exists():
    rig = load_rig()
    assert rig.atm == 0.0


def test_role_and_side_lookups():
    rig = load_rig()
    assert len(rig.by_role("spot")) == 2
    assert len(rig.by_role("laser")) == 2
    assert len(rig.by_role("wash")) == 4  # 2 pars + 2 bars
    assert len(rig.by_side("left")) == 4
    assert len(rig.by_role_side("wash", "right")) == 2
    assert rig.fixture("mh_l").label == "MH Left"


def test_rig_loads_optional_layout_and_home_position_fields():
    data = {
        "name": "test",
        "slots": [
            {"slot_id": "mh", "fixture_type": "shehds_180w_mh", "dmx_address": 1,
             "role": "spot", "side": "left", "x": 0.3, "y": 0.4,
             "home_pan": 0.65, "home_tilt": 0.4},
            {"slot_id": "mh2", "fixture_type": "shehds_180w_mh", "dmx_address": 20,
             "role": "spot", "side": "right"},  # no layout/home fields at all
        ],
    }
    rig = _rig_from_dict(data)
    calibrated = rig.fixture("mh")
    uncalibrated = rig.fixture("mh2")
    assert calibrated.layout_x == 0.3 and calibrated.layout_y == 0.4
    assert calibrated.home_pan == 0.65 and calibrated.home_tilt == 0.4
    assert uncalibrated.layout_x is None and uncalibrated.layout_y is None
    assert uncalibrated.home_pan is None and uncalibrated.home_tilt is None


def test_rig_to_dict_round_trips_every_loaded_field():
    """2026-09-18: the three UI save paths used to each rebuild slot dicts by
    hand and drop what they didn't know (a layout save deleted the laser's
    measured mount height). One serializer now; nothing is lost."""
    import os

    from livereact.fixtures.rig import rig_to_dict

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rig = load_rig(os.path.join(here, "rigs", "josh_rig.json"))
    laser = rig.fixture("laser")

    data = rig_to_dict(rig)
    again = _rig_from_dict(data)
    laser2 = again.fixture("laser")
    assert laser2.extra == laser.extra
    assert [f.address for f in again.fixtures] == [f.address for f in rig.fixtures]
    assert again.description == rig.description
    assert data.get("venue") is None  # josh_rig.json has no floor plan yet


def test_rig_round_trips_layout_home_and_venue_geometry():
    from livereact.fixtures.geometry import Point3
    from livereact.fixtures.rig import rig_to_dict

    data = {
        "name": "geo",
        "venue": {"width_m": 9.0, "length_m": 12.0, "height_m": 3.6,
                  "targets": {"dj_booth": {"x_m": 4.5, "y_m": 0.9, "z_m": 1.2}}},
        "slots": [
            {"slot_id": "mh", "fixture_type": "shehds_180w_mh", "dmx_address": 1,
             "role": "spot", "side": "left", "x": 0.3, "y": 0.4, "home_pan": 0.65, "home_tilt": 0.4,
             "position": {"x_m": 1.5, "y_m": 0.6, "z_m": 0.3}, "facing_deg": 15.0,
             "calibration": {"target": "dj_booth", "pan01": 0.41, "tilt01": 0.63,
                             "pan_offset_deg": -3.2, "tilt_offset_deg": 12.5,
                             "invert_pan": True, "invert_tilt": False}},
            {"slot_id": "laser", "fixture_type": "shehds_3w_laser", "dmx_address": 20,
             "role": "laser", "side": "right",
             "position": {"x_m": 7.6, "y_m": 0.6, "z_m": 0.9144},
             "beam": {"tilt_deg": 10.0, "fan_half_angle_deg": 25.0, "h_offset01": 0.4},
             "custom_note": "kept"},
        ],
    }
    rig = _rig_from_dict(data)
    assert rig.venue is not None and rig.venue.width_m == 9.0
    assert rig.venue.targets["dj_booth"] == Point3(4.5, 0.9, 1.2)
    mh = rig.fixture("mh")
    assert mh.position == Point3(1.5, 0.6, 0.3) and mh.facing_deg == 15.0
    assert mh.calibration.captured and mh.calibration.invert_pan is True
    assert mh.calibration.pan_offset_deg == pytest.approx(-3.2)
    laser = rig.fixture("laser")
    assert laser.fan_half_angle_deg == 25.0 and laser.h_offset01 == 0.4
    assert laser.extra == {"custom_note": "kept"}

    again = _rig_from_dict(rig_to_dict(rig))
    mh2, laser2 = again.fixture("mh"), again.fixture("laser")
    assert mh2.position == mh.position and mh2.facing_deg == 15.0
    assert mh2.home_pan == 0.65 and mh2.layout_x == 0.3
    assert mh2.calibration.pan01 == 0.41 and mh2.calibration.tilt_offset_deg == pytest.approx(12.5)
    assert laser2.beam_tilt_deg == 10.0 and laser2.extra == {"custom_note": "kept"}
    assert again.venue.targets == rig.venue.targets


def test_side_is_derived_from_real_x_when_a_venue_is_declared(caplog):
    """LIGHTING_BIBLE.md §1.2/§6.2 (MOVE-01/02/03, LOOK-02): mirrored effects
    key off real x, not a hand-typed label. The rig file's `side` is only
    the fallback for rigs with no floor plan."""
    import logging

    base = {
        "name": "sides",
        "venue": {"width_m": 9.0, "length_m": 12.0},
        "slots": [
            {"slot_id": "a", "fixture_type": "shehds_180w_mh", "dmx_address": 1, "role": "spot",
             "side": "right", "position": {"x_m": 1.0, "y_m": 0.5, "z_m": 0.3}},   # declared right, really left
            {"slot_id": "b", "fixture_type": "shehds_180w_mh", "dmx_address": 20, "role": "spot",
             "side": "left", "position": {"x_m": 8.0, "y_m": 0.5, "z_m": 0.3}},    # declared left, really right
        ],
    }
    rig = _rig_from_dict(base)
    assert rig.fixture("a").side == "left" and rig.fixture("a").x_norm == pytest.approx(-7 / 9)
    assert rig.fixture("b").side == "right" and rig.fixture("b").x_norm == pytest.approx(7 / 9)

    # both on the same side of centre -> can't mirror -> MOVE-03 warning
    base["slots"][1]["position"]["x_m"] = 2.0
    with caplog.at_level(logging.WARNING, logger="livereact.fixtures.rig"):
        _rig_from_dict(base)
    assert any("MOVE-03" in r.message for r in caplog.records)

    # no venue: side stays exactly as declared, x_norm undefined
    legacy = {"name": "legacy", "slots": [dict(s) for s in base["slots"]]}
    rig = _rig_from_dict(legacy)
    assert rig.fixture("a").side == "right" and rig.fixture("a").x_norm is None


def test_channel_kind_map_matches_addresses():
    rig = load_rig()
    # MH Left at address 1: ch6 (index 5) is the dimmer.
    assert rig.kind_at(5) == ChannelKind.DIMMER
    # Laser Left at address 31: all channels are LASER-kind.
    assert rig.kind_at(30) == ChannelKind.LASER
    # Par Left at address 55 (index 54): ch1 is the dimmer, ch2 is COLOR (R).
    assert rig.kind_at(54) == ChannelKind.DIMMER
    assert rig.kind_at(55) == ChannelKind.COLOR


def test_boot_universe_has_moving_heads_centered():
    rig = load_rig()
    boot = rig.boot_universe()
    assert boot[0] == 128   # MH Left pan center
    assert boot[2] == 80    # MH Left tilt down
    assert boot[5] == 0     # MH Left dimmer off
    assert boot[30] == 0    # Laser Left CH1 off


def test_overlapping_addresses_raise():
    bad = {
        "name": "bad",
        "slots": [
            {"slot_id": "a", "fixture_type": "rgb_par", "dmx_address": 1, "role": "wash", "side": "left"},
            {"slot_id": "b", "fixture_type": "rgb_par", "dmx_address": 3, "role": "wash", "side": "right"},
        ],
    }
    with pytest.raises(RigError):
        _rig_from_dict(bad)


def test_overflow_past_512_raises():
    bad = {
        "name": "bad",
        "slots": [
            {"slot_id": "a", "fixture_type": "shehds_180w_mh", "dmx_address": 500, "role": "spot", "side": "left"},
        ],
    }
    with pytest.raises(RigError):
        _rig_from_dict(bad)


def test_unknown_fixture_type_raises():
    bad = {"name": "bad", "slots": [{"slot_id": "a", "fixture_type": "nope", "dmx_address": 1}]}
    with pytest.raises(RigError):
        _rig_from_dict(bad)


# --- laser_profile (2026-10-01) ------------------------------------------------------

def test_laser_profile_defaults_to_accent_and_old_files_still_load():
    from livereact.fixtures.rig import DEFAULT_LASER_PROFILE

    assert DEFAULT_LASER_PROFILE == "accent"
    assert load_rig().laser_profile == "accent"
    rig = _rig_from_dict({"name": "old", "slots": []})
    assert rig.laser_profile == "accent"


def test_laser_profile_feature_loads_and_round_trips():
    from livereact.fixtures.rig import rig_to_dict

    data = {"name": "bar", "laser_profile": "feature", "slots": [
        {"slot_id": "laser_l", "fixture_type": "shehds_3w_laser", "dmx_address": 1,
         "role": "laser", "side": "left"},
    ]}
    rig = _rig_from_dict(data)
    assert rig.laser_profile == "feature"
    out = rig_to_dict(rig)
    assert out["laser_profile"] == "feature"
    assert _rig_from_dict(out).laser_profile == "feature"


def test_unknown_laser_profile_raises():
    with pytest.raises(RigError, match="laser_profile"):
        _rig_from_dict({"name": "x", "laser_profile": "disco", "slots": []})


def test_bar_rig_file_loads_as_the_feature_profile_with_two_lasers():
    """rigs/bar_rig.json (2026-10-01): two lasers, the wash bar, no venue yet."""
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rig = load_rig(os.path.join(here, "rigs", "bar_rig.json"))
    assert rig.laser_profile == "feature"
    lasers = rig.by_role("laser")
    assert [fx.side for fx in lasers] == ["left", "right"]
    for fx in lasers:
        assert fx.beam_tilt_deg == 0.0
    assert rig.venue is None
    # Josh, 2026-10-01: a fresh rig, channels laid out in order
    assert [fx.address for fx in rig.fixtures] == [1, 7, 19]
    assert [fx.offset + fx.channel_count for fx in rig.fixtures] == [6, 18, 30]  # 1-6, 7-18, 19-30


def test_dj_pan_tilt_is_optional_round_trips_and_is_never_guessed():
    """Scorer v2 (STRUCT-04 "heads to the DJ"): a slot may declare the raw
    pan/tilt that faces the DJ. Absent -> Rig.dj_pose() is None (the scorer
    marks that sub-check NOT_SCORED) unless a dj_booth target AND a captured
    aim calibration let geometry.aim() derive it. Never invented."""
    from livereact.fixtures.rig import rig_to_dict
    rig = load_rig("rigs/josh_rig.json")
    mh = rig.fixture("mh")
    assert rig.dj_pose(mh) is None                      # no field, no venue
    assert "dj_pan_tilt" not in rig_to_dict(rig)["slots"][2]
    data = rig_to_dict(rig)
    data["slots"][2]["dj_pan_tilt"] = {"pan": 0.3, "tilt": 0.7}
    rig2 = _rig_from_dict(data)
    assert rig2.dj_pose(rig2.fixture("mh")) == (0.3, 0.7)
    assert rig_to_dict(rig2)["slots"][2]["dj_pan_tilt"] == {"pan": 0.3, "tilt": 0.7}
    data["slots"][2]["dj_pan_tilt"] = [0.25, 0.5]        # list form
    assert _rig_from_dict(data).dj_pose(_rig_from_dict(data).fixture("mh")) == (0.25, 0.5)
    data["slots"][2]["dj_pan_tilt"] = {"pan": 1.5, "tilt": 0.5}
    with pytest.raises(RigError):
        _rig_from_dict(data)
    # a dj_booth target alone (uncalibrated head) is not enough to derive a pose
    data["slots"][2].pop("dj_pan_tilt")
    data["venue"] = {"width_m": 8.0, "length_m": 12.0, "height_m": 3.0,
                     "targets": {"dj_booth": {"x_m": 4.0, "y_m": 1.0, "z_m": 1.2}}}
    data["slots"][2]["position"] = {"x_m": 2.0, "y_m": 0.5, "z_m": 3.0}
    assert _rig_from_dict(data).dj_pose(_rig_from_dict(data).fixture("mh")) is None
    data["slots"][2]["calibration"] = {"target": "dj_booth", "pan01": 0.5, "tilt01": 0.5}
    pose = _rig_from_dict(data).dj_pose(_rig_from_dict(data).fixture("mh"))
    assert pose is not None and all(0.0 <= v <= 1.0 for v in pose)
