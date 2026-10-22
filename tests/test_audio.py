"""Tests for the real audio analyzer (docs/14).

The capture side (sounddevice callback -> ring buffer -> analysis thread) needs
real hardware and isn't unit-tested here (matches how prodj.py's raw-socket
receive loop isn't unit-tested either) — this covers the pure, hardware-free
parts: block analysis math and the clear-error device-not-found path, plus a
regression test pinning down the verified-by-simulation band-math bug so it
can never silently come back (docs/14 §1, tools/simulations/audio_pipeline_sim_v2.py).
"""

from __future__ import annotations

import queue
import threading

import numpy as np
import pytest

from livereact.clock import FakeClock
from livereact.core.messages import AudioLevelMessage
from livereact.inputs.audio import AudioAnalyzer, AudioInputError, BLOCK


def _analyzer() -> AudioAnalyzer:
    a = AudioAnalyzer(queue.Queue(), FakeClock(), threading.Event())
    a._sr = 44100
    a._setup_bands(np)
    return a


def test_find_device_raises_clear_error_without_a_matching_device():
    class _FakeSd:
        @staticmethod
        def query_devices():
            return [{"name": "Built-in Microphone", "max_input_channels": 1}]

    with pytest.raises(AudioInputError, match="No XDJ audio input device"):
        AudioAnalyzer._find_device(_FakeSd())


def test_find_device_matches_by_name_case_insensitively():
    class _FakeSd:
        @staticmethod
        def query_devices():
            return [
                {"name": "Built-in Microphone", "max_input_channels": 1},
                {"name": "XDJ-XZ", "max_input_channels": 8},
            ]

    assert AudioAnalyzer._find_device(_FakeSd()) == 1


def test_analyze_block_emits_higher_rms_for_a_louder_block():
    a = _analyzer()
    quiet = (np.random.default_rng(1).standard_normal(BLOCK) * 0.001).astype(np.float32)
    loud = (np.random.default_rng(1).standard_normal(BLOCK) * 0.5).astype(np.float32)

    a._analyze_block(quiet, attack=1.0, release=1.0, np=np)
    quiet_msg = a.queue.get_nowait()
    a._rms_smooth = 0.0  # reset smoothing between the two probes
    a._analyze_block(loud, attack=1.0, release=1.0, np=np)
    loud_msg = a.queue.get_nowait()

    assert isinstance(quiet_msg, AudioLevelMessage)
    assert loud_msg.rms > quiet_msg.rms


def test_analyze_block_reads_a_pure_low_tone_as_low_dominant():
    t = np.arange(BLOCK) / 44100
    tone = (np.sin(2 * np.pi * 80 * t) * 0.8).astype(np.float32)  # 80Hz, well inside the low band
    a = _analyzer()
    a._analyze_block(tone, attack=1.0, release=1.0, np=np)
    msg = a.queue.get_nowait()
    assert msg.band_low > msg.band_mid
    assert msg.band_low > msg.band_high


def test_normalized_band_fraction_is_not_biased_toward_the_wider_high_band():
    """Regression test for the bug found by simulation (docs/14): summing raw
    FFT magnitude per band (instead of averaging per bin) reads pure noise as
    ~64% "high"-dominant purely because the high band has ~65x more FFT bins
    than the low band at this block size — not because of real content."""
    noise = (np.random.default_rng(7).standard_normal(BLOCK) * 0.01).astype(np.float32)
    a = _analyzer()
    a._analyze_block(noise, attack=1.0, release=1.0, np=np)
    msg = a.queue.get_nowait()
    assert max(msg.band_low, msg.band_mid, msg.band_high) < 0.6  # no single band dominates pure noise


