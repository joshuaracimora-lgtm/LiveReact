"""Track index over a rekordbox ``export.pdb`` + a per-track phrase-table cache.

The pdb parser is the vendored ``prodj.pdblib`` (python-prodj-link, already in
the venv for the Pro DJ Link packet formats). ``PDBDatabase`` only loads from a
filename and its ``get_track`` is a linear scan, so this wraps it with a dict
index and a bytes -> cache-file path for NFS downloads.

Imports of ``prodj`` are deferred so this module (and the package) imports in
environments without the library; ``load_*`` raise ``RekordboxLibraryError``
with a clear message instead.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .pssi import PhraseTable

log = logging.getLogger(__name__)


class RekordboxLibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrackInfo:
    id: int
    title: str
    artist: str
    bpm: float            # rekordbox analyzed tempo (bpm_100 / 100)
    duration_s: int
    analyze_path: str     # e.g. /PIONEER/USBANLZ/P06C/000175F8/ANLZ0000.DAT
    path: str = ""        # the audio file on the stick, absolute from the USB root: /Contents/<artist>/<album>/<file>
    filename: str = ""    # its file name alone
    file_size: int = 0

    @property
    def ext_path(self) -> str:
        return ext_path_for(self.analyze_path)


def ext_path_for(analyze_path: str) -> str:
    """The ``.EXT`` sibling of a track's ``analyze_path`` (which names the ``.DAT``)."""
    if analyze_path.upper().endswith(".DAT"):
        return analyze_path[:-4] + ".EXT"
    return analyze_path


def _pdblib():
    try:
        from prodj.pdblib.pdbdatabase import PDBDatabase  # type: ignore
    except ImportError as exc:  # pragma: no cover - lib present in the venv
        raise RekordboxLibraryError(
            "python-prodj-link (prodj.pdblib) is not importable; it is needed to read export.pdb "
            "(see inputs/prodj.py's docstring for the install note)."
        ) from exc
    _fix_utf16_strings()
    return PDBDatabase


def _fix_utf16_strings() -> None:
    """Patch pdblib's DeviceSQL long-string decoder (kind 0x90) in place.

    Upstream (b08921e) reads the text right after the 2-byte length as UTF-16-BE;
    rekordbox writes a pad byte there and then UTF-16-LE. Every non-ASCII path
    came out shifted by one byte ('Łaszewo' -> 'Ašszewo', '’ ' -> '\\x19†'), so
    those tracks never matched their files. ASCII strings use other kinds and are
    unaffected. The Switch's cases dict is shared by every struct that embeds
    PioString, so mutating it fixes all of them; idempotent."""
    from construct import ExprAdapter, FocusedSeq, Int16ul, Padding, PaddedString, this  # type: ignore
    from prodj.pdblib import piostring  # type: ignore
    cases = piostring.PioString.subcons[1].subcon.cases
    if getattr(cases.get(0x90), "_livereact_fixed", False):
        return
    fixed = FocusedSeq("text",
        "actual_length" / ExprAdapter(Int16ul, lambda o, c: o - 4, lambda o, c: o + 4),
        Padding(1),
        "text" / PaddedString(this.actual_length, "utf-16-le"))
    fixed._livereact_fixed = True
    cases[0x90] = fixed


class RekordboxLibrary:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._tracks: dict[int, TrackInfo] = {}
        self._phrases: dict[int, PhraseTable | None] = {}
        self.source: str = ""          # where the current index came from (path/key)

    # ------------------------------------------------------------------ loading
    def load_pdb_file(self, path: str | Path) -> int:
        """Index an export.pdb from disk; returns the number of tracks."""
        PDBDatabase = _pdblib()
        p = Path(path)
        if not p.exists():
            raise RekordboxLibraryError(f"export.pdb not found: {p}")
        db = PDBDatabase()
        try:
            db.load_file(str(p))
        except Exception as exc:  # noqa: BLE001 - construct raises many types
            raise RekordboxLibraryError(f"could not parse {p}: {exc}") from exc

        artists: dict[int, str] = {}
        for a in db.get("artists", []):
            try:
                artists[int(a.id)] = str(a.name)
            except Exception:  # noqa: BLE001
                continue
        tracks: dict[int, TrackInfo] = {}
        for t in db.get("tracks", []):
            try:
                tid = int(t.id)
                tracks[tid] = TrackInfo(
                    id=tid,
                    title=str(t.title or ""),
                    artist=artists.get(int(t.artist_id), ""),
                    bpm=float(t.bpm_100) / 100.0,
                    duration_s=int(t.duration),
                    analyze_path=str(t.analyze_path or ""),
                    path=str(t.path or ""),
                    filename=str(t.filename or ""),
                    file_size=int(getattr(t, "file_size", 0) or 0),
                )
            except Exception as exc:  # noqa: BLE001 - one bad row must not sink the index
                log.debug("skipping unparsable track row: %s", exc)
        self._tracks = tracks
        self.source = str(p)
        log.info("rekordbox library: %d tracks from %s", len(tracks), p)
        return len(tracks)

    def load_pdb_bytes(self, data: bytes, key: str) -> int:
        """Index an export.pdb received as bytes (NFS). Written to the cache dir first."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key) or "export"
        target_dir = self.cache_dir or Path(tempfile.gettempdir()) / "livereact-rekordbox"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{safe}.pdb"
        tmp = path.with_suffix(".pdb.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return self.load_pdb_file(path)

    # ------------------------------------------------------------------ lookup
    def track(self, track_id: int) -> TrackInfo | None:
        return self._tracks.get(int(track_id))

    def tracks(self) -> list[TrackInfo]:
        return sorted(self._tracks.values(), key=lambda t: t.id)

    def __len__(self) -> int:
        return len(self._tracks)

    # ------------------------------------------------------------------ phrase cache
    def put_phrases(self, track_id: int, table: PhraseTable | None) -> None:
        self._phrases[int(track_id)] = table

    def phrases(self, track_id: int) -> PhraseTable | None:
        return self._phrases.get(int(track_id))

    def has_phrases(self, track_id: int) -> bool:
        return int(track_id) in self._phrases
