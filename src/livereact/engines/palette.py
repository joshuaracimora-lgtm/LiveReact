"""Phrase-frozen palette (LIGHTING_BIBLE.md §3.2 named colors, §3.8 hue rule).

§3.8: "choose hue from the active palette on phrase boundaries only ... hue
frozen for the whole phrase" -- picked via "a deterministic energy-mode map
(BREAK cool, DROP saturated primary, etc.)". Until 2026-09-15 every Phrase
scene re-derived its hue from the instantaneous dominant audio band on every
tick (`two_axis_color(snap.replace(energy=1.0))`), which the third live
capture (recordings/session-20260915-125433) measured as the wash bar
flipping magenta<->red 6-10 times per SECOND in every BUILD/PEAK look --
Josh: "scrolling through random colors every half beat." The Reactive
layer's own phrase-frozen hue anchor never reached the wash at all (COLOR is
LTP by priority; Phrase outranks Reactive) and was stuck on the low band
100% of the time anyway (electronic music: bass always wins a raw max()).

So: ONE palette entry per bucket family per phrase, chosen here by the
metronome (the sole snapshot writer), read by every scene and by the
Reactive layer via `scenes/base.py`'s `scene_color`/`spot_color`. Audio no
longer picks hue at all. Per-song variety (Josh: "lights and colors can
still be personalized between songs") comes from `track_seed`, which the
metronome bumps on every detected track transition, so the same phrase
index lands on a different palette entry for the next track.

Per-bucket lists are the Bible's own §3.8 sketch ("BREAK cool, DROP
saturated primary") filled in with §3.2's named entries; BUILD's wash is
white by Josh's explicit call (2026-09-15: the build strobe "should be
white ... a little more intense") with the palette hue carried by the
moving heads instead.
"""

from __future__ import annotations

# §3.2 "RGB (no-white par)" column, scaled to 0..1.
PALETTE_RGB: dict[str, tuple[float, float, float]] = {
    "blood_red": (1.0, 0.0, 0.0),
    "deep_amber": (1.0, 90 / 255, 0.0),
    "acid_yellow": (1.0, 220 / 255, 0.0),
    "toxic_green": (0.0, 1.0, 40 / 255),
    "cyan_ice": (0.0, 220 / 255, 1.0),
    "cold_steel": (40 / 255, 120 / 255, 1.0),
    "deep_blue": (0.0, 20 / 255, 1.0),
    "uv_violet": (130 / 255, 0.0, 1.0),
    "hot_magenta": (1.0, 0.0, 150 / 255),
    "warm_white": (1.0, 200 / 255, 140 / 255),
    "neutral_wash": (1.0, 1.0, 1.0),
}

PALETTE_PHRASE_BARS = 16  # §3.8: "on each 16/32-bar boundary" -- counted from the
                          # bar the current bucket family was entered (core/metronome.py)
PALETTE_MIN_HOLD_BARS = 8  # a phrase boundary never re-picks a color younger than this
# Only a BREAK re-picks on phrase boundaries at all. A drop's phrase
# boundaries are where its section changes happen (session-20260915-144431:
# the breakdown landed exactly 32 bars after the drop), so a boundary
# re-pick there fires ~2s before the classifier sees the change -- one
# color, then another, then the break's: Josh's "two or three colors before
# the scene it should be at." One color per drop/peak stay instead; BUILD's
# wash is white regardless. Long breaks/intros keep the 16-bar walk.
PHRASE_REPICK_FAMILIES = frozenset({"break"})

# (wash entries, spot entries) per bucket FAMILY (see `family()` below). A
# single-entry tuple means "always this" (BUILD's white wash). PEAK shares
# DROP's list by construction: a PEAK is the escalation of a drop that is
# already showing its one color, and `_derive_palette` never re-picks on a
# same-family overlay flip -- a separate PEAK list (blood_red/acid_yellow/
# hot_magenta, the earlier 2026-09-15 sketch) was unreachable and was
# removed in the same day's polish pass.
BREAK_WASH = ("deep_blue", "uv_violet", "cold_steel")
BREAK_SPOT = BREAK_WASH
BUILD_WASH = ("neutral_wash",)
BUILD_SPOT = ("hot_magenta", "deep_amber", "cyan_ice")
DROP_WASH = ("blood_red", "cold_steel")   # the pair drop_blackout_hit already alternated
DROP_SPOT = DROP_WASH

_FAMILY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "break": (BREAK_WASH, BREAK_SPOT),
    "build": (BUILD_WASH, BUILD_SPOT),
    "drop": (DROP_WASH, DROP_SPOT),
}


# Two colours per song (palette-v2, 2026-10-28): a planned track's drop colour (D-23 / a look note)
# brings ONE companion for its breaks -- a related, cooler hue -- and its builds keep the white wash
# with the drop colour on the spots. Ed Warren (LD for Four Tet / Skrillex / Fred again..) keeps to at
# most two colours per song; docs/13_DECISION_LOG.md D-27.
COMPANION: dict[str, str] = {
    "blood_red": "uv_violet",
    "uv_violet": "deep_blue",
    "hot_magenta": "uv_violet",
    "deep_blue": "uv_violet",
    "cold_steel": "deep_blue",
    "cyan_ice": "deep_blue",
    "toxic_green": "cyan_ice",
    "deep_amber": "hot_magenta",
    "acid_yellow": "toxic_green",
    "warm_white": "cyan_ice",
}


def song_pair(bucket: str, song: str, wash: str, spot: str) -> tuple[str, str]:
    """(wash, spot) for a planned track whose drop colour is ``song``; ``wash``/``spot`` are the
    family's own pick, kept where the song colour does not apply (the build's white wash)."""
    fam = family(bucket)
    if fam == "drop":
        return song, song
    if fam == "break":
        c = COMPANION.get(song, wash)
        return c, c
    return wash, song                      # build: white wash, the song's colour on the heads


def family(bucket: str) -> str:
    """OUTRO is a promoted BREAK, PEAK an escalated DROP (core/metronome.py's
    overlays) -- same palette family, so an overlay flip alone never
    re-picks a color mid-drop/mid-break."""
    return {"outro": "break", "peak": "drop"}.get(bucket, bucket)


def pick(bucket: str, phrase_index: int, drop_cycle: int, track_seed: int) -> tuple[str, str]:
    """Deterministic (wash_name, spot_name) for this bucket + phrase. Same
    inputs -> same answer (replayable from a recording); `track_seed` and
    `drop_cycle` shift the walk so successive tracks/drops differ."""
    wash_list, spot_list = _FAMILY.get(family(bucket), _FAMILY["break"])
    idx = phrase_index + drop_cycle + track_seed
    return wash_list[idx % len(wash_list)], spot_list[idx % len(spot_list)]


def rgb(name: str) -> tuple[float, float, float]:
    return PALETTE_RGB.get(name, PALETTE_RGB["neutral_wash"])
