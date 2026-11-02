"""``--preflight``: a pass/fail check of the real rig before a set (2026-11-02, 2026-10-31 show post-mortem).

On 2026-10-31 the set went live with rekordbox holding the Pro DJ Link ports, which only showed up as
an ERROR line in the log. This runs every check the show depends on, on THIS machine, and says in
one screen what to fix before the doors open. Read-only except for briefly binding the DJ Link
ports and opening the audio stream / DMX port (so run it while LiveReact is NOT running).

Each check returns a ``Check``; ``run`` takes the probes as arguments so tests can fake the hardware.
"""
from __future__ import annotations

import errno
import socket
import subprocess
from dataclasses import dataclass

from .inputs.prodj import BEAT_PORT, KEEPALIVE_PORT, STATUS_PORT

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
PDL_PORTS = (KEEPALIVE_PORT, BEAT_PORT, STATUS_PORT)
AUDIO_SECONDS = 1.0
AUDIO_MIN_BLOCKS = 10          # a live stream delivers ~43 blocks/s; fewer means it is not really running
AUDIO_SILENT_RMS = 1e-4        # below this the XDJ is connected but nothing is playing (or the master is down)


@dataclass
class Check:
    name: str
    status: str
    detail: str


# ---------------------------------------------------------------- probes (real hardware)

def port_holder(port: int) -> str:
    """The process holding a UDP port, by name, best effort (macOS/Linux lsof)."""
    try:
        out = subprocess.run(["lsof", "-nP", f"-iUDP:{port}"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001 - lsof missing (Windows) or slow
        return ""
    rows = [ln.split() for ln in out.splitlines()[1:] if ln.strip()]
    return rows[0][0] if rows else ""


def try_bind_udp(port: int) -> OSError | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", port))
        return None
    except OSError as exc:
        return exc
    finally:
        s.close()


def app_running(ps_output: str | None = None, self_pid: int | None = None) -> bool:
    """Another LiveReact (``-m livereact``, not a --preflight) is running. macOS/Linux ``ps``; False where
    there is none (Windows). 2026-11-02 stress test: the first version used ``pgrep -m``, which macOS
    pgrep rejects, so it always answered "not running"."""
    import os
    if ps_output is None:
        try:
            ps_output = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                                       timeout=5).stdout
        except Exception:  # noqa: BLE001
            return False
    me = os.getpid() if self_pid is None else self_pid
    for line in ps_output.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) == me:
            continue
        if ("-m livereact" in cmd or "livereact/__main__" in cmd) and "--preflight" not in cmd:
            return True
    return False


def probe_audio(seconds: float = AUDIO_SECONDS) -> tuple[str, int, float]:
    """(device name, blocks received, rms) from a short capture on the XDJ, raises if none."""
    import time

    import numpy as np
    import sounddevice as sd

    from .inputs.audio import BLOCK, CHANNELS, AudioAnalyzer

    dev = AudioAnalyzer._find_device(sd)
    info = sd.query_devices(dev)
    got: list = []
    with sd.InputStream(device=dev, channels=max(CHANNELS), samplerate=int(info["default_samplerate"]),
                        blocksize=BLOCK, dtype="float32",
                        callback=lambda d, f, t, s: got.append(d[:, [c - 1 for c in CHANNELS]].copy())):
        time.sleep(seconds)
    rms = float(np.sqrt(np.mean(np.concatenate(got) ** 2))) if got else 0.0
    return str(info["name"]), len(got), rms


def probe_dmx() -> str:
    """Open and close the Enttec; returns its port, raises if absent or busy."""
    from .output.enttec import EnttecSerialBackend
    b = EnttecSerialBackend()
    b.open()
    port = b._port
    b.close()
    return str(port)


# ---------------------------------------------------------------- checks

def check_not_running(running: bool) -> Check:
    if running:
        return Check("LiveReact not running", FAIL,
                     "LiveReact is already running — it holds the DMX port and DJ Link ports, so these checks "
                     "would fail. Stop it, run --preflight, then launch the show.")
    return Check("LiveReact not running", PASS, "ok")


def check_pdl_ports(bind=try_bind_udp, holder=port_holder) -> Check:
    for port in PDL_PORTS:
        exc = bind(port)
        if exc is None:
            continue
        who = holder(port) if exc.errno == errno.EADDRINUSE else ""
        if who:
            hint = " Quit rekordbox on this Mac." if "rekordbox" in who.lower() else f" Quit {who}."
            return Check("DJ Link ports free", FAIL, f"UDP {port} is held by {who}.{hint}")
        return Check("DJ Link ports free", FAIL, f"cannot bind UDP {port}: {exc}")
    return Check("DJ Link ports free", PASS, f"UDP {', '.join(map(str, PDL_PORTS))} free")


