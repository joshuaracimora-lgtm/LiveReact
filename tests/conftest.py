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
