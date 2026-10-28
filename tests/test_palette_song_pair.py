"""palette-v2 (D-28): a planned song shows its colour on drops, one companion on breaks."""
from __future__ import annotations

from livereact.engines import palette
from livereact.engines.laser import PALETTE_TO_LASER


def test_every_song_colour_has_a_companion_that_is_a_real_palette():
    for song, comp in palette.COMPANION.items():   # the fan lasers need a CH12 for either
        assert comp != song and song in PALETTE_TO_LASER and comp in PALETTE_TO_LASER


def test_song_pair_drop_is_the_song_break_its_companion_build_keeps_the_white_wash():
    assert palette.song_pair("drop", "blood_red", "x", "y") == ("blood_red", "blood_red")
    assert palette.song_pair("peak", "deep_amber", "x", "y") == ("deep_amber", "deep_amber")
    assert palette.song_pair("break", "blood_red", "x", "y") == ("uv_violet", "uv_violet")
    assert palette.song_pair("outro", "deep_amber", "x", "y") == ("hot_magenta", "hot_magenta")
    assert palette.song_pair("build", "acid_yellow", "neutral_wash", "y") == ("neutral_wash", "acid_yellow")


def test_a_song_colour_without_a_companion_keeps_the_break_family_pick():
    assert palette.song_pair("break", "not_a_palette", "deep_blue", "y") == ("deep_blue", "deep_blue")
