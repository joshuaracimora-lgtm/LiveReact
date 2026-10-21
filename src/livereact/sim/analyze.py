"""Build a Timeline from audio: BPM (if not given) and the first downbeat.

Deliberately numpy-only so it runs on the Windows agent box with the same
deps the harness already needs. librosa is used for the BPM estimate when it
is importable; otherwise a kick-band onset autocorrelation does it. Either
way the BPM you already know (``--bpm``) beats any estimate.

Downbeat method (BPM known): compute a low-band (30-150 Hz) onset-strength
curve at block resolution, then (1) find the beat-grid phase that maximizes
onset strength sampled on the grid, and (2) among the 4 candidate beats pick
the one whose bar-grid samples carry the most low-band energy. Good enough for
four-on-the-floor house; always open to a hand correction in the JSON.
"""
from __future__ import annotations

from .harness import SR, BLOCK, decode_audio
from .timeline import Timeline


def _low_onset_curve(audio, np):
    n_blocks = len(audio) // BLOCK
    x = audio[: n_blocks * BLOCK].reshape(n_blocks, BLOCK)
    win = np.hanning(BLOCK)
    spec = np.abs(np.fft.rfft(x * win, axis=1))
    freqs = np.fft.rfftfreq(BLOCK, d=1 / SR)
    low = spec[:, (freqs >= 30) & (freqs <= 150)].sum(axis=1)
    onset = np.maximum(0.0, np.diff(low, prepend=low[0]))
    if onset.max() > 0:
        onset /= onset.max()
    return onset, low


def estimate_bpm(audio, np, lo: float = 118.0, hi: float = 136.0) -> float:
    try:
        import librosa  # optional
        tempo = librosa.beat.tempo(y=audio, sr=SR, start_bpm=128.0)
        t = float(np.atleast_1d(tempo)[0])
        if lo <= t <= hi:
            return round(t, 1)
    except Exception:
        pass
    onset, _ = _low_onset_curve(audio, np)
    dt = BLOCK / SR
    best, best_score = 128.0, -1.0
    for bpm10 in range(int(lo * 10), int(hi * 10) + 1):
        bpm = bpm10 / 10.0
        lag = (60.0 / bpm) / dt
        k = int(round(lag))
        # autocorrelation at lag k and 2k (bar-ish), fractional via neighbour blend
        score = float(np.dot(onset[:-k], onset[k:])) + 0.5 * float(np.dot(onset[:-2 * k], onset[2 * k:]))
        if score > best_score:
            best, best_score = bpm, score
    return best


def estimate_first_downbeat(audio, bpm: float, np, beats_per_bar: int = 4) -> float:
    onset, low = _low_onset_curve(audio, np)
    dt = BLOCK / SR
    beat = 60.0 / bpm
    n = len(onset)
    t = np.arange(n) * dt
    # (1) beat phase: scan offsets within one beat at 1 ms resolution
    best_phase, best = 0.0, -1.0
    for ph in np.arange(0.0, beat, 0.001):
        idx = np.round((np.arange(ph, t[-1], beat)) / dt).astype(int)
        idx = idx[idx < n]
        s = float(onset[idx].sum())
        if s > best:
            best_phase, best = ph, s
    # (2) which of the 4 beats is the downbeat: most low-band energy on the bar grid
    best_k, best = 0, -1.0
    for k in range(beats_per_bar):
        start = best_phase + k * beat
        idx = np.round((np.arange(start, t[-1], beat * beats_per_bar)) / dt).astype(int)
        idx = idx[idx < n]
        s = float(low[idx].sum())
        if s > best:
            best_k, best = k, s
    return round(best_phase + best_k * beat, 3)


def analyze(path: str, bpm: float | None = None, duration: float | None = 90.0) -> Timeline:
    import numpy as np
    audio = decode_audio(path, duration)
    if bpm is None:
        bpm = estimate_bpm(audio, np)
    fd = estimate_first_downbeat(audio, bpm, np)
    return Timeline(bpm=float(bpm), first_downbeat_s=fd, source="analyze_track")
