"""Session setup shared by every test module.

The UI tests run Qt with the offscreen platform (no window, works in a scheduled task). That platform
ships no fonts: every glyph is a placeholder box, so label sizes -- and any test about label layout --
would measure nothing real. Point it at the system fonts before any module imports PySide6.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_fonts = Path(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
if sys.platform == "win32" and _fonts.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(_fonts))

# LIVEREACT_OFFLINE=1 (tools/agent/gate_app.py sets it): the App agent's tests may not reach the internet or
# a real Anthropic key. Network code must take an injectable transport and be tested with recorded data, so
# a test that dials out fails here instead of passing on the desktop's connection (or billing the key).
if os.environ.get("LIVEREACT_OFFLINE") == "1":
    import ipaddress
    import socket

    os.environ.pop("ANTHROPIC_API_KEY", None)
    _connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _loopback_only(address) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if host is None:                        # AF_UNIX path
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            if host == "localhost":
                return
        raise OSError(f"LIVEREACT_OFFLINE: test tried to connect to {address!r}")

    def _guarded_connect(self, address):
        _loopback_only(address)
        return _connect(self, address)

    def _guarded_connect_ex(self, address):
        _loopback_only(address)
        return _connect_ex(self, address)

    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