def test_band_low_abs_is_not_normalized_like_band_low():
    """docs/14: band_low_abs must stay a separate, un-normalized signal — the
    normalized fraction saturates near-constant under continuous bass and is a
    bad signal for onset detection (verified: 1.06x vs 2.35x contrast)."""
    t = np.arange(BLOCK) / 44100
    quiet_bass = (np.sin(2 * np.pi * 60 * t) * 0.1).astype(np.float32)
    loud_bass = (np.sin(2 * np.pi * 60 * t) * 0.9).astype(np.float32)
    a = _analyzer()
    a._analyze_block(quiet_bass, attack=1.0, release=1.0, np=np)
    quiet_msg = a.queue.get_nowait()
    a._analyze_block(loud_bass, attack=1.0, release=1.0, np=np)
    loud_msg = a.queue.get_nowait()
    # the normalized fraction is near-saturated for any pure low tone either way...
    assert quiet_msg.band_low > 0.9 and loud_msg.band_low > 0.9
    # ...but the absolute signal still shows the real loudness difference.
    assert loud_msg.band_low_abs > quiet_msg.band_low_abs * 5


def test_band_mid_abs_and_high_abs_are_not_normalized_either():
    """build_snare_accent Step 0 (2026-09-14): a snare/hi-hat onset detector
    needs the same un-normalized signal for the mid/high bands that the
    laser's kick detector already gets for the low band -- same structural
    reason, verified the same way (a pure tone's normalized fraction
    saturates near-constant regardless of volume; the absolute magnitude
    still tracks real loudness)."""
    t = np.arange(BLOCK) / 44100
    quiet_mid = (np.sin(2 * np.pi * 800 * t) * 0.1).astype(np.float32)
    loud_mid = (np.sin(2 * np.pi * 800 * t) * 0.9).astype(np.float32)
    quiet_high = (np.sin(2 * np.pi * 6000 * t) * 0.1).astype(np.float32)
    loud_high = (np.sin(2 * np.pi * 6000 * t) * 0.9).astype(np.float32)

    a = _analyzer()
    a._analyze_block(quiet_mid, attack=1.0, release=1.0, np=np)
    quiet_mid_msg = a.queue.get_nowait()
    a._analyze_block(loud_mid, attack=1.0, release=1.0, np=np)
    loud_mid_msg = a.queue.get_nowait()
    assert loud_mid_msg.band_mid_abs > quiet_mid_msg.band_mid_abs * 5

    a._analyze_block(quiet_high, attack=1.0, release=1.0, np=np)
    quiet_high_msg = a.queue.get_nowait()
    a._analyze_block(loud_high, attack=1.0, release=1.0, np=np)
    loud_high_msg = a.queue.get_nowait()
    assert loud_high_msg.band_high_abs > quiet_high_msg.band_high_abs * 5


# --- high-band onset density (2026-09-17, the roll accent's rate signal) -----

