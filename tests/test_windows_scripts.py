"""tools/windows/*.ps1 must be pure ASCII.

Windows PowerShell 5.1 reads a BOM-less script as Windows-1252, so a UTF-8 em dash
inside a string literal becomes a stray quote and the whole script fails to parse
(nightly.ps1 line 53, found on the desktop 2026-10-23: "The string is missing the
terminator"). ASCII-only is simpler than relying on a BOM surviving every editor.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_powershell_scripts_are_ascii():
    bad = {}
    for p in sorted((ROOT / "tools" / "windows").glob("*.ps1")):
        data = p.read_bytes()
        for i, line in enumerate(data.splitlines(), 1):
            if any(b > 0x7F for b in line):
                bad.setdefault(p.name, []).append(i)
    assert not bad, f"non-ASCII bytes in PowerShell scripts (PS 5.1 misparses them): {bad}"
