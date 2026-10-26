"""Application supervisor: wires the components, owns the lifecycle.

Phase 0 wiring is intentionally thin — a frame buffer, an output backend, and the
DMX writer thread, paced by a real clock and shut down cleanly on Ctrl-C or a stop
signal. Later phases attach the metronome, input sources, show engine, and UI to
the same supervisor without changing its shutdown contract.
"""

from __future__ import annotations

import logging
import os
import queue
import signal
import threading
from dataclasses import dataclass

from . import config as app_config
from .clock import Clock, RealClock
from .core.frame_buffer import DmxFrameBuffer
from .core.metronome import Metronome
from .core.show_engine import ShowEngine
from .core.show_state import ShowState
from .engines.laser import LaserEngine
from .engines.laser_feature import LaserFeatureEngine
from .engines.laser_manual import LaserManualEngine
from .engines.laser_show import LaserShowEngine
from .engines.laser_trigger import LaserTriggerEngine
from .engines.phrase import PhraseEngine
from .engines.reactive import ReactiveLayerEngine
from .engines.transition import TransitionEngine
from .scenes.catalog import show_catalog
from .fixtures.rig import Rig, load_rig
from .inputs.demo import DemoDataSource
from .merge.merge import ManualParams
from .output.base import OutputBackend
from .output.null_log import NullLogBackend
from .output.writer import DmxWriter

log = logging.getLogger(__name__)


@dataclass
class AppConfig:
    demo: bool = True
    dmx_rate_hz: float = 40.0
    duration: float | None = None  # bounded run for testing; None = run until signal
    config_path: str | None = "config.json"  # None disables persistence
    midi_map_path: str = "midi_map.json"      # discovered XDJ CC map (hardware mode)
    pdl_interface: str | None = None          # explicit NIC for Pro DJ Link
    record: bool = False                      # record this session for analysis
    session_dir: str | None = None            # where to record (set by the CLI)
    rig_path: str | None = None               # custom rig JSON; None = packaged default_rig.json
    audio_device: str | int | None = None     # explicit audio input device; None = auto-detect "XDJ"
    bible_failsafe_enabled: bool = False      # Bible-integration Phase 1 gig-safe flag: off by
        # default (old instant-hard-zero auto_blackout stays selectable); when True, auto_blackout
        # fades to LIGHTING_BIBLE.md's real safe_default look instead (LOOK-08/FAIL-04). See
        # docs/bible_integration/ and /Users/joshuaracimora/.claude/plans/adaptive-squishing-pebble.md.
    full_catalog: bool = False  # rotate ALL_SCENES instead of the one-look-per-bucket
        # CORE_SCENES default (scenes/library.py, 2026-09-15) -- see --full-catalog.
    norm_seed: bool = True  # seed the energy normalizer from the last real session's
        # persisted range (config.py energy_norm, 2026-09-17); `--no-norm-seed` for a
        # cold start (new venue, re-gained mixer). Demo mode never reads or writes it.
    phrases: str = "shadow"  # rekordbox phrase data via XDJ-XZ status packets + NFS (2026-10-25):
        # "off" | "shadow" (fields recorded + shown, lights unaffected) | "drive" (the track plan
        # built from the phrase table decides the sections; audio classifier = fallback). docs/13 D-21.
    rekordbox_cache: str = "cache/rekordbox"  # where fetched export.pdb copies live (gitignored)


