"""Environment + device diagnostics ("doctor").

Captures everything I'd ask about a machine before debugging a hardware session:
Python/OS, installed dep versions, detected serial ports (with VID/PID), MIDI
inputs, whether the Pro DJ Link library is installed, and the network readiness.
Writes a plain-text + JSON report you can hand back for analysis.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import sys

from . import __version__
from .network import diagnostics as network_diagnostics

_DEP_NAMES = ("pyserial", "mido", "python-rtmidi", "PySide6", "pyartnet", "psutil")


def _dep_version(dist_name: str) -> str:
    try:
        import importlib.metadata as md

        return md.version(dist_name)
    except Exception:  # noqa: BLE001
        return "not installed"


def list_serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports

        return [
            {
                "device": p.device,
                "vid": f"0x{p.vid:04x}" if p.vid is not None else None,
                "pid": f"0x{p.pid:04x}" if p.pid is not None else None,
                "description": p.description,
            }
            for p in list_ports.comports()
        ]
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


def list_midi_inputs() -> list[str]:
    try:
        import mido

        return list(mido.get_input_names())
    except Exception as exc:  # noqa: BLE001
        return [f"<error: {exc}>"]


def prodj_link_installed() -> bool:
    try:
        return importlib.util.find_spec("prodj") is not None
    except Exception:  # noqa: BLE001
        return False


def prodj_nfs_importable() -> bool:
    """The vendored library's NFS client (rekordbox export fetch from the player)."""
    try:
        return importlib.util.find_spec("prodj.network.nfsclient") is not None
    except Exception:  # noqa: BLE001
        return False


def collect_environment() -> dict:
    return {
        "livereact_version": __version__,
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "dependencies": {name: _dep_version(name) for name in _DEP_NAMES},
        "serial_ports": list_serial_ports(),
        "midi_inputs": list_midi_inputs(),
        "prodj_link_installed": prodj_link_installed(),
        "prodj_nfs_importable": prodj_nfs_importable(),
        "construct_version": _dep_version("construct"),
        "network": network_diagnostics(),
    }


def format_report(env: dict) -> str:
    lines = ["=== LiveReact diagnostics ===",
             f"livereact: {env['livereact_version']}",
             f"python:    {env['python']}",
             f"platform:  {env['platform']}",
             "",
             "dependencies:"]
    for name, ver in env["dependencies"].items():
        lines.append(f"  {name:16} {ver}")
    lines.append(f"  python-prodj-link {'installed' if env['prodj_link_installed'] else 'NOT installed (git-only)'}")
    lines.append(f"  prodj NFS client  {'importable' if env.get('prodj_nfs_importable') else 'NOT importable'}"
                 f"  (construct {env.get('construct_version', '?')})")
    lines.append("")
    lines.append("serial ports:")
    for p in env["serial_ports"]:
        if "error" in p:
            lines.append(f"  <error: {p['error']}>")
        else:
            lines.append(f"  {p['device']}  vid={p['vid']} pid={p['pid']}  {p['description']}")
    if not env["serial_ports"]:
        lines.append("  (none)")
    lines.append("")
    lines.append("MIDI inputs:")
    for name in env["midi_inputs"] or ["(none)"]:
        lines.append(f"  {name}")
    lines.append("")
    lines.append(env["network"])
    return "\n".join(lines)


def write_report(path: str) -> dict:
    """Write both <path> (text) and <path>.json. Returns the environment dict."""
    env = collect_environment()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(format_report(env))
    with open(path + ".json", "w", encoding="utf-8") as fh:
        json.dump(env, fh, indent=2)
    return env
