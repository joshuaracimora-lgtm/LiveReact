"""Enttec DMX USB Pro output backend.

⚠️ UNTESTED ON HARDWARE — written from the Enttec Widget API spec (docs/04 §5.2)
and verified by unit tests for the parts that don't need a device (frame framing,
port selection). The serial open/write path must be confirmed on the real Enttec.

The DmxWriter already provides the never-stops, self-healing reopen loop around
this backend (docs/05 §5.1), so this class only has to open a port, frame each
universe, and write it.
"""

from __future__ import annotations

import logging

from ..core.frame_buffer import UNIVERSE_SIZE
from .base import OutputBackend

log = logging.getLogger(__name__)

FTDI_VID = 0x0403                      # Enttec uses an FTDI chip
FTDI_PIDS = {0x6001, 0x6010, 0x6014, 0x6015}
LABEL_OUTPUT_ONLY = 0x06               # "Output Only Send DMX Packet"


def build_enttec_frame(universe: bytes) -> bytes:
    """Frame a 512-byte universe for the Enttec (label 6). Returns 518 bytes.

    Wire format: 0x7E label len_LSB len_MSB [start_code=0x00 + 512 data] 0xE7
    (docs/04 §5.2).
    """
    if len(universe) != UNIVERSE_SIZE:
        raise ValueError(f"universe must be {UNIVERSE_SIZE} bytes, got {len(universe)}")
    payload = b"\x00" + universe       # DMX start code + channel data = 513 bytes
    n = len(payload)
    return bytes([0x7E, LABEL_OUTPUT_ONLY, n & 0xFF, (n >> 8) & 0xFF]) + payload + bytes([0xE7])


def select_enttec_port(ports) -> str | None:
    """Pick the Enttec serial device from ``serial.tools.list_ports`` entries.

    Matches by FTDI VID (not by name), and prefers the macOS ``cu.*`` node over
    ``tty.*`` (the tty node blocks on open — docs/04 §5.3). ``ports`` items need
    ``device``/``vid``/``pid`` attributes.
    """
    return enttec_port_choice(ports)[0]


def _says_enttec(p) -> bool:
    """True when a port identifies itself as an Enttec (serial "EN......" or product "DMX USB PRO")."""
    serial_no = str(getattr(p, "serial_number", None) or "").upper()
    text = " ".join(str(getattr(p, a, None) or "") for a in ("description", "product", "manufacturer")).lower()
    return serial_no.startswith("EN") or "dmx usb pro" in text or "enttec" in text


def _node_key(dev: str) -> str:
    """The same device seen as macOS tty.* and cu.* nodes has one key."""
    return dev.replace("/dev/tty.", "/dev/cu.")


def enttec_port_choice(ports) -> tuple[str | None, list[str]]:
    """The port to open, and the other FTDI devices it was guessed among (2026-11-19).

    Another FTDI device (a second DMX dongle, an Arduino clone, a programming cable) used to be taken
    when it came first, leaving the lights dark with no error. A port that says it is an Enttec now wins;
    with none saying so, the first FTDI device is used as before and the rivals are listed so the
    operator can be told. One FTDI device -> the same choice as always, and no rivals.
    """
    matches = [p for p in ports if getattr(p, "vid", None) == FTDI_VID]
    named = [p for p in matches if _says_enttec(p)]
    pool = named or matches
    if not pool:
        return None, []
    chosen = getattr(pool[0], "device", None)
    for p in pool:  # prefer cu.* on macOS
        dev = getattr(p, "device", "") or ""
        if "cu." in dev:
            chosen = dev
            break
    if named or not chosen:
        return chosen, []
    rivals: list[str] = []
    for p in matches:
        dev = getattr(p, "device", "") or ""
        if dev and _node_key(dev) != _node_key(chosen) and _node_key(dev) not in map(_node_key, rivals):
            rivals.append(dev)
    return chosen, rivals


class EnttecSerialBackend(OutputBackend):
    def __init__(self, port: str | None = None, baud: int = 57600) -> None:
        self._port_hint = port
        self._baud = baud
        self._serial = None
        self._port: str | None = None
        # other FTDI devices the auto-pick chose among, none saying it is an Enttec (health banner, preflight)
        self.ambiguous_with: list[str] = []

    @property
    def name(self) -> str:
        return f"enttec-serial({self._port or self._port_hint or 'auto'})"

    def open(self) -> None:
        import serial  # lazy: only needed in hardware mode
        from serial.tools import list_ports

        self.ambiguous_with = []
        if self._port_hint:
            self._port = self._port_hint
        else:
            self._port, self.ambiguous_with = enttec_port_choice(list_ports.comports())
        if not self._port:
            raise RuntimeError("No Enttec DMX USB Pro found (no FTDI device on the bus)")
        # timeout=0 -> non-blocking writes; the chip clocks DMX itself.
        self._serial = serial.Serial(self._port, baudrate=self._baud, timeout=0)
        log.info("Enttec opened on %s", self._port)

    def send(self, universe: bytes) -> None:
        if self._serial is None:
            raise RuntimeError("Enttec port not open")
        self._serial.write(build_enttec_frame(universe))

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
