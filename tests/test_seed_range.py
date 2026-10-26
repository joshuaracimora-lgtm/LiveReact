"""The simulation's normalizer seed is pinned, recorded, and gated.

2026-10-24: evaluate() seeded the energy normalizer from the cwd's gitignored config.json
(the live app's last mic calibration). A tree with no config.json cold-started it and the
UNCHANGED engine disagreed with its own baseline on every track (7 spurious rule flips, two
fake regressions). The baseline, the gate and the knob search must all simulate from one seed,
so the seed is a constant, every evaluation records it, and compare() refuses a mismatch.
"""
from __future__ import annotations

import json
import os
import unicodedata

from livereact.loop.evaluate import Evaluation, TrackEval
from livereact.loop.gate import compare
from livereact.sim.harness import SIM_SEED_RANGE, default_seed_range


def test_default_seed_is_pinned_and_ignores_config_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"energy_norm": {"floor": 0.5, "ceiling": 0.6}}), encoding="utf-8")
    assert default_seed_range() == SIM_SEED_RANGE
    assert default_seed_range("config.json") == SIM_SEED_RANGE
    os.remove(tmp_path / "config.json")
    assert default_seed_range() == SIM_SEED_RANGE


def test_pinned_seed_matches_the_committed_baselines():
    # the committed results/baseline/evaluation.json files were made with this seed (Josh's
    # 2026-10-11 MacBook calibration); changing it means regenerating every baseline
    assert SIM_SEED_RANGE == (0.0795, 0.8728)


def _ev(seed, compliance=0.5):
    ev = Evaluation(params={}, seed_range=seed)
    ev.tracks = [TrackEval(track="t.mp3", holdout=False, status="BLOCKED", compliance=compliance,
                           verdicts={"TIME-02": "FAIL"}, quality={"distinct_looks": 4}, frames_digest="x")]
    return ev


def test_evaluation_round_trips_its_seed():
    ev = _ev([0.1, 0.9])
    back = Evaluation.from_json(json.loads(json.dumps(ev.to_json())))
    assert back.seed_range == [0.1, 0.9]
    # an older baseline without the field loads as None, and is still comparable
    old = ev.to_json(); del old["seed_range"]
    assert Evaluation.from_json(old).seed_range is None


def test_gate_refuses_a_candidate_simulated_from_another_seed():
    base = _ev([0.0795, 0.8728], compliance=0.30)
    cand = _ev([0.0, 1.0], compliance=0.40)   # "better", but not comparable
    v = compare(base, cand)
    assert not v["accept"] and any("seed differs" in r for r in v["reasons"])
    same = _ev([0.0795, 0.8728], compliance=0.40)
    assert not any("seed differs" in r for r in compare(base, same)["reasons"])
    legacy = _ev(None, compliance=0.30)        # pre-2026-10-24 baseline: taken at its word
    assert not any("seed differs" in r for r in compare(legacy, same)["reasons"])


def test_gate_head_flips_match_track_names_across_nfc_nfd():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("gate_head", Path(__file__).resolve().parents[1] / "tools" / "agent" / "gate_head.py")
    gh = importlib.util.module_from_spec(spec); spec.loader.exec_module(gh)
    nfc, nfd = unicodedata.normalize("NFC", "Åa.mp3"), unicodedata.normalize("NFD", "Åa.mp3")
    assert nfc != nfd
    base = {"tracks": [{"track": nfd, "verdicts": {"TIME-02": "FAIL"}}]}
    cand = {"tracks": [{"track": nfc, "verdicts": {"TIME-02": "PASS"}}]}
    fl = gh.flips(base, cand)
    assert fl == [{"track": nfc, "rule": "TIME-02", "from": "FAIL", "to": "PASS"}]


def test_rules_fingerprint_tracks_the_scorer_source(tmp_path, monkeypatch):
    """A baseline must be regenerated when a rule's logic changes, not only when a rule is added."""
    import hashlib
    from pathlib import Path
    from livereact.loop import evaluate
    from livereact.sim.score import RULES
    sim = Path(evaluate.__file__).resolve().parents[1] / "sim"
    score_src = (sim / "score.py").read_bytes()
    # 2026-10-26: the drop-class rules are part of what is measured too
    src = score_src + b"\n" + (sim / "drop_class.py").read_bytes()
    ids = "|".join(f"{r}:{s}" for r, s, _ in RULES)
    assert evaluate.rules_fingerprint() == hashlib.sha256(ids.encode() + b"\n" + src).hexdigest()[:12]
    assert evaluate.rules_fingerprint() != hashlib.sha256(ids.encode() + b"\n" + score_src).hexdigest()[:12]
    assert evaluate.rules_fingerprint() != hashlib.sha256(ids.encode()).hexdigest()[:12]
