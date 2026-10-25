"""rekordbox export data: the USB stick's own analysis, read by LiveReact.

Why this package exists (2026-10-25): rekordbox writes everything it knows about
a track onto the USB when you export -- ``PIONEER/rekordbox/export.pdb`` (the
track database) and per-track ``ANLZ0000.DAT/.EXT`` files (beat grid, cues,
waveforms and the ``PSSI`` song-structure table: intro/up/down/chorus/outro or
verse/bridge/chorus phrases positioned by beat number). The XDJ-XZ's CDJ status
packets carry the loaded track's rekordbox ID and an absolute beat counter, so
beat counter + phrase table = the exact section the track is in *and* how many
beats until the next one. That replaces audio-inferred section guessing with a
lookup (see the plan in docs/13_DECISION_LOG.md D-21 once landed).

Modules:
- ``pssi``     -- pure-``struct`` parser for the ANLZ container, PSSI and PQTZ tags.
- ``mapping``  -- PSSI phrase kinds -> LiveReact section / bucket / timeline kinds (data).
- ``library``  -- track index over export.pdb (via the vendored ``prodj.pdblib``) + phrase cache.
- ``fetch``    -- (Stage 1) NFS fetch of export.pdb / ANLZ files from the player.

Nothing here touches the engines; it is read-only data plumbing.
"""
