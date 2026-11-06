"""USB-MIDI input from the XDJ-XZ via mido + python-rtmidi.

⚠️ UNTESTED ON HARDWARE. Read-only: LiveReact never sends MIDI back to the XDJ.
Each control-change is mapped through a CcMap to a semantic control and pushed to
the input queue as a MidiControlMessage. mido is lazy-imported so the rest of the
app doesn't need it.
"""

from __future__ import annotations

import logging
import queue

from ..clock import Clock
from ..core.messages import MidiControlMessage
from .base import InputSource
from .midi_map import CcMap

log = logging.getLogger(__name__)


def find_midi_port(port_names: list[str], match: str = "XDJ") -> str | None:
    """Pick the input port whose name contains ``match`` (case-insensitive).

    VERIFIED ON HARDWARE: the XDJ-XZ exposes *two* USB-MIDI ports — only one
    (named "... (2)" on macOS) actually carries mixer/deck control-change data;
    the other ("... (1)") stayed silent through repeated isolated capture. Relying
    on OS enumeration order to land on the right one was luck, not guaranteed
    stable across reconnects — so when both match, prefer "(2)" explicitly.
    """
    match = match.lower()
    matches = [name for name in port_names if match in name.lower()]
    if not matches:
        return port_names[0] if port_names else None
    preferred = [name for name in matches if "(2)" in name]
    return preferred[0] if preferred else matches[0]


class MidiListener(InputSource):
    def __init__(
        self,
        output_queue: "queue.Queue",
        clock: Clock,
        cc_map: CcMap | None = None,
        port_match: str = "XDJ",
    ) -> None:
        self.queue = output_queue
        self.clock = clock
        self.cc_map = cc_map or CcMap()
        self.port_match = port_match
        self._port = None
        self._unmapped: set[tuple[int, int]] = set()

    @property
    def name(self) -> str:
        return "midi"

    def start(self) -> None:
        import mido  # lazy: only in hardware mode

        names = mido.get_input_names()
        chosen = find_midi_port(names, self.port_match)
        if not chosen:
            raise RuntimeError("No MIDI input ports found")
        # callback keeps us off a polling loop; mido manages the backend thread.
        self._port = mido.open_input(chosen, callback=self._on_message)
        log.info("MIDI listening on %r (read-only)", chosen)

    def _on_message(self, msg) -> None:
        if msg.type != "control_change":
            return
        control = self.cc_map.resolve(msg.channel, msg.control)
        if control is None:
            key = (msg.channel, msg.control)
            if key not in self._unmapped:
                self._unmapped.add(key)
                log.debug("Unmapped MIDI CC ch=%d cc=%d (use --midi-monitor)", *key)
            return
        self.queue.put(MidiControlMessage(self.clock.monotonic(), control, msg.value / 127.0))

    def stop(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            finally:
                self._port = None


def monitor(port_match: str = "XDJ", duration: float | None = None, out_path: str | None = None) -> None:
    """Print (and optionally record) incoming MIDI to remap the XDJ empirically.

    Move each control on the XDJ and note the channel/CC it reports, then put those
    into midi_map.json (or update DEFAULT_CC_MAP). Blocks until Ctrl-C / duration.
    """
    import time

    import mido

    out_fh = open(out_path, "w", encoding="utf-8") if out_path else None

    def emit(line: str) -> None:
        print(line)
        if out_fh is not None:
            out_fh.write(line + "\n")
            out_fh.flush()

    try:
        names = mido.get_input_names()
        emit(f"MIDI input ports: {names or '(none)'}")
        chosen = find_midi_port(names, port_match)
        if not chosen:
            emit("No MIDI input found. Connect the XDJ-XZ and enable MIDI in Utility -> MIDI.")
            return
        emit(f"Monitoring {chosen!r}. Move controls on the XDJ; Ctrl-C to stop.\n")
        deadline = None if duration is None else time.monotonic() + duration
        with mido.open_input(chosen) as port:
            try:
                while deadline is None or time.monotonic() < deadline:
                    for msg in port.iter_pending():
                        if msg.type == "control_change":
                            emit(f"  control_change  channel={msg.channel:<2} cc={msg.control:<3} value={msg.value}")
                        else:
                            emit(f"  {msg.type}  {msg}")
                    time.sleep(0.01)
            except KeyboardInterrupt:
                emit("\nStopped.")
    finally:
        if out_fh is not None:
            out_fh.close()