def _clicks(rate_hz: float, seconds: float, sr: int = 44100, amp: float = 0.5, seed: int = 1) -> np.ndarray:
    """Short white-noise bursts (10 ms, hi-hat-like; a 3 ms click can vanish under the Hann window's edge) at `rate_hz` over a -60 dB
    noise floor -- the shape a hat pattern has in the high band."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    x = rng.standard_normal(n) * 1e-3
    burst_len = int(0.010 * sr)
    env = np.hanning(burst_len)
    step = sr / rate_hz
    t = 0.0
    while t + burst_len < n:
        i = int(t)
        x[i:i + burst_len] += rng.standard_normal(burst_len) * env * amp
        t += step
    return x.astype(np.float32)


def _feed(a: AudioAnalyzer, x: np.ndarray) -> list[AudioLevelMessage]:
    out = []
    for i in range(0, len(x) - BLOCK + 1, BLOCK):
        a._analyze_block(x[i:i + BLOCK], attack=1.0, release=1.0, np=np)
        out.append(a.queue.get())
    return out


@pytest.mark.parametrize("rate", [4.0, 8.0])
def test_high_onset_rate_counts_synthetic_clicks(rate):
    """8th-note hats at 120 BPM are 4/s, 16ths are 8/s: the trailing rate
    must read within about one onset of the truth once the window is full."""
    a = _analyzer()
    msgs = _feed(a, _clicks(rate, 4.0))
    settled = [m.high_onset_rate for m in msgs[-40:]]  # last ~1 s
    assert min(settled) >= rate - 1.0 and max(settled) <= rate + 1.0, settled


def test_a_16th_roll_reads_far_above_8th_hats_even_at_the_resolution_limit():
    """32nd-note density (16/s) sits at the ~70 ms refractory's ceiling and
    is allowed to read low -- but it must still read well above 8/s, which
    is all the roll detector needs from it."""
    a = _analyzer()
    msgs = _feed(a, _clicks(16.0, 4.0))
    assert min(m.high_onset_rate for m in msgs[-40:]) >= 10.0


def test_high_onset_rate_is_zero_for_silence_and_a_steady_tone():
    a = _analyzer()
    silence = np.zeros(BLOCK * 60, dtype=np.float32)
    assert max(m.high_onset_rate for m in _feed(a, silence)) == 0.0
    a = _analyzer()
    t = np.arange(BLOCK * 60) / 44100
    tone = (0.3 * np.sin(2 * np.pi * 4000 * t)).astype(np.float32)
    msgs = _feed(a, tone)
    assert max(m.high_onset_rate for m in msgs[5:]) == 0.0  # the first block's own attack may count


def test_low_band_kicks_do_not_register_as_high_onsets():
    """A 60 Hz kick pattern has no energy above 2 kHz: the HIGH-band flux
    must stay flat, so a kick-heavy build can't fake a roll."""
    a = _analyzer()
    sr = 44100
    n = sr * 4
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float32)
    for k in range(0, n, sr // 2):  # a kick every 0.5 s
        seg = slice(k, min(n, k + int(0.15 * sr)))
        env = np.exp(-np.arange(seg.stop - seg.start) / (0.03 * sr))
        x[seg] += (0.8 * np.sin(2 * np.pi * 60 * t[seg]) * env).astype(np.float32)
    msgs = _feed(a, x)
    assert max(m.high_onset_rate for m in msgs[-80:]) <= 1.0


def test_onset_rate_is_independent_of_input_gain():
    """A ratio on the raw spectrum: the same pattern at -20 dB and +20 dB
    must count the same onsets (the AGC only ever touches rms)."""
    quiet = _feed(_analyzer(), _clicks(8.0, 3.0, amp=0.1))
    loud = _feed(_analyzer(), _clicks(8.0, 3.0, amp=10.0))
    q = [m.high_onset_rate for m in quiet[-40:]]
    l = [m.high_onset_rate for m in loud[-40:]]
    assert abs(sum(q) / len(q) - sum(l) / len(l)) <= 1.0


def test_white_noise_and_a_noise_riser_produce_no_onsets():
    """The build's most common texture is a white-noise riser; its spectrum
    fluctuates block to block but never clears ONSET_RATIO x its own
    between-onset mean, so it can't read as a roll. Steady hiss neither
    (the running mean starts cold and is excluded from onset blocks, so
    the first second may count a few -- measured after that)."""
    rng = np.random.default_rng(0)
    for level in (0.01, 0.1):
        hiss = (rng.standard_normal(BLOCK * 200) * level).astype(np.float32)
        assert max(m.high_onset_rate for m in _feed(_analyzer(), hiss)[-120:]) == 0.0
    n = BLOCK * 170
    riser = (rng.standard_normal(n) * np.linspace(0.001, 0.2, n)).astype(np.float32)
    assert max(m.high_onset_rate for m in _feed(_analyzer(), riser)[-60:]) == 0.0


def test_band_snare_abs_reads_the_snare_body_band_not_the_hats():
    """SNARE_HZ (150-500 Hz, 2026-10-22) feeds core/roll_period.py; it overlaps
    LOW/MID by design and is absolute like the other *_abs fields."""
    import numpy as np
    from livereact.inputs.audio import BLOCK
    a = _analyzer()
    a._setup_bands(np)
    t = np.arange(BLOCK) / a._sr
    a._analyze_block((0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32), attack=1.0, release=1.0, np=np)
    snare_msg = a.queue.get_nowait()
    a._analyze_block((0.3 * np.sin(2 * np.pi * 6000 * t)).astype(np.float32), attack=1.0, release=1.0, np=np)
    hat_msg = a.queue.get_nowait()
    assert snare_msg.band_snare_abs > hat_msg.band_snare_abs * 20
    assert hat_msg.band_high_abs > snare_msg.band_high_abs * 20
    # other producers can leave it out
    from livereact.core.messages import AudioLevelMessage
    assert AudioLevelMessage(0.0, 0.1, 0.3, 0.3, 0.4, 1.0, 1.0, 1.0).band_snare_abs == 0.0
