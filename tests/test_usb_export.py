"""UsbExport over a synthetic export tree (no pdb parser needed) + the real pdb when present."""

from __future__ import annotations

import importlib.util
import json
import unicodedata
from pathlib import Path

import pytest

from anlz_builders import high_track
from livereact.rekordbox.library import RekordboxLibrary, TrackInfo
from livereact.rekordbox.usb_export import UsbExport, default_usb_root

PDB = Path(__file__).resolve().parents[1] / "databases" / "player-1-sd.pdb"
HAVE_PRODJ = importlib.util.find_spec("prodj") is not None


class FakeLibrary(RekordboxLibrary):
    def __init__(self, infos):
        super().__init__()
        self._tracks = {t.id: t for t in infos}


def _export(tmp_path: Path, names: list[tuple[int, str, str]], nfd_dirs=False):
    """names: (id, title, filename). Builds Contents/<Artist>/<Album>/<file> + USBANLZ files."""
    root = tmp_path / "usb"
    infos = []
    for tid, title, fname in names:
        artist = "Café Artist" if nfd_dirs else "Cafe Artist"          # NFD on disk when asked
        pdb_artist = unicodedata.normalize("NFC", artist)                    # pdb strings are NFC
        audio = root / "Contents" / artist / "Album" / fname
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"\x00" * (1000 + tid))
        anlz = root / "PIONEER" / "USBANLZ" / f"P{tid:03X}" / f"{tid:08X}"
        anlz.mkdir(parents=True, exist_ok=True)
        ext, dat = high_track()
        (anlz / "ANLZ0000.EXT").write_bytes(ext)
        (anlz / "ANLZ0000.DAT").write_bytes(dat)
        infos.append(TrackInfo(id=tid, title=title, artist=pdb_artist, bpm=125.0, duration_s=240,
                               analyze_path=f"/PIONEER/USBANLZ/P{tid:03X}/{tid:08X}/ANLZ0000.DAT",
                               path=f"/Contents/{pdb_artist}/Album/{fname}", filename=fname, file_size=1000 + tid))
    (root / "PIONEER" / "rekordbox").mkdir(parents=True, exist_ok=True)
    (root / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"fake")
    return UsbExport(root, library=FakeLibrary(infos))


def test_tracks_resolve_audio_and_analysis_including_nfd_directories(tmp_path):
    usb = _export(tmp_path, [(4, "Lift Me Up (Extended)", "Lift_Me_Up.mp3"), (7, "Other", "Other Song.mp3")], nfd_dirs=True)
    ts = usb.tracks()
    assert [t.info.id for t in ts] == [4, 7]
    assert all(t.complete for t in ts), usb.missing()
    assert ts[0].audio.name == "Lift_Me_Up.mp3" and ts[0].ext.name == "ANLZ0000.EXT"
    assert usb.missing() == {"audio": [], "dat": [], "ext": []}


def test_missing_files_are_listed_not_fatal(tmp_path):
    usb = _export(tmp_path, [(1, "A", "a.mp3"), (2, "B", "b.mp3")])
    usb.tracks()[1].audio.unlink()
    usb._tracks = None
    missing = usb.missing()
    assert [t.info.id for t in missing["audio"]] == [2] and not missing["ext"]


def test_load_analysis_and_timeline_from_grid(tmp_path):
    usb = _export(tmp_path, [(4, "Lift", "Lift.mp3")])
    t = usb.tracks()[0]
    table, grid, wave = usb.load_analysis(t)
    assert table is not None and len(table.phrases) == 7 and grid and wave is not None
    tl = usb.timeline(t, grid)
    assert tl.bpm == 125.0 and tl.source == "rekordbox" and tl.markers == []
    assert abs(tl.first_downbeat_s - 0.1) < 1e-6              # the first grid beat with beat_in_bar == 1
    assert tl.notes == ""


def test_find_by_name_title_and_size(tmp_path):
    usb = _export(tmp_path, [(4, "Lift Me Up", "Lift_Me_Up_KLICKAUD.mp3"), (5, "Zed", "zed.mp3")])
    other = tmp_path / "tracks"
    other.mkdir()
    (other / "Lift_Me_Up_KLICKAUD.mp3").write_bytes(b"x")
    assert usb.find(other / "Lift_Me_Up_KLICKAUD.mp3").info.id == 4            # by file name
    (other / "Josh - Lift Me Up (edit).mp3").write_bytes(b"x")
    assert usb.find(other / "Josh - Lift Me Up (edit).mp3").info.id == 4       # by title in stem
    (other / "mystery.mp3").write_bytes(b"\x00" * 1005)
    assert usb.find(other / "mystery.mp3").info.id == 5                        # by size
    (other / "nope.mp3").write_bytes(b"\x00" * 3)
    assert usb.find(other / "nope.mp3") is None


def test_ear_timeline_match_by_stem_nfc_and_title(tmp_path):
    usb = _export(tmp_path, [(4, "Lift Me Up (Extended)", "Lift_Me_Up.mp3"), (5, "Sweet Nothing", "sweet.mp3")])
    tdir = tmp_path / "tracks"
    tdir.mkdir()
    (tdir / "Lift_Me_Up.timeline.json").write_text(json.dumps({"bpm": 125.0, "markers": []}))
    (tdir / "Sweet_Nothing_2025_Edit.timeline.json").write_text(json.dumps({"bpm": 130.0, "markers": []}))
    ts = usb.tracks()
    assert usb.ear_timeline(ts[0], tdir, check_duration=False).name == "Lift_Me_Up.timeline.json"
    assert usb.ear_timeline(ts[1], tdir, check_duration=False).name == "Sweet_Nothing_2025_Edit.timeline.json"
    usb2 = _export(tmp_path / "b", [(9, "Unknown", "unknown.mp3")])
    assert usb2.ear_timeline(usb2.tracks()[0], tdir) is None


def test_analysis_fingerprint_depends_only_on_the_given_tracks(tmp_path):
    usb = _export(tmp_path, [(1, "A", "a.mp3"), (2, "B", "b.mp3"), (3, "C", "c.mp3")])
    ts = usb.tracks()
    fp = usb.analysis_fingerprint(ts[:2])
    assert fp == usb.analysis_fingerprint(ts[:2]) and len(fp) == 12
    assert fp != usb.analysis_fingerprint(ts[:3])
    ts[0].ext.write_bytes(b"PMAI" + b"\x00" * 40)         # re-analysed: fingerprint changes
    assert fp != usb.analysis_fingerprint(ts[:2])


def test_default_usb_root_env_and_none(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVEREACT_USB", raising=False)
    assert default_usb_root(tmp_path) is None
    env = tmp_path / "elsewhere"
    (env / "PIONEER" / "rekordbox").mkdir(parents=True)
    (env / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(b"x")
    monkeypatch.setenv("LIVEREACT_USB", str(env))
    assert default_usb_root(tmp_path) == env
    local = tmp_path / "usb_export" / "PIONEER" / "rekordbox"
    local.mkdir(parents=True)
    (local / "export.pdb").write_bytes(b"x")
    assert default_usb_root(tmp_path) == tmp_path / "usb_export"      # the repo copy wins


@pytest.mark.skipif(not (HAVE_PRODJ and PDB.exists()), reason="local XDJ export.pdb not present")
def test_real_pdb_paths_and_filenames():
    lib = RekordboxLibrary()
    lib.load_pdb_file(PDB)
    t = lib.track(4)
    assert t.path.startswith("/Contents/") and t.path.endswith(".mp3") and t.filename == Path(t.path).name
    assert t.file_size > 0
