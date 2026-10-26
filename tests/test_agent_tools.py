"""tools/agent/*: the pure functions the nightly agent and its orchestrator rely on."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "agent" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ev(verdicts_by_track: dict[str, dict[str, str]], holdout: set[str] = frozenset()) -> dict:
    tracks = [{"track": n, "holdout": n in holdout, "status": "BLOCKED", "compliance": 0.3, "verdicts": v,
               "quality": {}, "frames_digest": "x"} for n, v in verdicts_by_track.items()]
    return {"params": {}, "rules": "abc", "summary": {"tuning": {"n": 1, "compliance": 0.3, "critical_fails": 1},
                                                      "holdout": {"n": 1, "compliance": 0.3, "critical_fails": 0}},
            "tracks": tracks}


def test_status_family_table_counts_fails_and_picks_next_family():
    status = _load("status")
    ev = _ev({"a": {"STRUCT-03": "PASS", "TIME-11": "FAIL", "LOOK-05": "FAIL"},
              "b": {"STRUCT-03": "PASS", "TIME-11": "FAIL", "TIME-02": "FAIL"}}, holdout={"b"})
    rows = status.family_table(ev)
    by_rule = {r["rule"]: r for r in rows}
    assert by_rule["STRUCT-03"]["fail"] == 0 and by_rule["STRUCT-03"]["family"] == 1
    assert by_rule["TIME-11"]["family"] == 4 and by_rule["TIME-11"]["fail"] == 2      # marker-free -> generalisation
    assert by_rule["TIME-11"]["tuning"] == 1 and by_rule["TIME-11"]["holdout"] == 1
    assert by_rule["LOOK-05"]["family"] == 2   # section shape
    assert status.next_family(rows) == 2       # family 1 clean, family 2 has failures
    text = status.render(ev, None, "x.json")
    assert "NEXT FAMILY: 2  (lowest family with failures)" in text and "TIME-11   FAIL  2" in text
    # 2026-10-26: a serious sweep finding is the Bugs & UI agent's (family 0), never the Track Classifier's
    assert status.next_family(rows, None, {"counts": {"crash": 1}}) == 2
    assert status.next_family(rows, None, {"counts": {"crash": 1}}, role="ui") == 0
    assert status.next_family(rows, None, {"counts": {"warning": 3}}, role="ui") is None   # -> the UI bug list
    assert status.next_family(rows, None, {"counts": {"warning": 3}}) == 2
    assert "sweep" in status.render(ev, None, "x", {"date": "20261026", "tracks_ran": 5, "tracks_total": 9, "counts": {"crash": 1}}).lower()


def test_status_closed_family_is_skipped_unless_state_reopens_it():
    status = _load("status")
    rows = status.family_table(_ev({"a": {"STRUCT-03": "FAIL", "TIME-11": "FAIL"}}))
    assert status.next_family(rows) == 1                    # drop placement is open again in the planner era
    status.CLOSED[1] = "test closure"
    try:
        assert status.next_family(rows) == 4                # family 1 fails but is closed; TIME-11 sits in 4
        assert status.next_family(rows, {"family": 1}) == 1 # state.json may reopen it explicitly
        assert status.next_family(rows, {"family": 3}) == 3
        text = status.render(_ev({"a": {"STRUCT-03": "FAIL"}}), {"family": 3}, "x")
        assert "NEXT FAMILY: 3  (from state.json)" in text and "[CLOSED:" in text
    finally:
        status.CLOSED.pop(1, None)


def test_status_next_family_none_when_all_clean():
    status = _load("status")
    rows = status.family_table(_ev({"a": {"STRUCT-03": "PASS", "TIME-11": "PASS"}}))
    assert status.next_family(rows) is None


def test_gate_head_flips_lists_both_directions_and_new_tracks():
    gate_head = _load("gate_head")
    base = _ev({"a": {"R1": "FAIL", "R2": "PASS"}, "b": {"R1": "PASS"}})
    cand = _ev({"a": {"R1": "PASS", "R2": "FAIL"}, "b": {"R1": "PASS"}, "c": {"R1": "PASS"}})
    fl = gate_head.flips(base, cand)
    assert {"track": "a", "rule": "R1", "from": "FAIL", "to": "PASS"} in fl
    assert {"track": "a", "rule": "R2", "from": "PASS", "to": "FAIL"} in fl
    assert not any(f["track"] == "b" for f in fl)
    assert any(f["track"] == "c" and f["to"] == "new track" for f in fl)


def test_record_night_merges_agent_and_orchestrator_records(tmp_path):
    rn = _load("record_night")
    st = rn.load(tmp_path / "state.json")
    assert st["nights"] == [] and st["family"] is None
    # the agent records first: family, subgoal, its note, a question
    st = rn.record(st, date="20261024", branch="agent/20261024", commits=1, gate="ACCEPT", tests="ok",
                   family=2, subgoal="TIME-02 next", note="dwell in bars", questions=["is HOLD 8 right?"])
    # the orchestrator records the verified numbers afterwards, with no family/subgoal
    st = rn.record(st, date="20261024", branch="agent/20261024", commits=1, gate="REJECT", tests="FAIL",
                   family=None, subgoal="", note="orchestrator: exited 0", questions=["is HOLD 8 right?"])
    assert len(st["nights"]) == 1
    n = st["nights"][0]
    assert n["gate"] == "REJECT" and n["tests"] == "FAIL"           # verified values win
    assert n["family"] == 2 and n["note"] == "dwell in bars; orchestrator: exited 0"   # nothing erased
    assert st["family"] == 2 and st["subgoal"] == "TIME-02 next"
    assert st["open_questions"] == ["is HOLD 8 right?"]
    # a different night is a new entry
    st = rn.record(st, date="20261025", branch="agent/20261025", commits=0, gate="NO-CHANGE", tests="skipped")
    assert len(st["nights"]) == 2
    (tmp_path / "state.json").write_text(json.dumps(st))
    assert rn.load(tmp_path / "state.json")["nights"][0]["gate"] == "REJECT"


def test_only_ear_verified_tracks_count_and_dump_signals_refuses_the_rest(tmp_path):
    """Josh's rule: the agent works only tracks whose pass he has finished (no 'est:' notes)."""
    import json
    import subprocess
    import sys
    from livereact.loop.evaluate import is_ear_verified, labeled_tracks
    from livereact.sim.timeline import Timeline

    good = {"bpm": 128.0, "first_downbeat_s": 0.5, "beats_per_bar": 4, "source": "ear",
            "markers": [{"t": 30.0, "kind": "drop", "note": "big drop, whiteout before", "end": None}]}
    est = {**good, "markers": [{"t": 30.0, "kind": "drop", "note": "est: kick 0.1->1.0", "end": None}]}
    for name, tl in (("good", good), ("est", est)):
        (tmp_path / f"{name}.mp3").write_bytes(b"")
        (tmp_path / f"{name}.timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    assert is_ear_verified(Timeline.load(tmp_path / "good.timeline.json"))
    assert not is_ear_verified(Timeline.load(tmp_path / "est.timeline.json"))
    assert [p.name for p in labeled_tracks(tmp_path)] == ["good.mp3"]

    r = subprocess.run([sys.executable, "tools/agent/dump_signals.py", str(tmp_path / "est.mp3"), "--all"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stderr
    assert "not ear-verified" in r.stderr


def test_record_night_keeps_the_two_agents_apart(tmp_path):
    """The UI agent's family/subgoal/questions live under roles.ui; the Track Classifier keeps the top level."""
    rn = _load("record_night")
    status = _load("status")
    st = rn.load(tmp_path / "state.json")
    st = rn.record(st, date="20261027", branch="agent/20261027", role="tracks", commits=1, gate="ACCEPT",
                   tests="ok", family=2, subgoal="tracks next", note="", questions=["tracks q"])
    st = rn.record(st, date="20261027", branch="agent/20261027-ui", role="ui", commits=1, gate="ACCEPT",
                   tests="ok", family=0, subgoal="ui next", note="", questions=["ui q"])
    assert st["family"] == 2 and st["subgoal"] == "tracks next" and st["open_questions"] == ["tracks q"]
    assert st["roles"]["ui"] == {"family": 0, "subgoal": "ui next", "open_questions": ["ui q"]}
    assert [n["role"] for n in st["nights"]] == ["tracks", "ui"]
    rows = status.family_table(_ev({"a": {"LOOK-05": "FAIL"}}))
    assert status.next_family(rows, st) == 2                      # the UI agent's family 0 does not leak
    tracks_view, ui_view = status._role_view(st, "tracks"), status._role_view(st, "ui")
    assert "roles" not in tracks_view and [n["role"] for n in tracks_view["nights"]] == ["tracks"]
    assert ui_view["subgoal"] == "ui next" and [n["role"] for n in ui_view["nights"]] == ["ui"]
    # an older night without a role is the Track Classifier's
    st["nights"].insert(0, {"date": "20261020", "branch": "agent/20261020", "gate": "ACCEPT"})
    assert len(status._role_view(st, "tracks")["nights"]) == 2


def test_open_ui_bugs_reads_the_numbered_open_list(tmp_path):
    status = _load("status")
    f = tmp_path / "UI_BUGS.md"
    f.write_text("# x\n\n## Open (highest risk first)\n\n1. **First.** a\n   more\n2. **Second.** b\n\n"
                 "## Fixed\n\n- **Old.** c\n", encoding="utf-8")
    assert status.open_ui_bugs(f) == ["**First.** a", "**Second.** b"]
    assert len(status.open_ui_bugs()) >= 1                         # the real list parses too

