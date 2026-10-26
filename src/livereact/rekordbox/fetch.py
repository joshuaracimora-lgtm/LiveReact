"""Pull rekordbox export files off the USB stick *inside* the player, over NFS.

Pioneer players serve their media over plain NFSv2/UDP (that is how linked CDJs
browse each other's sticks). The vendored ``prodj.network.nfsclient`` implements
the client; it works standalone with just an IP (its ``prodj`` argument is only
used by a callback path we don't take). ``databases/player-1-sd.pdb`` in this
repo was fetched from Josh's XDJ-XZ this way in June 2026, so the path is known
to work on this unit.

Slot naming quirk (Deep Symmetry; matches that file's name): the XDJ-XZ reports
both of its USB slots under player 1, and USB1 shows up as the *SD* slot
(NFS export ``/B/``); "usb" is ``/C/``.

Blocking calls, meant to run on a worker thread (``rekordbox/service.py``) or in
the probe — never on the metronome thread.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

PDB_PATH = "/PIONEER/rekordbox/export.pdb"
_SLOTS = {"sd": "sd", "usb": "usb"}


def slot_name(status_slot: str) -> str | None:
    """NFS slot for a status packet's ``loaded_slot``; None when not fetchable (cd/rekordbox/empty)."""
    return _SLOTS.get(str(status_slot))


class XdjNfs:
    def __init__(self) -> None:
        self._client = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._client is not None:
            return
        try:
            from prodj.network.nfsclient import NfsClient  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("python-prodj-link (prodj.network.nfsclient) not importable") from exc
        client = NfsClient(None)
        client.start()
        self._client = client

    def stop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.stop()
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            log.debug("NFS client stop: %s", exc)

    @property
    def running(self) -> bool:
        return self._client is not None

    def fetch(self, ip: str, slot: str, path: str, timeout_s: float = 30.0) -> bytes | None:
        """Download one file as bytes; None on any failure (logged at warning)."""
        if self._client is None:
            raise RuntimeError("XdjNfs not started")
        nfs_slot = slot_name(slot)
        if nfs_slot is None:
            log.warning("NFS: slot %r is not fetchable", slot)
            return None
        with self._lock:  # one transfer at a time keeps the UDP window predictable
            try:
                future = self._client.enqueue_download(ip, nfs_slot, path)
                data = future.result(timeout=timeout_s)
            except Exception as exc:  # noqa: BLE001 - RuntimeError / TimeoutError / socket errors
                log.warning("NFS fetch %s:%s%s failed: %s", ip, nfs_slot, path, exc)
                return None
        if not isinstance(data, (bytes, bytearray)):
            log.warning("NFS fetch %s returned %r", path, type(data).__name__)
            return None
        log.info("NFS fetched %s from %s (%d bytes)", path, ip, len(data))
        return bytes(data)

    def fetch_pdb(self, ip: str, slot: str, timeout_s: float = 60.0) -> bytes | None:
        return self.fetch(ip, slot, PDB_PATH, timeout_s=timeout_s)
