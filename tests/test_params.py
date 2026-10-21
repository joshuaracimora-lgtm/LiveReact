"""params.json knobs: locked tags stay locked, ranges hold, apply/restore round-trips."""
from __future__ import annotations

import pytest

from livereact import params as P


def test_spec_loads_and_defaults_match_modules():
    spec = P.load_spec()
    live = P.current(spec)
    for k, meta in spec.items():
        assert live[k] == meta["default"], f"{k}: params.json default {meta['default']} != module {live[k]}"


def test_locked_knobs_cannot_change():
    with pytest.raises(ValueError):
        P.apply({"STROBE_MAX": 0.9})
    with pytest.raises(ValueError):
        P.apply({"SYNTH_GAIN": 0.5})


def test_range_enforced():
    with pytest.raises(ValueError):
        P.apply({"BREAK_MAX": 0.99})


def test_apply_restore_roundtrip_including_derived():
    import livereact.core.metronome as m
    import livereact.scenes.catalog as c
    before = (m.ROLL_ARM_HOLD_S, c._BUCKET_BOUNDARIES, c.bucket_for_energy_with_hysteresis.__defaults__)
    prev = P.apply({"ROLL_ARM_HOLD_BARS": 6.0, "BREAK_MAX": 0.2, "HYSTERESIS_MARGIN_UP": 0.1})
    assert m.ROLL_ARM_HOLD_S == 6.0 * m.SECONDS_PER_BAR
    assert c._BUCKET_BOUNDARIES[0] == 0.2
    assert c.bucket_for_energy_with_hysteresis.__defaults__[-2] == 0.1
    P.restore(prev)
    assert (m.ROLL_ARM_HOLD_S, c._BUCKET_BOUNDARIES, c.bucket_for_energy_with_hysteresis.__defaults__) == before
