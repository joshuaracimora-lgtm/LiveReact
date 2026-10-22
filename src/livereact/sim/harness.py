"""The replay harness: run the REAL LiveReact pipeline from files, headless.

Substitutes exactly the three live-only inputs and the one live-only output:

  Pro DJ Link beat feed  -> synthetic BeatMessages on the Timeline's grid
  MIDI mixer gestures    -> GestureScript -> MidiControlMessages
  live audio capture     -> decoded file, block by block, into the same
                            AudioAnalyzer math (no sounddevice stream)
  Enttec DMX output      -> an in-memory frame log (uint8[N, 512] + timestamps)

Everything between -- AudioAnalyzer analysis, Metronome, every engine, merge,
finalize -- is the untouched production code path, driven by a FakeClock so a
5-minute track simulates in seconds and the SAME inputs produce byte-identical
frame logs run after run (tests/test_harness_determinism.py enforces this).

Grown out of tools/simulations/replay_song.py (2026-09-07), which proved the
approach; this version is rig-agnostic, records the full universe every tick
instead of four hand-picked fixtures' VisualState, drives scripted MIDI, aligns
bar 1 to the track's real downbeat, and returns a machine-readable RunResult so
an unattended agent can score it. Nothing here touches the lighting, merge or
DMX paths (CLAUDE.md).

NOTE on tick rate: like replay_song.py, the engine ticks once per audio block
(1024 samples @ 44.1 kHz ~= 43 Hz), not at the live DmxWriter's 40 Hz. Timing
rules in the scorer should use the recorded timestamps, not frame counts.
"""
from __future__ import annotations

import csv
import json
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..clock import FakeClock
from ..core.frame_buffer import DmxFrameBuffer
from ..core.messages import BeatMessage
from ..core.metronome import Metronome
from ..core.show_engine import ShowEngine
from ..core.show_state import ShowState
from ..engines.laser import LaserEngine
from ..engines.phrase import PhraseEngine
from ..engines.reactive import ReactiveLayerEngine
from ..engines.transition import TransitionEngine
from ..fixtures.rig import load_rig
from ..inputs.audio import ATTACK_S, BLOCK, RELEASE_S, AudioAnalyzer
from ..merge.merge import ManualParams
from .gestures import GestureScript
from .timeline import Timeline

SR = 44100
# Decoded files read far hotter than the live mic path; scale to the real
# measured range so file runs are comparable to live ones. See the long note
# in tools/simulations/replay_song.py (found by testing, 2026-09-07).
TARGET_RAW_RMS = 0.08

# Rules the simulation can never verify: strobe Hz lives in fixture firmware
# (one written byte -> a rate the engine never sees). The scorer must report
# these as unverifiable, never as PASS. (Confirmed Case B, Oct 2026.)
def default_seed_range(config_path: str | Path = "config.json") -> "tuple[float, float] | None":
    """The live app's saved normalizer range, if config.json has one (fingerprint ignored:
    the sim's gain calibration targets the same measured raw range as the live mic path)."""
    try:
        seed = json.loads(Path(config_path).read_text(encoding="utf-8")).get("energy_norm") or {}
        return float(seed["floor"]), float(seed["ceiling"])
    except Exception:
        return None


UNVERIFIABLE_IN_SIM = ["SAFE-01", "SAFE-02", "SAFE-03", "SAFE-04", "SAFE-05", "SAFE-06"]


# ----------------------------------------------------------------- audio in

