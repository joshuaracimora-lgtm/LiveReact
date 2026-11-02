"""Tiny optional persistence (docs/10 C1).

Saves a handful of operator settings between runs so scene/brightness tuning isn't
lost on every restart. Deliberately minimal — no cloud, no accounts, no presets,
just a local ``config.json``. Missing/corrupt files are ignored (fall back to
defaults), so this can never block startup.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

DEFAULT_PATH = "config.json"

# ManualParams fields that persist between runs (momentary states do not).
# `laser_enable` was persisted until 2026-09-16: a saved True armed the laser
# at every launch, so it fired on every track of the third live run. Josh
# wants it reserved for the tunes he picks, and the software cannot tell
# those apart (see engines/laser.py) -- so arming is a per-run, per-track
# operator act (console checkbox / L), never carried over. A stale key in
# an old config.json is simply ignored.
_PERSIST = ("master_brightness", "strobe_cap", "warm_cool_bias", "synth_threshold")


def load_into(manual, path: str = DEFAULT_PATH) -> bool:
    """Apply saved settings onto ``manual``. Returns True if a file was loaded.

    Warns loudly (2026-09-13, found via a real hardware session) about any
    loaded value that differs from ``ManualParams``' own dataclass default --
    a stale `warm_cool_bias` left over from a previous console session
    silently biased every fixture's color (R x0.5/B x1.5 at the value found,
    +1.0 "max cool") for a full ~7-minute real test with zero visibility
    into why, since the only prior log line was "Loaded settings from
    config.json" with no indication anything non-default had loaded. This
    doesn't refuse to load a non-default value (that's a legitimate, saved
    operator choice for master_brightness/strobe_cap) -- it just makes sure
    it's never silent again.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable config %s: %s", path, exc)
        return False
    defaults = {field: getattr(manual, field) for field in _PERSIST}
    non_default = []
    for field in _PERSIST:
        if field in data:
            setattr(manual, field, data[field])
            if data[field] != defaults[field]:
                non_default.append(f"{field}={data[field]!r} (default {defaults[field]!r})")
    log.info("Loaded settings from %s", path)
    if non_default:
        log.warning(
            "%s has NON-DEFAULT operator settings active: %s -- these affect every fixture "
            "every frame; confirm this is intentional before trusting tonight's colors/caps.",
            path, "; ".join(non_default),
        )
    return True


def save_from(manual, path: str = DEFAULT_PATH, energy_norm: dict | None = None) -> None:
    """Write the persistable settings from ``manual`` to ``path``.

    ``energy_norm`` (see `save_energy_norm_seed`) is written alongside when
    given; when it's None the file's existing seed is carried over untouched,
    so a demo run or a session that never finished warming up can't erase a
    good seed from the last real set.
    """
    data = {field: getattr(manual, field) for field in _PERSIST}
    if energy_norm is None:
        energy_norm = _read(path).get(ENERGY_NORM_KEY)
    if energy_norm is not None:
        data[ENERGY_NORM_KEY] = energy_norm
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError as exc:
        log.warning("Could not save config %s: %s", path, exc)


# --- normalizer seed (2026-09-17) --------------------------------------------
# core/metronome.py's adaptive energy normalizer needs ~40 s of music before it
# can classify anything (ENERGY_NORM_MIN_SAMPLES): until then energy_norm reads
# 0.0 and every track starts as BREAK -- on 2026-09-15's first live laser run
# a track audible only 46 s before its drop went BREAK -> DROP with no build.
# The range it converges to is a property of the venue's gain staging far more
# than of the track, so the last real session's p10/p90 is a good first guess
# for the next one. Josh's call (2026-09-17): persist it and seed the next
# launch, rather than shorten the window or start tracks earlier.
#
# The seed is only meaningful for the SAME gain staging, so it carries a
# fingerprint (the static input gain and the audio device) and is ignored --
# with a log line -- when either differs. Demo mode never writes one.
ENERGY_NORM_KEY = "energy_norm"


def _read(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def energy_norm_seed(path: str, fingerprint: dict) -> tuple[float, float] | None:
    """The (floor, ceiling) saved by the last real session, or None when there
    is none or it was made under a different ``fingerprint`` (gain/device)."""
    seed = _read(path).get(ENERGY_NORM_KEY)
    if not isinstance(seed, dict):
        return None
    try:
        floor, ceiling = float(seed["floor"]), float(seed["ceiling"])
    except (KeyError, TypeError, ValueError):
        return None
    saved_fp = seed.get("fingerprint") or {}
    if saved_fp != fingerprint:
        log.info("Ignoring saved energy_norm seed from %s: fingerprint %s != %s",
                 seed.get("saved", "?"), saved_fp, fingerprint)
        return None
    if not (0.0 <= floor <= ceiling <= 1.5):
        return None
    log.info("Seeding energy normalizer from %s: floor=%.3f ceiling=%.3f (saved %s)",
             path, floor, ceiling, seed.get("saved", "?"))
    return floor, ceiling


def energy_norm_record(floor: float, ceiling: float, fingerprint: dict) -> dict:
    """The dict `save_from` writes under ENERGY_NORM_KEY."""
    from datetime import datetime

    return {
        "floor": round(float(floor), 4),
        "ceiling": round(float(ceiling), 4),
        "fingerprint": dict(fingerprint),
        "saved": datetime.now().isoformat(timespec="seconds"),
    }
