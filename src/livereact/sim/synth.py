"""A deterministic synthetic four-on-the-floor clip: no audio file, no ffmpeg.
Used by the determinism test and the search preflight."""
from __future__ import annotations

from .harness import SR


def synth_house(bpm: float = 128.0, seconds: float = 24.0, seed: int = 0):
    import numpy as np
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    t = np.arange(n) / SR
    beat = 60.0 / bpm
    kick = np.zeros(n)
    env_len = int(0.15 * SR)
    for k in np.arange(0.0, seconds, beat):
        i = int(k * SR)
        m = min(n - i, env_len)
        env = np.exp(-np.arange(m) / (0.03 * SR))
        kick[i:i + m] += np.sin(2 * np.pi * 55 * np.arange(m) / SR) * env
    hats = rng.standard_normal(n) * 0.05 * (np.sin(2 * np.pi * t / (beat / 2)) > 0.9)
    build = np.clip((t - 8.0) / 8.0, 0.0, 1.0)
    return (0.6 * kick + hats * build + 0.1 * np.sin(2 * np.pi * 110 * t) * build).astype(np.float32)
