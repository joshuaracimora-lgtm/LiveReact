"""Audio <-> timeline pairing must survive NFC/NFD file-name differences (Windows)."""
from __future__ import annotations

import unicodedata

from livereact.sim.timeline import match_timeline_name

NFC = unicodedata.normalize("NFC", "Inez - crystallized feat. Inéz .timeline.json")
NFD = unicodedata.normalize("NFD", NFC)
OTHER = "messy _laszewo edit_ - Łaszewo.timeline.json"


def test_exact_name_wins():
    assert match_timeline_name(NFC, [NFC, OTHER]) == NFC


def test_nfd_audio_finds_nfc_timeline_and_back():
    assert NFC != NFD                                     # the two spellings really differ
    assert match_timeline_name(NFD, [NFC, OTHER]) == NFC  # Mac-copied audio, git-committed timeline
    assert match_timeline_name(NFC, [NFD, OTHER]) == NFD


def test_no_match_returns_none():
    assert match_timeline_name(NFD, [OTHER]) is None
    assert match_timeline_name("x.timeline.json", []) is None
