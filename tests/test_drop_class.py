"""Drop classes (2026-10-26) and the marker "class" field."""
import json
import logging

import pytest

from livereact.sim.drop_class import FULL_DROP_MIN_BARS, drop_bars, infer_drop_class, is_inferred
from livereact.sim.timeline import DROP_CLASSES, Marker, Timeline

BPM = 128.0
BAR = 240.0 / BPM


def _drop(note: str, bars: float | None) -> Marker:
    return Marker(t=10.0, kind="drop", note=note, end=None if bars is None else 10.0 + bars * BAR)


# The ear notes on the ear-verified drops as of 2026-10-26 (text only, no track names), with the class
# each must get. The order of the rules is what this pins: negated rolls, atmospheric-with-a-roll and
# "crashes/claps, not a strobe roll" all mention a roll and are not one.
EAR_DROPS = [
    ("in-the-roll kick: strobe starts low-brightness and escalates to the end; drums cut at the end", 7.0, "snare_roll"),
    ("drop 2, less of a drop: weaker snare roll, strobe at reduced intensity (not drop-1 intensity)", 7.0, "snare_roll"),
    ("", 12.3, "full"),
    ("no snare roll (synths + claps, no kick): don't strobe hard - a soft, slower strobe at most, "
     "better to hit the lights on the claps/crashes", 6.0, "clap"),
    ("react to the crashes/claps, not a strobe roll; kick drops out 3:23.66-3:30.93 while claps carry it", 8.0, "clap"),
    ("", 8.5, "full"),
    ("", 4.2, "snare_roll"),
    ("snare roll 1: escalating strobe", 3.0, "snare_roll"),
    ("snare roll 2: escalating strobe", 4.0, "snare_roll"),
    ("snare roll 3: escalating strobe", 4.0, "snare_roll"),
    ("starts on the crash; no roll: lights follow the crashes", 8.0, "clap"),
    ("snare roll, super quiet: low strobe ceiling, escalate gently", 5.0, "snare_roll"),
    ("strobe section at the top of the build", 3.0, "snare_roll"),
    ("atmospheric drop: a bit of a snare roll but NO strobe - lights get brighter and pulsate", 8.0, "atmospheric"),
    ("starts on the snare roll", 2.9, "snare_roll"),
    ("", 8.0, "full"),
    ("starts on the clap", 7.3, "snare_roll"),     # score.py CLAP_WORDS: a roll that lands on a clap
    ("starts quiet and escalates (long drop): strobe should start small and build like a snare roll", 7.5, "snare_roll"),
]


@pytest.mark.parametrize("note,bars,want", EAR_DROPS)
def test_every_ear_drop_gets_the_class_its_note_describes(note, bars, want):
    assert infer_drop_class(_drop(note, bars), BPM) == want


def test_an_explicit_class_always_wins_and_is_not_inferred():
    m = _drop("snare roll 1: escalating strobe", 3.0)
    m.cls = "full"
    assert infer_drop_class(m, BPM) == "full" and not is_inferred(m)
    assert is_inferred(_drop("", 12.0))


def test_length_alone_decides_without_a_telling_note():
    assert infer_drop_class(_drop("", FULL_DROP_MIN_BARS), BPM) == "full"
    assert infer_drop_class(_drop("", 7.8), BPM) == "full"         # a label a hair short of 8 bars
    assert infer_drop_class(_drop("", 6.0), BPM) == "snare_roll"
    assert infer_drop_class(_drop("", None), BPM) == "full"        # open-ended drop
    assert drop_bars(_drop("", 4.0), BPM) == pytest.approx(4.0)


def test_class_round_trips_and_files_without_one_are_unchanged():
    tl = Timeline(bpm=BPM, markers=[Marker(1.0, "drop", "x", 9.0, cls="clap"), Marker(20.0, "breakdown")])
    d = json.loads(tl.to_json())
    assert d["markers"][0]["class"] == "clap" and "class" not in d["markers"][1] and "cls" not in d["markers"][0]
    assert Timeline.from_json(tl.to_json()).markers[0].cls == "clap"
    plain = {"bpm": 120.0, "first_downbeat_s": 0.5, "beats_per_bar": 4, "source": "manual", "notes": "",
             "markers": [{"t": 1.0, "kind": "drop", "note": "", "end": 5.0}]}
    assert json.loads(Timeline.from_json(json.dumps(plain)).to_json()) == plain


def test_bad_vocabulary_warns_instead_of_crashing_the_night(caplog):
    text = json.dumps({"bpm": 120.0, "markers": [
        {"t": 1.0, "kind": "dorp", "note": "", "end": None},
        {"t": 2.0, "kind": "drop", "note": "", "end": None, "class": "huge"},
        {"t": 3.0, "kind": "fill", "note": "", "end": None, "colour": "red"}]})
    with caplog.at_level(logging.WARNING, logger="livereact.sim.timeline"):
        tl = Timeline.from_json(text, where=" test.json")
    assert len(tl.markers) == 3
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "unknown kind 'dorp'" in msgs and "class 'huge'" in msgs and "unknown keys ['colour']" in msgs


def test_drop_classes_cover_what_josh_named():
    assert {"snare_roll", "full"} <= set(DROP_CLASSES)


def test_josh_confirming_a_class_or_editing_a_look_note_forces_a_new_baseline():
    from livereact.loop.evaluate import Evaluation, labels_fingerprint
    from livereact.loop.gate import mode_mismatch
    from livereact.rekordbox.look_notes import from_dict

    def tl(cls=""):
        return Timeline(bpm=BPM, markers=[Marker(10.0, "drop", "", 10.0 + 4 * BAR, cls=cls)])

    base = labels_fingerprint([("a.mp3", tl(), None)])
    assert labels_fingerprint([("a.mp3", tl(), None)]) == base                       # deterministic
    assert labels_fingerprint([("a.mp3", tl("snare_roll"), None)]) == base           # confirming the inferred class
    assert labels_fingerprint([("a.mp3", tl("full"), None)]) != base                 # overriding it
    look = from_dict({"schema": 1, "track": "a", "strobe": "soft"})
    assert labels_fingerprint([("a.mp3", tl(), look)]) != base
    b = Evaluation(params={}, mode="drive", labels_fingerprint=base)
    c = Evaluation(params={}, mode="drive", labels_fingerprint=labels_fingerprint([("a.mp3", tl("full"), None)]))
    assert "labels on the scored tracks changed" in mode_mismatch(b, c)
    assert Evaluation.from_json(b.to_json()).labels_fingerprint == base
