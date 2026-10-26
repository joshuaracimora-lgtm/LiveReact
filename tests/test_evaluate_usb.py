"""evaluate() over a synthetic USB export: scored tracks drive from their own plan, the sweep sample
scores marker-free rules on a marker-less timeline, and the JSON round-trips."""

from __future__ import annotations

import json
from pathlib import Path

from livereact.loop import evaluate as ev_mod
from livereact.loop.evaluate import Evaluation, evaluate, labeled_tracks, sweep_sample
from livereact.sim.synth import synth_house
from test_usb_export import _export


def _tracks_dir(tmp_path: Path) -> Path:
    tdir = tmp_path / "tracks"
    tdir.mkdir(exist_ok=True)
    # an ear-verified timeline for Lift (drop at the first chorus's bass return, beat 65 @125bpm ~ 30.8 s)
    (tdir / "Lift.timeline.json").write_text(json.dumps({"bpm": 125.0, "first_downbeat_s": 0.1, "source": "manual",
                                                         "markers": [{"t": 30.8, "kind": "drop", "note": ""}]}))
    return tdir


def test_evaluate_drive_with_usb_and_sweep(tmp_path, monkeypatch):
    usb = _export(tmp_path, [(4, "Lift", "Lift.mp3"), (5, "Other", "Other.mp3"), (6, "Third", "Third.mp3")])
    tdir = _tracks_dir(tmp_path)
    audio = synth_house(seconds=40)
    monkeypatch.setattr(ev_mod, "_audio", lambda p: audio)
    monkeypatch.setattr(ev_mod, "decode_audio", lambda p: audio)

    scored = labeled_tracks(tdir, usb)
    assert [p.name for p in scored] == ["Lift.mp3"] and scored[0].parent.name == "Album"   # audio from the USB
    sweep = sweep_sample(usb, 2, exclude={p.name for p in scored})
    assert {t.info.id for t in sweep} == {5, 6}

    ev = evaluate(None, scored, rig="rigs/josh_rig.json", usb=usb, drive=True, sweep_tracks=sweep, tracks_dir=tdir)
    assert ev.mode == "drive" and ev.analysis_fingerprint and len(ev.analysis_fingerprint) == 12
    assert len(ev.tracks) == 1 and ev.tracks[0].driven and ev.tracks[0].track_id == 4
    assert len(ev.tracks[0].drop_offsets) == 1 and "offset_bars" in ev.tracks[0].drop_offsets[0]
    assert len(ev.sweep) == 2 and all(t.driven for t in ev.sweep) and ev.sweep_set
    marker_rules = {"STRUCT-03", "TIME-04", "TIME-05", "TIME-06", "LOOK-05", "STRUCT-01"}
    for t in ev.sweep:
        assert all(t.verdicts[r] == "NOT_SCORED" for r in marker_rules if r in t.verdicts)
    s = ev.summary()
    assert s["mode"] == "drive" and s["sweep"]["n"] == 2 and s["tuning"]["driven"] + s["holdout"]["driven"] == 1
    back = Evaluation.from_json(json.loads(json.dumps(ev.to_json())))
    assert back.summary() == s and back.tracks[0].drop_offsets == ev.tracks[0].drop_offsets


def test_evaluate_without_usb_is_classifier_mode(tmp_path, monkeypatch):
    tdir = _tracks_dir(tmp_path)
    (tdir / "Lift.mp3").write_bytes(b"x")
    audio = synth_house(seconds=30)
    monkeypatch.setattr(ev_mod, "_audio", lambda p: audio)
    ev = evaluate(None, labeled_tracks(tdir), rig="rigs/josh_rig.json", tracks_dir=tdir)
    assert ev.mode == "classifier" and not ev.tracks[0].driven and ev.tracks[0].track_id is None
    assert ev.analysis_fingerprint is None and ev.sweep == [] and ev.sweep_set is None
    assert ev.summary()["sweep"]["n"] == 0


def test_labeled_tracks_pairs_nfd_audio_with_nfc_timeline(tmp_path):
    tdir = tmp_path / "tracks"
    tdir.mkdir()
    nfc = "Caf\u00e9 Track"                                 # the timeline name as git stores it (NFC)
    nfd = "Cafe\u0301 Track"                                # the audio copied from the Mac (NFD)
    (tdir / f"{nfc}.timeline.json").write_text(json.dumps({"bpm": 134.0, "source": "manual",
                                                            "markers": [{"t": 42.8, "kind": "drop", "note": ""}]}))
    (tdir / f"{nfd}.mp3").write_bytes(b"x")
    got = labeled_tracks(tdir)
    assert len(got) == 1 and got[0].suffix == ".mp3"
