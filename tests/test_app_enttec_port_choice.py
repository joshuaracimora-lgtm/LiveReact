"""Which USB-serial device is the Enttec (2026-11-19, show-night resilience).

The Enttec DMX USB Pro is an FTDI chip. `select_enttec_port` matched any FTDI device and took the first,
so a second FTDI device on the laptop (another DMX dongle, an Arduino clone, a programming cable) could
be opened instead of the Enttec: the writes "succeed", the lights stay dark, and nothing says why.
An Enttec identifies itself (serial number "EN......", product "DMX USB PRO"); that one now wins. When
several FTDI devices are present and none identifies itself, the old choice stands, and the operator is
told -- in --preflight and on the console's health banner -- which port was used and what else is there.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace

from livereact import preflight as pf
from livereact.output import enttec
from livereact.output.enttec import FTDI_VID, EnttecSerialBackend, select_enttec_port
from livereact.ui.health import health_warnings


@dataclass
class _Port:
    device: str
    vid: int | None = FTDI_VID
    pid: int | None = 0x6001
    serial_number: str | None = None
    description: str = "USB Serial Port"
    product: str | None = None
    manufacturer: str | None = "FTDI"


ENTTEC_WIN = _Port("COM7", serial_number="EN489520", description="DMX USB PRO", product="DMX USB PRO")
ARDUINO_WIN = _Port("COM3", serial_number="A9XK2LQ1", description="USB Serial Port")


def test_the_enttec_wins_over_another_ftdi_device_listed_first():
    assert select_enttec_port([ARDUINO_WIN, ENTTEC_WIN]) == "COM7"


def test_the_enttec_is_known_by_its_serial_number_alone():
    plain = _Port("COM9", serial_number="EN111111")
    assert select_enttec_port([ARDUINO_WIN, plain]) == "COM9"


def test_on_macos_the_enttec_cu_node_wins_over_another_ftdi_cu_node():
    ports = [
        _Port("/dev/cu.usbserial-A9XK2LQ1", serial_number="A9XK2LQ1"),
        _Port("/dev/tty.usbserial-EN489520", serial_number="EN489520", product="DMX USB PRO"),
        _Port("/dev/cu.usbserial-EN489520", serial_number="EN489520", product="DMX USB PRO"),
    ]
    assert select_enttec_port(ports) == "/dev/cu.usbserial-EN489520"


def test_a_lone_unnamed_ftdi_device_is_still_used():
    # a rig whose Enttec reports nothing about itself must behave exactly as before
    assert select_enttec_port([_Port("COM4", serial_number=None, description="USB Serial Port")]) == "COM4"


def test_two_unnamed_ftdi_devices_keep_the_old_choice_and_name_the_other():
    a, b = _Port("COM3"), _Port("COM4")
    assert select_enttec_port([a, b]) == "COM3"
    assert enttec.enttec_port_choice([a, b]) == ("COM3", ["COM4"])
    assert enttec.enttec_port_choice([ARDUINO_WIN, ENTTEC_WIN]) == ("COM7", [])
    assert enttec.enttec_port_choice([a]) == ("COM3", [])
    # the tty twin of the chosen cu node is the same device, not a rival
    mac = [_Port("/dev/tty.usbserial-A1"), _Port("/dev/cu.usbserial-A1")]
    assert enttec.enttec_port_choice(mac) == ("/dev/cu.usbserial-A1", [])


def _fake_serial(monkeypatch, ports):
    opened = []
    serial_mod = types.ModuleType("serial")
    serial_mod.Serial = lambda port, **kw: opened.append(port) or SimpleNamespace(close=lambda: None)
    tools = types.ModuleType("serial.tools")
    lp = types.ModuleType("serial.tools.list_ports")
    lp.comports = lambda: list(ports)
    tools.list_ports = lp
    serial_mod.tools = tools
    monkeypatch.setitem(sys.modules, "serial", serial_mod)
    monkeypatch.setitem(sys.modules, "serial.tools", tools)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", lp)
    return opened


def test_the_backend_opens_the_enttec_and_remembers_a_guess(monkeypatch):
    opened = _fake_serial(monkeypatch, [ARDUINO_WIN, ENTTEC_WIN])
    b = EnttecSerialBackend()
    b.open()
    assert opened == ["COM7"] and b.ambiguous_with == []

    opened = _fake_serial(monkeypatch, [_Port("COM3"), _Port("COM4")])
    b = EnttecSerialBackend()
    b.open()
    assert opened == ["COM3"] and b.ambiguous_with == ["COM4"]

    # a port the user named is never second-guessed
    b = EnttecSerialBackend(port="COM4")
    b.open()
    assert b.ambiguous_with == []


def _hw_app(backend):
    return SimpleNamespace(config=SimpleNamespace(demo=False),
                           writer=SimpleNamespace(is_open=True, backend=backend),
                           failed_inputs={}, started_at=0.0, sources=[])


def test_the_banner_says_when_the_enttec_was_a_guess():
    snap = SimpleNamespace(pdl_fresh=True, audio_fresh=True)
    guessed = SimpleNamespace(name="enttec-serial(COM3)", ambiguous_with=["COM4"])
    w = health_warnings(_hw_app(guessed), snap, 60.0)
    assert len(w) == 1 and "COM3" in w[0] and "COM4" in w[0] and "unplug" in w[0].lower()
    sure = SimpleNamespace(name="enttec-serial(COM7)", ambiguous_with=[])
    assert health_warnings(_hw_app(sure), snap, 60.0) == []


def test_preflight_warns_when_the_enttec_was_a_guess():
    c = pf.check_dmx(lambda: ("COM3", ["COM4"]))
    assert c.status == pf.WARN and "COM3" in c.detail and "COM4" in c.detail
    assert pf.check_dmx(lambda: ("COM7", [])).status == pf.PASS
    assert pf.check_dmx(lambda: "COM7").status == pf.PASS    # a probe that returns only the port
