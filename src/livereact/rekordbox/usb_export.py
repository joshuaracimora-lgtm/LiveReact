"""A copy of a rekordbox USB export as the harness / nightly-agent corpus.

Josh copies his stick into ``<repo>/usb_export/`` (gitignored) on both machines:

    usb_export/PIONEER/rekordbox/export.pdb        the track database (ids the deck reports live)
    usb_export/PIONEER/USBANLZ/<..>/ANLZ0000.DAT    beat grid (PQTZ), cues, ...
    usb_export/PIONEER/USBANLZ/<..>/ANLZ0000.EXT    phrase table (PSSI), colour waveform (PWV5), ...
    usb_export/Contents/<artist>/<album>/<file>    the audio itself

So every track on the stick can run through ``sim/harness.run_sim`` in drive mode with
its own plan, not only the ten Josh timed by hand. Two sets live side by side:
the SCORED set (tracks with an ear-verified ``tracks/*.timeline.json``) and the
SWEEP set (everything else: crash / invariant / marker-free rules only). A USB
track's own ``Timeline`` carries NO markers on purpose -- the scorer treats any
marker without an ``est:`` note as Josh's truth.

Name handling: pdb strings are usually NFC, Mac file names NFD, FAT32/Windows
whatever was written; ``resolve`` tries both forms and then scans the directory.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..sim.timeline import Timeline, match_timeline_name
from .library import RekordboxLibrary, RekordboxLibraryError, TrackInfo
from .pssi import PhraseTable, parse_pqtz, parse_pssi
from .waveform import ColorWaveform, parse_pwv5

log = logging.getLogger(__name__)

USB_PDB = Path("PIONEER") / "rekordbox" / "export.pdb"
ENV_VAR = "LIVEREACT_USB"
DEFAULT_DIRNAME = "usb_export"
DURATION_TOLERANCE_S = 2.0


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _slug(s: str) -> str:
    """Lower-case alphanumerics only: 'Sweet Nothing' == 'Sweet_Nothing_2025_Edit'[:12] territory."""
    return "".join(ch for ch in _nfc(s).lower() if ch.isalnum())


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_usb_root(repo: Path | None = None) -> Path | None:
    """``<repo>/usb_export`` when it holds an export.pdb, else ``$LIVEREACT_USB``, else None."""
    cand = (repo or repo_root()) / DEFAULT_DIRNAME
    if (cand / USB_PDB).exists():
        return cand
    env = os.environ.get(ENV_VAR)
    if env and (Path(env) / USB_PDB).exists():
        return Path(env)
    return None


@dataclass(frozen=True)
class UsbTrack:
    info: TrackInfo
    audio: Path | None     # None when the file is not on disk
    dat: Path | None
    ext: Path | None

    @property
    def name(self) -> str:
        return _nfc(self.info.filename or Path(self.info.path).name)

    @property
    def stem(self) -> str:
        return Path(self.name).stem

    @property
    def complete(self) -> bool:
        return self.audio is not None and self.dat is not None and self.ext is not None


class UsbExport:
    def __init__(self, root: str | Path, library: RekordboxLibrary | None = None) -> None:
        self.root = Path(root)
        self.pdb_path = self.root / USB_PDB
        self._library = library
        self._tracks: list[UsbTrack] | None = None

    # ------------------------------------------------------------------ database
    @property
    def library(self) -> RekordboxLibrary:
        if self._library is None:
            lib = RekordboxLibrary()
            if not self.pdb_path.exists():
                raise RekordboxLibraryError(f"no rekordbox export at {self.root} (missing {USB_PDB})")
            lib.load_pdb_file(self.pdb_path)
            self._library = lib
        return self._library

    def tracks(self) -> list[UsbTrack]:
        if self._tracks is None:
            out = []
            for info in self.library.tracks():
                audio = self.resolve(info.path) if info.path else None
                dat = self.resolve(info.analyze_path) if info.analyze_path else None
                ext = self.resolve(_ext_path(info.analyze_path)) if info.analyze_path else None
                out.append(UsbTrack(info=info, audio=audio, dat=dat, ext=ext))
            self._tracks = out
        return self._tracks

    def missing(self) -> dict[str, list[UsbTrack]]:
        ts = self.tracks()
        return {"audio": [t for t in ts if t.audio is None], "dat": [t for t in ts if t.dat is None],
                "ext": [t for t in ts if t.ext is None]}

    # ------------------------------------------------------------------ paths
    def resolve(self, pdb_path: str) -> Path | None:
        """``/Contents/A/B/f.mp3`` -> an existing file under the root, or None."""
        if not pdb_path:
            return None
        rel = pdb_path.lstrip("/\\").replace("\\", "/")
        for variant in (rel, _nfc(rel), unicodedata.normalize("NFD", rel)):
            p = self.root / variant
            if p.exists():
                return p
        # per-directory scan comparing NFC (case-insensitively as a last resort: FAT32)
        cur = self.root
        for part in rel.split("/"):
            if not cur.is_dir():
                return None
            want = _nfc(part)
            hit = None
            try:
                entries = list(cur.iterdir())
            except OSError:
                return None
            for e in entries:
                if _nfc(e.name) == want:
                    hit = e
                    break
            if hit is None:
                lowered = [e for e in entries if _nfc(e.name).lower() == want.lower()]
                hit = lowered[0] if len(lowered) == 1 else None
            if hit is None:
                return None
            cur = hit
        return cur if cur.exists() else None

    def find(self, audio: str | Path) -> UsbTrack | None:
        """The USB track for an audio file elsewhere (e.g. ``tracks/x.mp3``): by file name (NFC),
        then by title contained in the stem (unique), then by file size."""
        p = Path(audio)
        want = _nfc(p.name)
        by_name = [t for t in self.tracks() if t.name == want]
        if len(by_name) == 1:
            return by_name[0]
        stem = _slug(p.stem)
        by_title = [t for t in self.tracks() if t.info.title and _slug(t.info.title) and _slug(t.info.title) in stem]
        if len(by_title) == 1:
            return by_title[0]
        try:
            size = p.stat().st_size
        except OSError:
            return None
        by_size = [t for t in self.tracks() if t.info.file_size and t.info.file_size == size]
        return by_size[0] if len(by_size) == 1 else None

    # ------------------------------------------------------------------ analysis
    def load_analysis(self, t: UsbTrack) -> tuple[PhraseTable | None, list | None, ColorWaveform | None]:
        """(phrase table, beat grid, colour waveform) -- each None when missing or unparsable; never raises."""
        table = grid = wave = None
        if t.ext is not None:
            try:
                ext = t.ext.read_bytes()
                table = parse_pssi(ext)
                wave = parse_pwv5(ext)
            except Exception as exc:  # noqa: BLE001
                log.warning("usb: unreadable %s: %s", t.ext, exc)
        if t.dat is not None:
            try:
                grid = parse_pqtz(t.dat.read_bytes())
            except Exception as exc:  # noqa: BLE001
                log.warning("usb: unreadable %s: %s", t.dat, exc)
        return table, grid, wave

    def timeline(self, t: UsbTrack, grid: list | None = None) -> Timeline:
        """A marker-less timeline on rekordbox's grid: bpm + the first downbeat. No markers by design."""
        if grid is None:
            _, grid, _ = self.load_analysis(t)
        bpm = float(t.info.bpm) if t.info.bpm > 0 else (float(grid[0][1]) if grid else 120.0)
        first_down = 0.0
        if grid:
            for beat_in_bar, _bpm, time_ms in grid:
                if beat_in_bar == 1:
                    first_down = time_ms / 1000.0
                    break
        return Timeline(bpm=bpm, first_downbeat_s=first_down, markers=[], source="rekordbox", notes="")

    # ------------------------------------------------------------------ Josh's labels
    def ear_timeline(self, t: UsbTrack, tracks_dir: str | Path = "tracks", check_duration: bool = True) -> Path | None:
        """``tracks/<stem>.timeline.json`` for this USB track (NFC-tolerant), else a unique title match.
        When the ``tracks/`` audio exists and ffprobe is available, the durations must agree within
        DURATION_TOLERANCE_S or the match is rejected (a different edit of the same name)."""
        tdir = Path(tracks_dir)
        if not tdir.is_dir():
            return None
        names = [q.name for q in tdir.iterdir() if q.name.endswith(".timeline.json")]
        hit = match_timeline_name(f"{t.stem}.timeline.json", names)
        if hit is None and t.info.title:
            title = _slug(t.info.title)
            cands = [n for n in names if title and (title in _slug(n[:-len(".timeline.json")])
                                                    or _slug(n[:-len(".timeline.json")]) in title)]
            hit = cands[0] if len(cands) == 1 else None
        if hit is None:
            return None
        path = tdir / hit
        if check_duration and t.info.duration_s:
            audio = _sibling_audio(path)
            if audio is not None:
                dur = audio_duration_s(audio)
                if dur is not None and abs(dur - t.info.duration_s) > DURATION_TOLERANCE_S:
                    log.warning("usb: %s matches %s by name but durations differ (%.1fs vs pdb %ds); not paired",
                                t.name, hit, dur, t.info.duration_s)
                    return None
        return path

    def analysis_fingerprint(self, tracks: list[UsbTrack]) -> str:
        """Identifies the analysis of THESE tracks (id, name, duration, DAT+EXT bytes) -- adding other
        tracks to the stick does not change it; re-analysing one of these does."""
        h = hashlib.sha256()
        for t in sorted(tracks, key=lambda x: (x.info.id, x.name)):
            h.update(f"{t.info.id}|{t.name}|{t.info.duration_s}|".encode("utf-8"))
            for p in (t.dat, t.ext):
                h.update(hashlib.sha256(p.read_bytes()).digest() if p is not None and p.exists() else b"-")
            h.update(b"\n")
        return h.hexdigest()[:12]


def _ext_path(analyze_path: str) -> str:
    return analyze_path[:-4] + ".EXT" if analyze_path.upper().endswith(".DAT") else analyze_path


def _sibling_audio(timeline_path: Path) -> Path | None:
    stem = timeline_path.name[: -len(".timeline.json")]
    for q in timeline_path.parent.iterdir():
        if q.stem == stem and q.suffix.lower() in {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg"}:
            return q
    return None


def audio_duration_s(path: Path) -> float | None:
    """Seconds, via ffprobe (ships with ffmpeg); None when unavailable."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:  # noqa: BLE001
        return None
