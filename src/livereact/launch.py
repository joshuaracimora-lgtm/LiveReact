"""What a plain ``python -m livereact`` launch should use when the operator passes no flags (2026-10-30).

The first-run setup (ui/first_run.py) and the Rig manager save their choices in the user's settings
(settings.py); until now nothing read them back, so a user's answers never reached the show. This
module turns those saved choices into launch defaults. An explicit command-line flag always wins.
Art-Net and sACN are offered as "coming later" only (no backend exists yet), so they never become a
launch choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import Settings

SETUP_KEY = "setup"           # same key ui/first_run.py writes
SUPPORTED_OUTPUTS = ("enttec", "demo")


@dataclass(frozen=True)
class LaunchDefaults:
    hardware: bool | None = None       # None = no saved choice
    audio_device: str | None = None
    pdl_interface: str | None = None
    rig: str | None = None
    notes: tuple[str, ...] = ()        # what was applied or skipped, for the log


def from_settings(settings: Settings) -> LaunchDefaults:
    setup = settings.extra.get(SETUP_KEY) or {}
    notes: list[str] = []
    hardware = None
    out = setup.get("output")
    if out in SUPPORTED_OUTPUTS:
        hardware = out == "enttec"
        notes.append(f"output {out} (first-run setup)")
    elif out:
        notes.append(f"saved output {out!r} is not available in this version; using demo")
        hardware = False
    audio = setup.get("audio_device") if isinstance(setup.get("audio_device"), str) else None
    iface = setup.get("pdl_interface") if isinstance(setup.get("pdl_interface"), str) else None
    if iface == "none":
        iface = None
    rig = None
    for r in settings.recent_rigs:
        if Path(r).is_file():
            rig = r
            notes.append(f"rig {Path(r).name} (most recent in the Rig manager)")
            break
    return LaunchDefaults(hardware=hardware, audio_device=audio, pdl_interface=iface, rig=rig, notes=tuple(notes))


def apply_to_args(args, defaults: LaunchDefaults, *, hardware_flag_given: bool) -> list[str]:
    """Fill ``args`` (the argparse namespace) from ``defaults`` where the operator gave no flag."""
    applied: list[str] = []
    if not hardware_flag_given and defaults.hardware:
        args.hardware = True
        applied.append("--hardware")
    if args.audio_device is None and defaults.audio_device:
        args.audio_device = defaults.audio_device
        applied.append(f"--audio-device {defaults.audio_device!r}")
    if args.pdl_interface is None and defaults.pdl_interface:
        args.pdl_interface = defaults.pdl_interface
        applied.append(f"--pdl-interface {defaults.pdl_interface}")
    if args.rig is None and defaults.rig:
        args.rig = defaults.rig
        applied.append(f"--rig {defaults.rig}")
    return applied
