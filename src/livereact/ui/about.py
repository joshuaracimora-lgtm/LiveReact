"""About LiveReact (roadmap item 33): the version and third-party notices.

Licences are read from the installed packages' metadata where they say so, with the known
licence as the fallback. Open Fixture Library attribution is listed per imported fixture.
The prodj-link and OFL entries stay marked "to confirm" until Josh checks them (items 5, 37).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

from PySide6 import QtWidgets


@dataclass(frozen=True)
class Notice:
    name: str
    licence: str
    note: str = ""


# (display name, distribution, fallback licence, note)
_THIRD_PARTY = (
    ("Qt / PySide6", "PySide6", "LGPL-3.0", "Qt for Python, used under the LGPL; Qt libraries are shipped as "
                                            "separate, replaceable files."),
    ("Anthropic Python SDK", "anthropic", "MIT", "Used by the manual reader (your own API key)."),
    ("platformdirs", "platformdirs", "MIT", ""),
    ("pypdf", "pypdf", "BSD-3-Clause", ""),
    ("keyring", "keyring", "MIT", "Keeps the API key in the OS keychain."),
)
PRODJ_NOTICE = Notice("python-prodj-link", "to confirm (roadmap item 5)", "Pro DJ Link protocol support.")
OFL_NOTICE = Notice("Open Fixture Library", "to confirm (roadmap item 37)",
                    "Fixture data fetched per user from open-fixture-library.org; contributors credited per fixture.")


def app_version() -> str:
    try:
        return metadata.version("livereact")
    except metadata.PackageNotFoundError:
        return "development build"


def _licence(dist: str, fallback: str) -> str:
    try:
        md = metadata.metadata(dist)
    except metadata.PackageNotFoundError:
        return fallback
    expr = md.get("License-Expression")
    if expr:
        return expr
    lic = md.get("License")
    if lic and len(lic) < 40 and "\n" not in lic:
        return lic
    return fallback


def notices() -> list[Notice]:
    out = [Notice(name, _licence(dist, fb), note) for name, dist, fb, note in _THIRD_PARTY]
    return out + [PRODJ_NOTICE, OFL_NOTICE]


def ofl_attributions() -> list[str]:
    """One line per Open Fixture Library definition in the user's library."""
    try:
        from ..fixtures.library import list_definitions
        return sorted({e.definition.source.attribution for e in list_definitions()
                       if e.definition.source.kind == "ofl" and e.definition.source.attribution})
    except Exception:  # noqa: BLE001 - About must open even with a broken user folder
        return []


def about_text() -> str:
    lines = [f"LiveReact {app_version()}", "", "Third-party software and data:"]
    for n in notices():
        lines.append(f"• {n.name} — {n.licence}" + (f". {n.note}" if n.note else ""))
    credits = ofl_attributions()
    if credits:
        lines += ["", "Fixture definitions from Open Fixture Library:"] + [f"• {c}" for c in credits]
    return "\n".join(lines)


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About LiveReact")
        v = QtWidgets.QVBoxLayout(self)
        self.text = QtWidgets.QPlainTextEdit(about_text())
        self.text.setReadOnly(True)
        v.addWidget(self.text, 1)
        b = QtWidgets.QPushButton("Close")
        b.clicked.connect(self.accept)
        v.addWidget(b)
        self.resize(560, 420)
