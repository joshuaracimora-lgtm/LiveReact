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
    """Comb-filter tempo estimate on the kick-band onset curve, 0.1 BPM steps.

    For each candidate BPM the onset envelope is sampled (linearly interpolated,
    so block resolution doesn't quantize the lag) on a beat grid at 24 phases;
    the best phase's sum is the candidate's score. librosa, if importable, is
    tried first and accepted when it lands in [lo, hi]. The Rekordbox value
    always beats either.
    """
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
    t_blocks = np.arange(len(onset)) * dt
    t_end = t_blocks[-1]
    best, best_score = 128.0, -1.0
    for bpm10 in range(int(lo * 10), int(hi * 10) + 1):
        bpm = bpm10 / 10.0
        beat = 60.0 / bpm
        n_beats = int(t_end / beat) - 1
        k = np.arange(n_beats)[None, :] * beat
        phases = np.linspace(0.0, beat, 24, endpoint=False)[:, None]
        grid = phases + k                                   # (24, n_beats)
        score = np.interp(grid, t_blocks, onset).sum(axis=1).max() / n_beats
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


# ----------------------------------------------------------------- markers

def _bar_energy(audio, timeline, np):
    """Per-bar low-band (kick) energy and full-band RMS, aligned to the bar grid.

    Returns (bar_start_times, low_per_bar, rms_per_bar), both normalized to the
    track's 95th percentile so thresholds are relative to the track itself.
    """
    n_blocks = len(audio) // BLOCK
    x = audio[: n_blocks * BLOCK].reshape(n_blocks, BLOCK)
    win = np.hanning(BLOCK)
    spec = np.abs(np.fft.rfft(x * win, axis=1))
    freqs = np.fft.rfftfreq(BLOCK, d=1 / SR)
    low = spec[:, (freqs >= 30) & (freqs <= 150)].sum(axis=1)
    rms = np.sqrt((x ** 2).mean(axis=1))
    dt = BLOCK / SR
    t_blocks = np.arange(n_blocks) * dt
    bar_dur = timeline.bar_dur
    t0 = timeline.first_downbeat_s % bar_dur
    bars = np.arange(t0, t_blocks[-1], bar_dur)
    idx = np.searchsorted(t_blocks, bars)
    low_b = np.array([low[a:b].mean() if b > a else 0.0 for a, b in zip(idx[:-1], idx[1:])])
    rms_b = np.array([rms[a:b].mean() if b > a else 0.0 for a, b in zip(idx[:-1], idx[1:])])
    def norm(v):
        p = np.percentile(v, 95)
        return v / p if p > 0 else v
    return bars[:-1], norm(low_b), norm(rms_b)


def estimate_markers(audio, timeline: Timeline, np,
                     drop_hi: float = 0.75, drop_lo: float = 0.45,
                     break_lo: float = 0.35, min_low_bars: int = 4, min_hold_bars: int = 4):
    """Guess drops and breakdowns from the kick band, snapped to bar lines.

    A *breakdown* starts at the first of >= ``min_low_bars`` consecutive bars
    whose kick energy is below ``break_lo``. A *drop* is a bar whose kick
    energy jumps from below ``drop_lo`` (averaged over the previous 2 bars) to
    above ``drop_hi`` and stays above ``drop_lo`` for ``min_hold_bars``. The
    first such bar after the intro is also a drop (the track "starting").
    Every marker is a GUESS to be corrected by ear; ``note`` says so.
    """
    from .timeline import Marker
    bars, low, rms = _bar_energy(audio, timeline, np)
    n = len(bars)
    markers: list[Marker] = []
    in_low = False
    low_run = 0
    for i in range(n):
        e = low[i]
        prev = low[max(0, i - 2):i].mean() if i >= 1 else 0.0
        hold_ok = i + min_hold_bars <= n and (low[i:i + min_hold_bars] > drop_lo).all()
        if e >= drop_hi and prev < drop_lo and hold_ok and (in_low or i == 0 or low_run >= 2):
            markers.append(Marker(t=round(float(bars[i]), 3), kind="drop",
                                  note=f"est: kick {prev:.2f}->{e:.2f}"))
            in_low = False
            low_run = 0
            continue
        if e < break_lo:
            low_run += 1
            if low_run == min_low_bars and not in_low:
                start = i - min_low_bars + 1
                if start > 0:   # the intro is not a breakdown
                    markers.append(Marker(t=round(float(bars[start]), 3), kind="breakdown",
                                          note=f"est: kick fell to {low[start]:.2f}"))
                in_low = True
        else:
            low_run = 0
    return markers


def fmt_mmss(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:05.2f}"
