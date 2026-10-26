"""PhraseService: turn "deck N loaded rekordbox track T from slot S" into a phrase table.

One worker thread. The Pro DJ Link listener calls ``request()`` on every
(ip, slot, track_id) change it sees in CDJ status packets; the worker fetches
``export.pdb`` once per (ip, slot) (then keeps it on disk), looks the track up,
pulls its ``ANLZ0000.EXT`` over NFS, parses the PSSI tag and posts a
``TrackPhraseMessage`` on the metronome's queue -- ``table=None`` with an
``error`` when anything is missing, so the metronome always hears back exactly
once per request and never blocks on the network.

Gig-safety: nothing here runs on the metronome thread; every failure is one
warning + one None message; a dead NFS client just means "no tables tonight",
which is today's behaviour.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from ..core.messages import TrackPhraseMessage
from .fetch import XdjNfs, slot_name
from .library import RekordboxLibrary, RekordboxLibraryError
from .planner import plan_track
from .pssi import parse_pqtz, parse_pssi
from .waveform import parse_pwv5

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "cache/rekordbox"


class PhraseService:
    def __init__(self, output_queue, clock, library: RekordboxLibrary | None = None,
                 nfs: XdjNfs | None = None, stop_event: threading.Event | None = None, *,
                 cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
                 scene_pool: dict[str, list[str]] | None = None,
                 raw_sink=None) -> None:
        self.queue = output_queue
        self.clock = clock
        self.cache_dir = Path(cache_dir) if cache_dir else None
        # One library per (ip, slot): rekordbox ids are per export, so two sticks in the XZ's
        # two slots can both have a "track 4". `library` (an injected default) serves the first slot.
        self._default_library = library if library is not None else RekordboxLibrary(cache_dir=self.cache_dir)
        self._libraries: dict[tuple[str, str], RekordboxLibrary] = {}
        self.raw_sink = raw_sink   # optional callable(kind, ip, slot, track_id, bytes) -- the probe's --dump
        self.nfs = nfs
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._requests: queue.Queue = queue.Queue()
        self._requested: set[tuple[str, str, int]] = set()
        self._pdb_loaded: dict[tuple[str, str], bool] = {}   # (ip, slot) -> loaded ok?
        self.scene_pool = scene_pool          # bucket -> scene names, from the app's catalog (planner)
        self._plans: dict[tuple[str, int], object] = {}   # (slot, track_id) -> TrackPlan
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "fetched": 0, "no_table": 0, "errors": 0}

    @property
    def library(self) -> RekordboxLibrary:
        """The most recently loaded slot's library (or the default before any fetch)."""
        return next(reversed(self._libraries.values())) if self._libraries else self._default_library

    def _lib(self, ip: str, slot: str) -> RekordboxLibrary:
        key = (ip, slot)
        if key not in self._libraries:
            # the first slot takes the (possibly injected) default library; later slots get their own
            self._libraries[key] = self._default_library if not self._libraries else RekordboxLibrary(cache_dir=self.cache_dir)
        return self._libraries[key]

    # ------------------------------------------------------------------ lifecycle (InputSource contract)
    @property
    def name(self) -> str:
        return "rekordbox-phrases"

    def start(self) -> None:
        if self._thread is not None:
            return
        if self.nfs is None:
            self.nfs = XdjNfs()
        try:
            self.nfs.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("rekordbox phrase service: NFS client unavailable (%s); phrase tables off", exc)
            self.nfs = None
        self._thread = threading.Thread(target=self._run, name="rekordbox-phrases", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._requests.put(None)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self.nfs is not None:
            self.nfs.stop()

    # ------------------------------------------------------------------ API (any thread)
    def request(self, ip: str, slot: str, player: int, track_id: int) -> None:
        """Ask for the phrase table of (ip, slot, track_id); duplicates are ignored."""
        if not track_id:
            return
        key = (ip, str(slot), int(track_id))
        with self._lock:
            if key in self._requested:
                # already answered (or in flight): re-emit the cached answer so a deck that
                # re-loads a known track still gets its table attached to the right player
                lib = self._libraries.get((ip, str(slot)))
                if lib is not None and lib.has_phrases(track_id):
                    table = lib.phrases(track_id)
                    self._emit(player, track_id, table, slot=str(slot), lib=lib,
                               error="" if table is not None else "no phrase table",
                               plan=self._plans.get((str(slot), int(track_id))))
                return
            self._requested.add(key)
        self.stats["requests"] += 1
        self._requests.put((ip, str(slot), int(player), int(track_id)))

    def prefetch_pdb(self, ip: str, slot: str) -> None:
        """Warm the export.pdb for a slot before any track is asked about."""
        self._requests.put((ip, str(slot), 0, 0))

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                item = self._requests.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            ip, slot, player, track_id = item
            try:
                self._handle(ip, slot, player, track_id)
            except Exception as exc:  # noqa: BLE001 - the worker must survive anything
                self.stats["errors"] += 1
                log.exception("phrase service failed for %s:%s track %d", ip, slot, track_id)
                if track_id:
                    self._emit(player, track_id, None, slot=slot, error=f"{type(exc).__name__}: {exc}")

    def _ensure_pdb(self, ip: str, slot: str) -> bool:
        key = (ip, slot)
        if key in self._pdb_loaded:
            return self._pdb_loaded[key]
        ok = False
        if slot_name(slot) is None:
            log.info("phrase service: slot %r not fetchable", slot)
        elif self.nfs is None:
            log.info("phrase service: no NFS client; cannot fetch export.pdb")
        else:
            data = self.nfs.fetch_pdb(ip, slot)
            if data is not None:
                self._sink("pdb", ip, slot, 0, data)
                try:
                    n = self._lib(ip, slot).load_pdb_bytes(data, f"{ip}-{slot}")
                    log.info("phrase service: export.pdb from %s %s -> %d tracks", ip, slot, n)
                    ok = True
                except RekordboxLibraryError as exc:
                    log.warning("phrase service: export.pdb unreadable: %s", exc)
        self._pdb_loaded[key] = ok
        return ok

    def _sink(self, kind: str, ip: str, slot: str, track_id: int, data: bytes) -> None:
        if self.raw_sink is None:
            return
        try:
            self.raw_sink(kind, ip, slot, track_id, data)
        except Exception:  # noqa: BLE001 - a dump failure must not stop the fetch
            log.exception("phrase service raw sink failed")

    def _handle(self, ip: str, slot: str, player: int, track_id: int) -> None:
        if not self._ensure_pdb(ip, slot):
            if track_id:
                self._emit(player, track_id, None, slot=slot, error="export.pdb unavailable")
            return
        if not track_id:
            return
        lib = self._lib(ip, slot)
        track = lib.track(track_id)
        if track is None:
            # the pdb may be stale (USB re-exported since we fetched it): refetch once
            self._pdb_loaded.pop((ip, slot), None)
            if self._ensure_pdb(ip, slot):
                track = lib.track(track_id)
        if track is None:
            self._emit(player, track_id, None, slot=slot, lib=lib, error="track id not in export.pdb")
            return
        ext = self.nfs.fetch(ip, slot, track.ext_path) if self.nfs is not None else None
        table = parse_pssi(ext) if ext else None
        lib.put_phrases(track_id, table)
        plan = None
        if table is not None:
            dat = self.nfs.fetch(ip, slot, track.analyze_path) if self.nfs is not None else None
            if ext:
                self._sink("ext", ip, slot, track_id, ext)
            if dat:
                self._sink("dat", ip, slot, track_id, dat)
            grid = parse_pqtz(dat) if dat else None
            wave = parse_pwv5(ext) if ext else None
            try:
                plan = plan_track(table, track_id=track_id, title=track.title, bpm=track.bpm, grid=grid,
                                  waveform=wave, scene_pool=self.scene_pool)
                log.info("phrase service: %s", plan.summary())
            except Exception:  # noqa: BLE001 - a plan is a bonus; the table alone is still useful
                log.exception("phrase service: planning failed for track %d", track_id)
            self._plans[(slot, track_id)] = plan
        if table is None:
            self.stats["no_table"] += 1
            self._emit(player, track_id, None, slot=slot, lib=lib, title=track.title, bpm=track.bpm,
                       error="ANLZ .EXT fetch failed" if ext is None else "no PSSI phrase table (phrase analysis off?)")
        else:
            self.stats["fetched"] += 1
            log.info("phrase service: track %d %r -> %d phrases (%s mood)", track_id, track.title,
                     len(table.phrases), table.mood_name)
            self._emit(player, track_id, table, slot=slot, lib=lib, title=track.title, bpm=track.bpm, plan=plan)

    def _emit(self, player: int, track_id: int, table, *, slot: str = "", lib: RekordboxLibrary | None = None,
              title: str = "", bpm: float = 0.0, error: str = "", plan=None) -> None:
        if not title and lib is not None:
            t = lib.track(track_id)
            if t is not None:
                title, bpm = t.title, t.bpm
        self.queue.put(TrackPhraseMessage(timestamp=self.clock.monotonic(), player=int(player), track_id=int(track_id),
                                          title=title, bpm=float(bpm), table=table, slot=str(slot),
                                          source="rekordbox" if table is not None else "", error=error, plan=plan))