def check_link_local(addresses, interface: str | None) -> Check:
    ll = [(name, ip) for name, ip in addresses if ip.startswith("169.254.")]
    if interface:
        mine = [ip for name, ip in ll if name == interface]
        if mine:
            return Check("DJ network (169.254)", PASS, f"{interface} = {mine[0]}")
        return Check("DJ network (169.254)", FAIL,
                     f"{interface} has no 169.254 address — check the XDJ's Ethernet cable / USB-LAN adapter"
                     + (f" (169.254 seen on {', '.join(n for n, _ in ll)})" if ll else ""))
    if ll:
        return Check("DJ network (169.254)", PASS, ", ".join(f"{n} = {ip}" for n, ip in ll))
    return Check("DJ network (169.254)", WARN, "no 169.254 address on any interface — the XDJ's Ethernet isn't up yet")


def check_audio(probe=probe_audio) -> Check:
    try:
        name, blocks, rms = probe()
    except Exception as exc:  # noqa: BLE001
        return Check("XDJ audio", FAIL, f"{exc}")
    if blocks < AUDIO_MIN_BLOCKS:
        return Check("XDJ audio", FAIL, f"{name}: only {blocks} audio blocks in {AUDIO_SECONDS:.0f} s — stream not flowing; replug the XDJ's USB")
    if rms < AUDIO_SILENT_RMS:
        return Check("XDJ audio", WARN, f"{name}: connected but silent — play something / check the master level")
    return Check("XDJ audio", PASS, f"{name}: live (level {rms:.3f})")


def check_midi(names: list[str]) -> Check:
    xdj = [n for n in names if "xdj" in n.lower()]
    if xdj:
        return Check("XDJ MIDI", PASS, xdj[0])
    return Check("XDJ MIDI", WARN, "no XDJ MIDI input (tempo still comes from the DJ Link / audio)")


def check_dmx(probe=probe_dmx) -> Check:
    try:
        port = probe()
    except Exception as exc:  # noqa: BLE001
        return Check("DMX adapter", FAIL, f"{exc} — check the Enttec's USB (and the hub's power)")
    return Check("DMX adapter", PASS, f"Enttec opened on {port}")


def check_rig(path: str | None) -> list[Check]:
    from .fixtures.rig import load_rig
    try:
        rig = load_rig(path)
    except Exception as exc:  # noqa: BLE001
        return [Check("Rig file", FAIL, f"{path}: {exc}")]
    out = [Check("Rig file", PASS, f"{rig.name}: {len(rig.fixtures)} fixtures, lasers = {rig.laser_profile}")]
    for fx in rig.fixtures:
        out.append(Check(f"  set unit: {fx.label}", "INFO",
                         f"DMX {fx.address:03d}-{fx.address + fx.channel_count - 1:03d} "
                         f"({fx.channel_count}-ch, {fx.type_name}) — confirm the unit's display reads {fx.address:03d}"))
    return out


def run(rig_path: str | None, interface: str | None, *, running=app_running, bind=try_bind_udp,
        holder=port_holder, addresses=None, audio=probe_audio, midi=None, dmx=probe_dmx) -> list[Check]:
    from . import network
    if addresses is None:
        addresses = network.list_ipv4_addresses()
    if midi is None:
        from .diagnostics import list_midi_inputs
        midi = list_midi_inputs()
    first = check_not_running(running() if callable(running) else running)
    checks = [first]
    if first.status == FAIL:
        return checks + check_rig(rig_path)
    checks += [check_pdl_ports(bind, holder), check_link_local(addresses, interface), check_audio(audio),
               check_midi(midi), check_dmx(dmx)]
    return checks + check_rig(rig_path)


def format_checks(checks: list[Check]) -> str:
    lines = ["LiveReact pre-show check", "=" * 60]
    for c in checks:
        lines.append(f"[{c.status:4}] {c.name}: {c.detail}" if c.status != "INFO" else f"       {c.name}: {c.detail}")
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    lines.append("=" * 60)
    lines.append("READY FOR THE SHOW" if not fails and not warns
                 else f"{fails} FAIL, {warns} WARN — fix the FAILs before going live")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == FAIL for c in checks) else 0