class App:
    """Owns the threads and the single stop event; guarantees clean shutdown."""

    def __init__(
        self,
        config: AppConfig | None = None,
        clock: Clock | None = None,
        backend: OutputBackend | None = None,
    ) -> None:
        self.config = config or AppConfig()
        self.clock = clock or RealClock()
        self.stop_event = threading.Event()

        # --- output pipeline (Phase 0; real Enttec in hardware mode) ---
        self.frame_buffer = DmxFrameBuffer()
        self.backend = backend or self._build_backend()
        self.writer = DmxWriter(
            self.frame_buffer,
            self.backend,
            self.clock,
            self.stop_event,
            hz=self.config.dmx_rate_hz,
        )

        # --- input pipeline (Phase 1; real PDL+MIDI in hardware mode) ---
        self.input_queue: "queue.Queue" = queue.Queue()
        self.show_state = ShowState()
        self.metronome = Metronome(self.input_queue, self.show_state, self.clock, self.stop_event,
                                   seed_range=self._energy_norm_seed(),
                                   range_callback=self._persist_energy_norm_range,
                                   control_callback=self._on_operator_control)
        # shared by PhraseEngine + the planner; sim/harness.py builds the identical one (2026-10-26)
        from .rekordbox import section_classes
        self.section_classes = section_classes.load()
        self.scene_catalog = show_catalog(full=self.config.full_catalog, extra=self.section_classes.approved_scenes())
        self.sources = self._build_sources()

        # --- show pipeline (Phases 3-4): snapshot -> engines -> merge -> frame buffer ---
        self.rig: Rig = load_rig(self.config.rig_path)
        self.manual = ManualParams()
        if self.config.config_path:
            app_config.load_into(self.manual, self.config.config_path)
        # In demo mode, simulate the operator arming lasers before the set so drop
        # scenes can show them. Lasers still boot off and only fire in drop scenes;
        # real/hardware mode leaves them disarmed until the operator arms (safety).
        if self.config.demo:
            self.manual.laser_enable = True
        self.phrase_engine = PhraseEngine(self.scene_catalog)
        self.reactive_engine = ReactiveLayerEngine(self.clock, manual=self.manual,  # manual: Synth Sync toggle
                                                   roll_sink=self.metronome.note_roll)  # a roll arms the drop
        # Which laser engine: the rig file's `laser_profile` (fixtures/rig.py).
        # "accent" (the home rig): one sweep per 8-bar block in PEAK, verified
        # live. "feature" (the bar, 2026-10-01): the lasers are the main light,
        # lit for the whole set -- engines/laser_feature.py. Both take manual=
        # for the operator's K cue; both sit behind the same arm switch and
        # geometry gate in finalize.
        # "trigger" (2026-10-06, Josh: "a specific trigger... used more
        # sparingly"): dark by default, 2-bar moments on the drop hit / PEAK
        # blocks / a big wub, plus the J hit and the K cue -- engines/laser_trigger.py.
        self._midi_hit_down = False  # MIDI "laser_hit" rising-edge state
        if self.rig.laser_profile == "feature":
            self.laser_engine = LaserFeatureEngine(self.clock, manual=self.manual)
        elif self.rig.laser_profile == "trigger":
            self.laser_engine = LaserTriggerEngine(self.clock, manual=self.manual)
        elif self.rig.laser_profile == "show":
            # "show" (2026-10-08, Sophia's Thai): the lasers hold a picked
            # scene for the whole set -- engines/laser_show.py, the console's
            # Laser Show tab. Josh: the gate must not keep them dark here, so
            # the arm switch starts ON for this profile (L still disarms,
            # B still blacks out everything instantly).
            self.laser_engine = LaserShowEngine(self.clock, manual=self.manual)
            self.manual.laser_enable = True
        elif self.rig.laser_profile == "manual":
            # "manual" (2026-10-10, Junior Crib): no music input at all -- the
            # operator drives each head from the Venue tab's manual section
            # (engines/laser_manual.py). Armed at launch like "show" so the
            # section's ON actually lights a head; L still disarms, B blacks out.
            self.laser_engine = LaserManualEngine(self.clock, manual=self.manual)
            self.manual.laser_enable = True
        else:
            self.laser_engine = LaserEngine(self.clock, manual=self.manual)
        # Merge is LTP-by-priority per channel kind (merge/merge.py), so list
        # order is cosmetic: Reactive (25) base, Phrase (50) look, Laser (60)
        # owns the laser channels, Transition (100) lift.
        self.engines = [self.reactive_engine, self.phrase_engine, self.laser_engine, TransitionEngine()]
        self.show_engine = ShowEngine(
            self.show_state, self.rig, self.frame_buffer, self.engines,
            self.manual, self.clock, self.stop_event,
            phrase_engine=self.phrase_engine,
            bible_failsafe_enabled=self.config.bible_failsafe_enabled,
            laser_engine=self.laser_engine,
        )

        # Core engine threads (always present). Input sources are managed separately
        # since some (e.g. MIDI) aren't threads themselves.
        self._core_threads: list[threading.Thread] = [
            self.metronome, self.show_engine, self.writer,
        ]

        # --- optional session recording (for hardware testing / feedback) ---
        self.recorder = None
        if self.config.record and self.config.session_dir:
            self._setup_recording(self.config.session_dir)

    def _norm_fingerprint(self) -> dict:
        """What the persisted normalizer range is only valid for: the same static
        input gain and the same audio device (a re-gained mixer or the laptop mic
        fallback is a different loudness range, not a different room)."""
        from .inputs.audio import ENERGY_GAIN

        return {"gain": ENERGY_GAIN, "audio_device": str(self.config.audio_device)}

    def _energy_norm_seed(self) -> tuple[float, float] | None:
        if self.config.demo or not self.config.norm_seed or not self.config.config_path:
            return None
        return app_config.energy_norm_seed(self.config.config_path, self._norm_fingerprint())

    def _persist_energy_norm_range(self, floor: float, ceiling: float) -> None:
        """Save the warm window's range as the next launch's seed -- called by
        the metronome every RANGE_PERSIST_INTERVAL_S, and at shutdown, because
        live runs end with kill -9 (SIGTERM doesn't land under --visualize)."""
        if self.config.demo or not self.config.config_path:
            return
        seed = app_config.energy_norm_record(floor, ceiling, self._norm_fingerprint())
        app_config.save_from(self.manual, self.config.config_path, energy_norm=seed)

    # MIDI controls learned into midi_map.json that are operator CUES rather
    # than mixer intents (inputs/midi_map.py's DEFAULT_CC_MAP). Value is 0..1
    # (CC/127): a knob past half-way, a switch, or a pad held reads as ON.
    MIDI_CUE_THRESHOLD = 0.5

    def _on_operator_control(self, control: str, value: float) -> None:
        """Metronome's control_callback -- runs on the metronome thread, so
        only plain attribute writes on ManualParams (the console refresh
        mirrors them, see ui/console.py's refresh)."""
        if control == "laser_cue":
            self.manual.laser_cue = value >= self.MIDI_CUE_THRESHOLD
        elif control == "laser_hit":
            if value >= self.MIDI_CUE_THRESHOLD and not self._midi_hit_down:
                self.manual.laser_hit += 1
            self._midi_hit_down = value >= self.MIDI_CUE_THRESHOLD

    def _setup_recording(self, session_dir: str) -> None:
        from .recorder import SessionRecorder, write_meta, write_notes_template

        write_notes_template(session_dir)
        write_meta(session_dir, {
            "mode": "demo" if self.config.demo else "hardware",
            "dmx_rate_hz": self.config.dmx_rate_hz,
            "pdl_interface": self.config.pdl_interface,
            "rig_path": self.config.rig_path,
            "full_catalog": self.config.full_catalog,
            "phrases": self.config.phrases,
        })
        self.recorder = SessionRecorder(
            self.show_state, self.frame_buffer, self.clock, self.stop_event, session_dir,
            reactive_engine=self.reactive_engine, phrase_engine=self.phrase_engine,
            laser_engine=self.laser_engine, silence_detector=self.show_engine.silence,
            rig=self.rig,
        )
        self._core_threads.append(self.recorder)
        log.info("Recording session to %s", session_dir)

    # ------------------------------------------------------------------ wiring
    def _build_backend(self) -> OutputBackend:
        """NullLog in demo; the Enttec in hardware mode, falling back to NullLog
        (with a warning) if no device is present so the app still runs."""
        if self.config.demo:
            return NullLogBackend(log_every=int(self.config.dmx_rate_hz))
        try:
            from serial.tools import list_ports

            from .output.enttec import EnttecSerialBackend, select_enttec_port

            if select_enttec_port(list_ports.comports()):
                return EnttecSerialBackend()
            log.warning("Hardware mode: no Enttec detected; output will be logged only.")
        except Exception:  # noqa: BLE001
            log.exception("Hardware mode: Enttec backend unavailable — logging output only.")
        return NullLogBackend(log_every=int(self.config.dmx_rate_hz))

    def _build_sources(self) -> list:
        """DemoDataSource in demo; real PDL + MIDI listeners in hardware mode."""
        if self.config.demo:
            return [DemoDataSource(self.input_queue, self.clock, self.stop_event)]

        from .inputs.audio import AudioAnalyzer
        from .inputs.midi import MidiListener
        from .inputs.midi_map import CcMap
        from .inputs.prodj import ProDjLinkListener

        cc_map = CcMap()
        if self.config.midi_map_path and os.path.exists(self.config.midi_map_path):
            try:
                cc_map = CcMap.load(self.config.midi_map_path)
            except Exception:  # noqa: BLE001
                log.warning("Could not load MIDI map %s; using defaults", self.config.midi_map_path)
        phrases = self.config.phrases if self.config.phrases in ("off", "shadow", "drive") else "shadow"
        self.metronome.phrases_mode = phrases
        if phrases == "drive":
            log.info("rekordbox phrases DRIVE the sections (track plan); the loudness classifier is the fallback")
        self.phrase_service = None
        if phrases != "off":
            try:
                from .rekordbox.service import PhraseService

                pool = self.scene_catalog.names_by_bucket()
                self.phrase_service = PhraseService(self.input_queue, self.clock, stop_event=self.stop_event,
                                                    cache_dir=self.config.rekordbox_cache, scene_pool=pool,
                                                    classes=self.section_classes)
            except Exception:  # noqa: BLE001 - phrase data is optional
                log.exception("rekordbox phrase service unavailable; continuing without it")
        sources: list = []
        if self.phrase_service is not None:
            sources.append(self.phrase_service)   # an InputSource: started/stopped with the rest
        return sources + [
            ProDjLinkListener(self.input_queue, self.clock, self.stop_event,
                              interface=self.config.pdl_interface,
                              announce=phrases != "off",   # off = fully passive, the pre-2026-10-25 listener
                              emit_status=phrases != "off", phrase_service=self.phrase_service),
            MidiListener(self.input_queue, self.clock, cc_map),
            AudioAnalyzer(self.input_queue, self.clock, self.stop_event,
                          device=self.config.audio_device),
        ]

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        log.info(
            "Starting LiveReact (mode=%s, dmx=%.0f Hz, backend=%s)",
            "demo" if self.config.demo else "hardware",
            self.config.dmx_rate_hz,
            self.backend.name,
        )
        for t in self._core_threads:
            t.start()
        for source in self.sources:
            try:
                source.start()
            except Exception:  # noqa: BLE001 - a failed input must not abort startup
                log.exception("Input source %r failed to start; continuing without it", source.name)

    def shutdown(self) -> None:
        if self.stop_event.is_set():
            return
        log.info("Shutting down...")
        self.stop_event.set()
        for source in self.sources:
            try:
                source.stop()
            except Exception:  # noqa: BLE001
                log.exception("Error stopping source %r", source.name)
        threads = self._core_threads + [s for s in self.sources if isinstance(s, threading.Thread)]
        for t in threads:
            t.join(timeout=3.0)
            if t.is_alive():
                log.warning("Thread %s did not stop within timeout", t.name)
        if self.config.config_path:
            seed = None
            if not self.config.demo and self.metronome.norm_warm:
                floor, ceiling = self.metronome.energy_norm_range
                seed = app_config.energy_norm_record(floor, ceiling, self._norm_fingerprint())
                log.info("Saving energy_norm seed for the next launch: floor=%.3f ceiling=%.3f", floor, ceiling)
            app_config.save_from(self.manual, self.config.config_path, energy_norm=seed)  # None keeps the periodic one
        if self.recorder is not None and self.config.session_dir:
            from .recorder import write_summary

            write_summary(self.config.session_dir, {
                "frames_sent": self.writer.frames_sent,
                "state_samples": self.recorder.samples,
            })
        log.info("Shutdown complete (%d frames sent)", self.writer.frames_sent)

    def serve(self) -> None:
        """Start threads and block until a stop signal, Ctrl-C, or duration end."""
        self.install_signal_handlers()
        self.start()
        try:
            if self.config.duration is not None:
                self.stop_event.wait(self.config.duration)
            else:
                # Wake periodically so the main thread stays responsive to signals.
                while not self.stop_event.wait(0.25):
                    pass
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt received")
        finally:
            self.shutdown()

    def install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            log.info("Received signal %s", signum)
            self.stop_event.set()

        # signal.signal only works on the main thread; ignore if not (e.g. tests).
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, AttributeError):
                pass
