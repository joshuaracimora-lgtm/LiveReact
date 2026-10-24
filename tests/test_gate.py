"""gate.compare: both sides must cover the same labeled tracks.

A track that silently drops out of the candidate (its timeline re-marked "est:",
or a name that no longer matches) would otherwise escape every per-track check
and could lift compliance by its absence alone.
"""
from __future__ import annotations

import unicodedata

from livereact.loop.evaluate import Evaluation, TrackEval
from livereact.loop.gate import compare
from livereact.sim.score import RULES

RULE = RULES[0][0]
QUALITY = {"distinct_looks": 4.0, "brightness_dynamic_range": 0.8,
           "fixture_utilization": 1.0, "drop_response_mean": 0.5}


def _t(name: str, compliance: float, verdict: str = "PASS", holdout: bool = False) -> TrackEval:
    return TrackEval(track=name, holdout=holdout, status="OK", compliance=compliance,
                     verdicts={RULE: verdict}, quality=dict(QUALITY), frames_digest="x")


def _ev(*tracks: TrackEval) -> Evaluation:
    return Evaluation(params={}, tracks=list(tracks))


def test_improvement_on_the_same_tracks_is_accepted():
    base = _ev(_t("a.mp3", 0.30), _t("b.mp3", 0.30))
    cand = _ev(_t("a.mp3", 0.40), _t("b.mp3", 0.30))
    assert compare(base, cand)["accept"]


def test_a_track_dropping_out_is_rejected_even_if_compliance_rises():
    base = _ev(_t("a.mp3", 0.60), _t("bad.mp3", 0.10, "FAIL"))
    cand = _ev(_t("a.mp3", 0.60))
    v = compare(base, cand)
    assert not v["accept"]
    assert any("track set changed" in r and "bad.mp3" in r for r in v["reasons"])


def test_a_new_track_appearing_is_rejected():
    base = _ev(_t("a.mp3", 0.30))
    cand = _ev(_t("a.mp3", 0.40), _t("new.mp3", 0.90))
    assert any("track set changed" in r for r in compare(base, cand)["reasons"])


def test_nfc_and_nfd_names_are_the_same_track():
    # the Mac baseline names files in NFD; a Windows copy may carry NFC
    nfc = "Inez feat. Inéz.mp3"
    nfd = unicodedata.normalize("NFD", nfc)
    base = _ev(_t(nfd, 0.30), _t("b.mp3", 0.30))
    cand = _ev(_t(nfc, 0.40), _t("b.mp3", 0.30))
    assert compare(base, cand)["accept"]


def test_a_regression_on_an_nfc_named_track_is_still_caught():
    nfc = "Inez feat. Inéz.mp3"
    nfd = unicodedata.normalize("NFD", nfc)
    base = _ev(_t(nfd, 0.30, "PASS"), _t("b.mp3", 0.30))
    cand = _ev(_t(nfc, 0.30, "FAIL"), _t("b.mp3", 0.50))
    v = compare(base, cand)
    assert not v["accept"]
    assert any("rule regressions" in r for r in v["reasons"])
