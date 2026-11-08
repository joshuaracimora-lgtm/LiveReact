"""The console's health banner: what is broken right now, in words an operator can act on mid-set.

2026-11-02, 2026-10-31 show post-mortem: on 2026-10-31 rekordbox held the Pro DJ Link ports all night (only an
ERROR line in the log), the XDJ's audio stream died for 44 minutes and the DMX adapter dropped off
USB, and all the console showed was four small dots. Pure function of the app and a snapshot so it
is testable without Qt; ui/console.py turns the list into a red banner.
"""
from __future__ import annotations

import errno

GRACE_S = 5.0   # inputs get this long after start before a missing signal counts as a fault
PDL_QUIET_S = 5.0   # no Pro DJ Link packet of ANY kind for this long = the XDJ is off the network


def _port_in_use(exc: BaseException | None) -> bool:
    return isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE


def _pdl_quiet(app, now: float) -> bool:
    src = next((s for s in getattr(app, "sources", ()) if getattr(s, "name", "") == "prodj-link"), None)
    if src is None:
        return False   # no Pro DJ Link input in this setup: nothing to warn about
    last = getattr(src, "last_packet_time", None)
    return last is None or now - last >= PDL_QUIET_S


def health_warnings(app, snap, now: float) -> list[str]:
    """Plain-language faults, worst first. Empty in demo mode and when all is well."""
    if getattr(app.config, "demo", True):
        return []
    out: list[str] = []
    started = getattr(app, "started_at", None)
    settled = started is not None and now - started >= GRACE_S
    failed = getattr(app, "failed_inputs", {})

    writer = app.writer
    if not writer.is_open or getattr(getattr(writer, "backend", None), "name", "") == "null-log":
        out.append("DMX ADAPTER LOST — lights are not getting signal. Check the Enttec's USB; "
                   "LiveReact reconnects by itself when it is back.")

    pdl = failed.get("prodj-link")
    if _port_in_use(pdl):
        out.append("DJ LINK BLOCKED — another program (rekordbox?) holds the Pro DJ Link ports. "
                   "Quit it on this Mac, then restart LiveReact. Until then: no tempo lock, no track plans, "
                   "no automatic lasers.")
    elif pdl is not None:
        out.append(f"DJ LINK FAILED TO START ({pdl}). No tempo lock, track plans or automatic lasers.")
    elif settled and not snap.pdl_fresh and _pdl_quiet(app, now):
        # beats stop whenever no deck plays; only a silent network (no keepalives either) is a fault
        out.append("DJ LINK SILENT — no beat data from the XDJ. Check its Ethernet cable / the link-local "
                   "network. Automatic lasers stay dark until it is back.")

    audio = failed.get("audio")
    if audio is not None:
        out.append(f"NO AUDIO INPUT ({audio}). The lights cannot hear the music.")
    elif settled and not snap.audio_fresh:
        out.append("NO AUDIO — LiveReact cannot hear the XDJ. Reconnecting by itself; if this stays, "
                   "check the XDJ's USB cable.")

    # MIDI is silent whenever the DJ leaves the controls alone, so only a port that was
    # open and VANISHED is a fault (2026-11-03; a setup without XDJ MIDI is never nagged).
    midi = next((s for s in getattr(app, "sources", ()) if getattr(s, "name", "") == "midi"), None)
    if midi is not None and getattr(midi, "lost", False):
        out.append("XDJ MIDI LOST — fader/EQ moves no longer reach the lights. Reconnecting by itself; "
                   "if this stays, check the XDJ's USB cable.")
    return out
