"""Engine knobs as data: params.json -> module constants, applied before engines are built.

The engine's tunables are module-level constants (metronome.py, reactive.py,
catalog.py, ...). The improvement loop needs to vary them per trial without
editing code, so this module loads ``params.json`` (name -> module, default,
range, tag) and ``apply()``s a parameter set by overwriting those constants
in place, then recomputing the handful of values derived from them.

Rules the loop relies on:
- Only ``tag == "tunable"`` knobs may be varied, and only within ``range``.
  ``ear-tuned`` (Josh set them on the real rig) and ``safety-frozen``
  (strobe, laser) are read-only; ``apply()`` refuses to change them.
- ``apply()`` must run BEFORE Metronome / engines are constructed, and
  ``restore()`` after a run, so trials don't leak into each other.
  ``run_sim(params=...)`` does both.
- Lighting/merge/DMX code is untouched: values change, code doesn't.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

PARAMS_PATH = Path(__file__).resolve().parents[2] / "params.json"
LOCKED_TAGS = ("ear-tuned", "safety-frozen")


def load_spec(path: str | Path | None = None) -> dict:
    p = Path(path) if path else PARAMS_PATH
    return json.loads(p.read_text(encoding="utf-8"))["knobs"]


def defaults(spec: dict | None = None) -> dict[str, Any]:
    spec = spec or load_spec()
    return {k: v["default"] for k, v in spec.items()}


def tunable(spec: dict | None = None) -> dict[str, dict]:
    spec = spec or load_spec()
    return {k: v for k, v in spec.items() if v["tag"] == "tunable"}


def validate(values: dict[str, Any], spec: dict | None = None) -> None:
    spec = spec or load_spec()
    for k, v in values.items():
        if k not in spec:
            raise KeyError(f"unknown knob {k}")
        meta = spec[k]
        if meta["tag"] in LOCKED_TAGS and v != meta["default"]:
            raise ValueError(f"{k} is {meta['tag']}; it cannot be changed by the loop")
        if meta["tag"] == "tunable":
            lo, hi = meta["range"]
            if not (lo <= v <= hi):
                raise ValueError(f"{k}={v} outside its range [{lo}, {hi}]")
        if meta["type"] == "int" and int(v) != v:
            raise ValueError(f"{k} must be an int")


def _recompute_derived() -> None:
    """Module-level values computed FROM knobs at import time, redone after an apply."""
    m = importlib.import_module("livereact.core.metronome")
    m.BUILD_CUE_HOLD_S = m.BUILD_CUE_HOLD_BARS * m.SECONDS_PER_BAR
    m.ROLL_ARM_HOLD_S = m.ROLL_ARM_HOLD_BARS * m.SECONDS_PER_BAR
    m.RISE_ARM_HOLD_S = m.RISE_ARM_HOLD_BARS * m.SECONDS_PER_BAR
    c = importlib.import_module("livereact.scenes.catalog")
    c._BUCKET_BOUNDARIES = (c.BREAK_MAX, c.BUILD_MAX)
    # hysteresis margins are default args captured at def time
    fn = c.bucket_for_energy_with_hysteresis
    d = list(fn.__defaults__ or ())
    if len(d) >= 2:
        d[-2], d[-1] = c.HYSTERESIS_MARGIN_UP, c.HYSTERESIS_MARGIN_DOWN
        fn.__defaults__ = tuple(d)
    r = importlib.import_module("livereact.engines.reactive")
    r.WASH_TRANSITION_WINDOW_S = 3 * r.WASH_CROSSFADE_TAU_S


def apply(values: dict[str, Any], spec: dict | None = None) -> dict[str, Any]:
    """Overwrite module constants with ``values``; returns the previous values for restore()."""
    spec = spec or load_spec()
    validate(values, spec)
    previous: dict[str, Any] = {}
    for k, v in values.items():
        mod = importlib.import_module(spec[k]["module"])
        previous[k] = getattr(mod, k)
        setattr(mod, k, int(v) if spec[k]["type"] == "int" else float(v))
    _recompute_derived()
    return previous


def restore(previous: dict[str, Any], spec: dict | None = None) -> None:
    spec = spec or load_spec()
    for k, v in previous.items():
        setattr(importlib.import_module(spec[k]["module"]), k, v)
    _recompute_derived()


def current(spec: dict | None = None) -> dict[str, Any]:
    spec = spec or load_spec()
    return {k: getattr(importlib.import_module(v["module"]), k) for k, v in spec.items()}
