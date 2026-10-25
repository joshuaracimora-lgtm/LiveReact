"""RekordboxLibrary over the vendored pdb parser.

``databases/player-1-sd.pdb`` is a real export.pdb pulled from Josh's XDJ-XZ over
NFS (June 2026) but the directory is gitignored, so those checks skip when the
file (or the vendored ``prodj`` library) is absent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from livereact.rekordbox.library import RekordboxLibrary, RekordboxLibraryError, TrackInfo, ext_path_for
from livereact.rekordbox.pssi import PhraseTable

PDB = Path(__file__).resolve().parents[1] / "databases" / "player-1-sd.pdb"
HAVE_PRODJ = importlib.util.find_spec("prodj") is not None


def test_ext_path_for():
    assert ext_path_for("/PIONEER/USBANLZ/P06C/000175F8/ANLZ0000.DAT") == "/PIONEER/USBANLZ/P06C/000175F8/ANLZ0000.EXT"
    assert ext_path_for("/x/ANLZ0000.EXT") == "/x/ANLZ0000.EXT"
    assert TrackInfo(1, "t", "a", 120.0, 100, "/a/ANLZ0000.DAT").ext_path.endswith(".EXT")


def test_phrase_cache_roundtrip():
    lib = RekordboxLibrary()
    assert not lib.has_phrases(7)
    lib.put_phrases(7, None)
    assert lib.has_phrases(7) and lib.phrases(7) is None
    table = PhraseTable(mood=1, end_beat=10, bank=0, phrases=())
    lib.put_phrases(8, table)
    assert lib.phrases(8) is table
    assert lib.track(1) is None and len(lib) == 0


@pytest.mark.skipif(not HAVE_PRODJ, reason="vendored prodj library not importable")
def test_missing_pdb_raises_clear_error(tmp_path):
    with pytest.raises(RekordboxLibraryError):
        RekordboxLibrary().load_pdb_file(tmp_path / "nope.pdb")


@pytest.mark.skipif(not (HAVE_PRODJ and PDB.exists()), reason="local XDJ export.pdb not present")
def test_real_export_pdb_indexes_tracks(tmp_path):
    lib = RekordboxLibrary(cache_dir=tmp_path)
    n = lib.load_pdb_file(PDB)
    assert n == 149 and len(lib) == 149
    t = lib.track(4)
    assert t is not None and t.title == "Lift Me Up (Extended)"
    assert t.analyze_path == "/PIONEER/USBANLZ/P06C/000175F8/ANLZ0000.DAT"
    assert t.ext_path == "/PIONEER/USBANLZ/P06C/000175F8/ANLZ0000.EXT"
    assert t.duration_s == 379 and t.bpm > 60
    assert lib.track(999999) is None
    # bytes path: same content via the cache dir
    lib2 = RekordboxLibrary(cache_dir=tmp_path)
    assert lib2.load_pdb_bytes(PDB.read_bytes(), "169.254.1.2-sd") == 149
    assert (tmp_path / "169.254.1.2-sd.pdb").exists()
