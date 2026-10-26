"""Command-line entry point: ``python -m livereact`` (or the ``livereact`` script)."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import App, AppConfig


def _configure_logging(level: str, log_file: str | None = None, error_file: str | None = None) -> None:
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)-12s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):  # reset (idempotent across calls/tests)
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if error_file:
        eh = logging.FileHandler(error_file, encoding="utf-8")
        eh.setLevel(logging.WARNING)  # just problems, for a quick scan
        eh.setFormatter(fmt)
        root.addHandler(eh)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="livereact",
        description="Beat-reactive DMX stage-lighting controller for live DJ sets.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Run with synthetic data, no hardware (default).")
    mode.add_argument("--hardware", action="store_true",
                      help="Use real devices (Enttec DMX + Pro DJ Link + XDJ MIDI/audio). Pair with --rig rigs/josh_rig.json.")
    p.add_argument("--rate", type=float, default=40.0, dest="rate", help="DMX refresh rate in Hz (default: 40).")
    p.add_argument("--duration", type=float, default=None, help="Run for N seconds then exit (default: until Ctrl-C).")
    p.add_argument("--visualize", action="store_true", help="Open the operator console + virtual rig (PySide6).")
    p.add_argument("--pdl-interface", default=None, help="Explicit network interface/IP for Pro DJ Link.")
    p.add_argument("--phrases", choices=("off", "shadow", "drive"), default="shadow",
                   help="rekordbox phrase data from the XDJ-XZ (status packets + USB phrase tables over NFS): "
                        "off | shadow (default: recorded + shown, lights unaffected) | drive (Stage 3, not yet built).")
    p.add_argument("--rig", default=None, help="Path to a custom rig JSON (default: the packaged 8-fixture default_rig.json).")
    p.add_argument("--full-catalog", action="store_true",
                   help="Rotate the full scene catalog instead of the default one-look-per-bucket core set.")
    p.add_argument("--bible-failsafe", action="store_true",
                   help="Bible-integration Phase 1 (gig-safe migration flag, off by default): "
                        "when set, the no-music auto-blackout failsafe fades to a real safe_default "
                        "look (LIGHTING_BIBLE.md LOOK-08) instead of an instant hard blackout. "
                        "The operator's own manual blackout switch is unaffected either way.")
    p.add_argument("--no-norm-seed", action="store_true",
                   help="Cold-start the energy normalizer instead of seeding it from the last "
                        "session's saved range (new venue / re-gained mixer).")
    p.add_argument("--audio-device", default=None, help="Explicit audio input device name/index for real audio analysis (default: auto-detect the XDJ).")
    p.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR (default: INFO).")
    # Recording / feedback (capture a session to hand back for analysis).
    p.add_argument("--record", action="store_true", help="Record this session (logs + state + notes) to recordings/.")
    p.add_argument("--session-dir", default=None, help="Where to record (default: recordings/session-<timestamp>).")
    p.add_argument("--log-file", default=None, help="Also write all log output to this file.")
    # Hardware bring-up tools (each runs and exits).
    p.add_argument("--fixture-test", action="store_true", help="Open the manual fixture-test panel (drive raw DMX).")
    p.add_argument("--midi-monitor", action="store_true", help="Print/record incoming MIDI to remap the XDJ, then exit.")
    p.add_argument("--net-check", action="store_true", help="Print Pro DJ Link network diagnostics and exit.")
    p.add_argument("--pdl-probe", action="store_true",
                   help="Announce to the XDJ-XZ as rekordbox, print its status packets (track id, play state, "
                        "beat counter) and exit (Ctrl-C or --duration). Pair with --pdl-fetch / --pdl-dump.")
    p.add_argument("--pdl-no-announce", action="store_true", help="--pdl-probe: listen passively (A/B test).")
    p.add_argument("--pdl-fetch", action="store_true",
                   help="--pdl-probe: pull export.pdb + the loaded track's phrase table over NFS and show a live countdown.")
    p.add_argument("--pdl-dump", default=None, help="--pdl-probe: save raw status packets + fetched files under DIR.")
    p.add_argument("--doctor", action="store_true", help="Write an environment/device diagnostics report and exit.")
    p.add_argument("--bundle", nargs="?", const="__latest__", default=None,
                   help="Zip a session folder for sharing (default: the latest), then exit.")
    return p.parse_args(argv)


def _bundle(which: str, log) -> int:
    """Zip a session folder into a single file for sharing."""
    import glob
    import shutil

    if which == "__latest__":
        sessions = sorted(glob.glob(os.path.join("recordings", "session-*")))
        if not sessions:
            log.error("No sessions found in recordings/.")
            return 1
        which = sessions[-1]
    if not os.path.isdir(which):
        log.error("Not a session directory: %s", which)
        return 1
    archive = shutil.make_archive(which.rstrip("/\\"), "zip", which)
    print(f"Bundled {which} -> {archive}")
    return 0


def _bringup_backend(demo: bool, rate: float):
    """Backend for the fixture-test tool: Enttec in hardware mode (fallback to
    NullLog), NullLog in demo."""
    from .output.null_log import NullLogBackend

    if not demo:
        try:
            from serial.tools import list_ports

            from .output.enttec import EnttecSerialBackend, select_enttec_port

            if select_enttec_port(list_ports.comports()):
                return EnttecSerialBackend()
            logging.getLogger("livereact").warning("No Enttec detected; using null backend.")
        except Exception:  # noqa: BLE001
            logging.getLogger("livereact").exception("Enttec unavailable; using null backend.")
    return NullLogBackend(log_every=int(rate))


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; ensure UTF-8 so diagnostics/logs never
    # crash on a non-ASCII character.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    demo = not args.hardware  # demo is the default; --demo is explicit-but-redundant

    # Resolve a session directory if recording, and route logs into it.
    session_dir = None
    if args.record or args.session_dir:
        from .recorder import create_session_dir

        session_dir = args.session_dir or create_session_dir()
        os.makedirs(session_dir, exist_ok=True)  # an explicit --session-dir may not exist yet
    log_file = (os.path.join(session_dir, "session.log") if session_dir else args.log_file)
    error_file = os.path.join(session_dir, "errors.log") if session_dir else None
    _configure_logging(args.log_level, log_file=log_file, error_file=error_file)
    log = logging.getLogger("livereact")

    # --- standalone tools (run and exit) ---
    if args.doctor:
        from .diagnostics import write_report

        os.makedirs("diagnostics", exist_ok=True)
        path = os.path.join("diagnostics", "doctor.txt")
        env = write_report(path)
        from .diagnostics import format_report
        print(format_report(env))
        print(f"\nWritten to {path} (+ .json) — hand this back for analysis.")
        return 0
    if args.bundle is not None:
        return _bundle(args.bundle, log)
    if args.net_check:
        from .network import diagnostics
        print(diagnostics())
        return 0
    if args.pdl_probe:
        from .inputs.pdl_probe_live import run_live

        return run_live(interface=args.pdl_interface, announce=not args.pdl_no_announce,
                        seconds=args.duration, fetch=args.pdl_fetch, dump_dir=args.pdl_dump)
    if args.midi_monitor:
        from .inputs.midi import monitor

        out = os.path.join(session_dir, "midi-monitor.txt") if session_dir else "midi-monitor.txt"
        monitor(duration=args.duration, out_path=out)
        print(f"MIDI capture written to {out}")
        return 0
    if args.fixture_test:
        from .clock import RealClock
        from .fixtures.rig import load_rig
        from .ui.fixture_test import run_fixture_test
        rig = load_rig(args.rig)
        backend = _bringup_backend(demo, args.rate)
        log.info("Fixture test using backend: %s", backend.name)
        run_fixture_test(rig, backend, RealClock(), dmx_rate_hz=args.rate, rig_path=args.rig)
        return 0

    config = AppConfig(demo=demo, dmx_rate_hz=args.rate, duration=args.duration,
                       pdl_interface=args.pdl_interface, rig_path=args.rig,
                       audio_device=args.audio_device,
                       norm_seed=not args.no_norm_seed,
                       bible_failsafe_enabled=args.bible_failsafe,
                       full_catalog=args.full_catalog,
                       phrases=args.phrases,
                       record=bool(session_dir), session_dir=session_dir)
    app = App(config)

    if args.visualize:
        # Qt must own the main thread: run the pipeline in the background and the
        # operator console here; closing the window shuts everything down.
        from .ui.console import run_console

        app.install_signal_handlers()
        app.start()
        try:
            run_console(app)
        finally:
            app.shutdown()
    else:
        app.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