def decode_audio(path: str | Path, duration: float | None = None) -> "np.ndarray":
    """ffmpeg -> mono float32 at SR. ffmpeg is a binary on PATH, not a dependency."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
           "-f", "f32le", "-acodec", "pcm_f32le"]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["pipe:1"]
    try:
        raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found on PATH -- required to decode audio files") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg failed on {path}: {exc.stderr.decode(errors='replace')}") from exc
    return np.frombuffer(raw, dtype=np.float32)


def calibrate_gain(audio: "np.ndarray", override: float | None = None) -> float:
    import numpy as np
    if override is not None:
        return override
    rms = float(np.sqrt(np.mean(audio ** 2)))
    return 1.0 if rms <= 1e-9 else TARGET_RAW_RMS / rms


# ----------------------------------------------------------------- results

@dataclass
class RunResult:
    """Everything one simulation produced. ``frames`` is the whole output."""
    track: str
    rig: str
    timeline: Timeline
    gestures: str | None
    frames: Any                 # np.uint8 [n_ticks, 512]
    t: Any                      # np.float64 [n_ticks] virtual seconds
    signals: list[dict]         # one row per tick, engine-internal signals
    summary: dict = field(default_factory=dict)

    def save(self, out_dir: str | Path) -> Path:
        """Write frames.npz, signals.csv, summary.json into ``out_dir``."""
        import numpy as np
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(out / "frames.npz", frames=self.frames, t=self.t)
        if self.signals:
            with open(out / "signals.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(self.signals[0].keys()))
                w.writeheader()
                w.writerows(self.signals)
        (out / "summary.json").write_text(json.dumps(self.summary, indent=2), encoding="utf-8")
        return out

    def frames_digest(self) -> str:
        import hashlib
        return hashlib.sha256(self.frames.tobytes()).hexdigest()


# ----------------------------------------------------------------- the run

def run_sim(
    track: str | Path,
    timeline: Timeline,
    rig_path: str | Path,
    gestures: str | Path | None = None,
    duration: float | None = None,
    gain: float | None = None,
    manual: ManualParams | None = None,
    audio: "np.ndarray | None" = None,
    seed_range: "tuple[float, float] | None" = None,
    params: "dict | None" = None,
) -> RunResult:
    """Simulate one track end to end and return the full frame log.

    ``audio`` lets tests pass pre-generated samples instead of a file; then
    ``track`` is just a label. ``seed_range`` = (floor, ceiling) seeds the
    energy normalizer the way the live app does from config.json's last
    session; None cold-starts it (the live ``--no-norm-seed`` behaviour --
    energy_norm reads 0.0 for the first ~40 s, found on the first scored run).
    ``params`` = knob overrides (livereact.params / params.json), applied before
    the engines are built and restored afterwards so runs stay independent.
    """
    import numpy as np
    from .. import params as knobs
    previous = knobs.apply(params) if params else {}
    try:
        return _run_sim(track, timeline, rig_path, gestures, duration, gain, manual, audio, seed_range, params, np)
    finally:
        if previous:
            knobs.restore(previous)


def _run_sim(track, timeline, rig_path, gestures, duration, gain, manual, audio, seed_range, params, np):

    if audio is None:
        audio = decode_audio(track, duration)
    n_blocks = len(audio) // BLOCK
    audio = audio[: n_blocks * BLOCK]
    applied_gain = calibrate_gain(audio, gain)
    audio = audio * applied_gain
    dt_block = BLOCK / SR
    attack = 1 - np.exp(-dt_block / ATTACK_S)
    release = 1 - np.exp(-dt_block / RELEASE_S)

    clock = FakeClock()
    shared_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    show_state = ShowState()
    metronome = Metronome(shared_queue, show_state, clock, stop_event, seed_range=seed_range)

    # Analysis-only AudioAnalyzer: never .start() (that opens a real stream).
    analyzer = AudioAnalyzer(shared_queue, clock, stop_event)
    analyzer._sr = SR
    analyzer._setup_bands(np)

    rig = load_rig(str(rig_path))
    # roll_sink: app.py parity -- the reactive engine's roll detector arms the
    # metronome's drop (Metronome.note_roll). Missing until 2026-10-22, so the
    # sim never had a roll-armed drop and STRUCT-03 under-read the live engine.
    reactive = ReactiveLayerEngine(clock, roll_sink=metronome.note_roll)
    phrase = PhraseEngine()
    laser_engine = LaserEngine(clock)
    transition = TransitionEngine()
    frame_buffer = DmxFrameBuffer()
    manual = manual if manual is not None else ManualParams(laser_enable=True)
    show_engine = ShowEngine(show_state, rig, frame_buffer,
                             [reactive, phrase, laser_engine, transition],
                             manual, clock, stop_event, laser_engine=laser_engine)

    script = GestureScript.load(gestures)
    slots = [getattr(fx, "slot_id", fx.label) for fx in rig.fixtures]

    beat_dur = timeline.beat_dur
    next_beat_time, beat_in_bar = timeline.grid_start()

    frames = np.zeros((n_blocks, 512), dtype=np.uint8)
    times = np.zeros(n_blocks, dtype=np.float64)
    rows: list[dict] = []

    for i in range(n_blocks):
        clock.advance(dt_block)
        now = clock.monotonic()

        while next_beat_time <= now:
            shared_queue.put(BeatMessage(timestamp=next_beat_time, beat_in_bar=beat_in_bar,
                                         bpm=timeline.bpm, is_master=True, player=1))
            beat_in_bar = beat_in_bar % timeline.beats_per_bar + 1
            next_beat_time += beat_dur
        for msg in script.messages_at(now):
            shared_queue.put(msg)

        analyzer._analyze_block(audio[i * BLOCK:(i + 1) * BLOCK], attack, release, np)
        snap = metronome.tick()
        frame = show_engine.tick()
        if frame is None:            # frozen -> hold last frame, same as the writer
            frame = frame_buffer.latest()
        frames[i, :len(frame)] = np.frombuffer(bytes(frame), dtype=np.uint8)[:512]
        times[i] = now

        row = {
            "t": round(now, 4), "bar": snap.bar, "beat": snap.beat_in_bar,
            "beat_phase": round(snap.beat_phase, 3),
            "energy": round(snap.energy, 4), "energy_norm": round(snap.energy_norm, 4),
            "bucket": snap.bucket.upper(), "drop_cycle": snap.drop_cycle,
            "drop_marker": snap.drop_marker, "build_cue": snap.build_cue,
            "wash_level": round(reactive.wash_level, 4),
            "band_low": round(snap.audio_band_low_abs, 4),
            "band_mid": round(snap.audio_band_mid_abs, 4),
            "band_high": round(snap.audio_band_high_abs, 4),
            "onset_rate": round(snap.audio_high_onset_rate, 1),
            "palette": snap.palette, "scene": phrase._current.name,
            "laser_on": laser_engine.active, "silence_active": show_engine.silence.active,
            "crossfader": round(snap.crossfader, 3), "fader_a": round(snap.fader_a, 3),
        }
        for slot, fx in zip(slots, rig.fixtures):
            vs = fx.visual_state(frame)
            row[f"{slot}_brightness"] = round(vs.brightness, 3)
            row[f"{slot}_active"] = vs.active
        rows.append(row)

    summary = _summarize(rows, frames, times, rig, slots, applied_gain, timeline)
    summary["seed_range"] = list(seed_range) if seed_range else None
    summary["params"] = dict(params) if params else {}
    return RunResult(track=str(track), rig=str(rig_path), timeline=timeline,
                     gestures=str(gestures) if gestures else None,
                     frames=frames, t=times, signals=rows, summary=summary)


def _summarize(rows, frames, times, rig, slots, applied_gain, timeline) -> dict:
    import numpy as np
    n = len(rows)
    total_s = float(times[-1]) if n else 0.0
    buckets: dict[str, int] = {}
    flips = 0
    prev = None
    scene_changes: list[float] = []
    prev_scene = None
    for r in rows:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
        if prev is not None and r["bucket"] != prev:
            flips += 1
        prev = r["bucket"]
        if prev_scene is not None and r["scene"] != prev_scene:
            scene_changes.append(r["t"])
        prev_scene = r["scene"]

    # Scene-change timing vs the bar grid: how far each look change landed
    # from the nearest downbeat. This is the first scorer-shaped number.
    bar_dur = timeline.bar_dur
    _, _ = timeline.grid_start()
    downbeat0 = timeline.first_downbeat_s % bar_dur
    offsets_ms = [abs(((t - downbeat0 + bar_dur / 2) % bar_dur) - bar_dur / 2) * 1000 for t in scene_changes]

    per_fixture = {}
    for slot, fx in zip(slots, rig.fixtures):
        chans = frames[:, fx.offset: fx.offset + fx.channel_count]
        per_fixture[slot] = {
            "role": fx.role,
            "active_pct": round(100 * float(np.mean([r[f"{slot}_active"] for r in rows])), 1) if n else 0.0,
            "mean_brightness": round(float(np.mean([r[f"{slot}_brightness"] for r in rows])), 3) if n else 0.0,
            "channel_max": [int(x) for x in chans.max(axis=0)] if n else [],
        }

    return {
        "track": None,  # filled by caller via RunResult.track if needed
        "ticks": n,
        "total_seconds": round(total_s, 2),
        "bpm": timeline.bpm,
        "first_downbeat_s": timeline.first_downbeat_s,
        "applied_gain": round(applied_gain, 3),
        "bucket_distribution_pct": {k: round(100 * v / n, 1) for k, v in buckets.items()} if n else {},
        "bucket_flips": flips,
        "bucket_flips_per_min": round(flips / (total_s / 60), 2) if total_s > 0 else 0.0,
        "laser_active_pct": round(100 * sum(1 for r in rows if r["laser_on"]) / n, 2) if n else 0.0,
        "silence_pct": round(100 * sum(1 for r in rows if r["silence_active"]) / n, 2) if n else 0.0,
        "scene_changes": len(scene_changes),
        "scene_change_offset_from_downbeat_ms": {
            "mean": round(float(np.mean(offsets_ms)), 1) if offsets_ms else None,
            "max": round(float(np.max(offsets_ms)), 1) if offsets_ms else None,
        },
        "fixtures": per_fixture,
        "unverifiable_in_sim": UNVERIFIABLE_IN_SIM,
    }
