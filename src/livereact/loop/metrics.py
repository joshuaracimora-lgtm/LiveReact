"""Loop metrics that are not scorer rules: drop placement error and the marker-free rule set.

``drop_offsets`` is the planner's own yardstick (2026-10-25): how many bars
separate each ear-labelled drop from the moment the lights actually went to
DROP. rekordbox's chorus boundary is the bass return; Josh marks the drop at
the roll 4-8 bars earlier, so this number is what the agent must drive down.

``marker_free_rules`` names the scorer rules that judge ANY run, labelled or
not (dwell, look hold, legal transitions, restraint, static cut, strobe
safety): the generalisation set is scored on these alone.
"""

from __future__ import annotations

from ..sim.timeline import Timeline

DROP_BUCKETS = {"DROP", "PEAK"}
MARKER_FREE_RULES: tuple[str, ...] = ("TIME-11", "TIME-02", "TIME-13", "TIME-21", "LOOK-03", "SAFE-04", "SAFE-09")


def marker_free_rules() -> list[str]:
    return list(MARKER_FREE_RULES)


def drop_entries(signals: list[dict]) -> list[float]:
    """Times at which the shown bucket entered DROP/PEAK from anything else."""
    out: list[float] = []
    prev = None
    for row in signals:
        b = str(row.get("bucket", "")).upper()
        if b in DROP_BUCKETS and (prev is None or prev not in DROP_BUCKETS):
            out.append(float(row["t"]))
        prev = b
    return out


def drop_offsets(signals: list[dict], timeline: Timeline, window_bars: float = 8.0) -> list[dict]:
    """Per ear 'drop' marker: the nearest DROP/PEAK entry within +-window_bars.

    offset_bars is unsigned (what the gate averages); signed_bars is negative when the
    lights were early. A drop with no entry inside the window is 'missed' and counts
    as window_bars so a plan that never drops is not rewarded.
    """
    bar = timeline.bar_dur
    entries = drop_entries(signals)
    out = []
    for m in timeline.markers:
        if m.kind != "drop":
            continue
        window = window_bars * bar
        near = [e for e in entries if abs(e - m.t) <= window]
        if not near:
            out.append({"t": round(m.t, 3), "commit_t": None, "offset_bars": round(window_bars, 3),
                        "signed_bars": None, "missed": True})
            continue
        best = min(near, key=lambda e: abs(e - m.t))
        signed = (best - m.t) / bar
        out.append({"t": round(m.t, 3), "commit_t": round(best, 3), "offset_bars": round(abs(signed), 3),
                    "signed_bars": round(signed, 3), "missed": False})
    return out


def offsets_summary(offsets: list[list[dict]]) -> dict:
    """Mean unsigned error (bars) + misses over many tracks' drop_offsets lists."""
    flat = [o for track in offsets for o in track]
    if not flat:
        return {"drop_offset_bars_mean": None, "drops_missed": 0, "drops": 0}
    return {"drop_offset_bars_mean": round(sum(o["offset_bars"] for o in flat) / len(flat), 3),
            "drops_missed": sum(1 for o in flat if o["missed"]), "drops": len(flat)}
