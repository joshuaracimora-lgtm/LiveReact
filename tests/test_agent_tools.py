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
    assert by_rule["TIME-11"] == {"family": 2, "name": "dwell and transitions", "rule": "TIME-11",
                                  "tuning": 1, "holdout": 1, "fail": 2}
    assert by_rule["LOOK-05"]["family"] == 0  # not in a family -> "other"
    assert status.next_family(rows) == 2       # family 1 clean, family 2 has failures
    text = status.render(ev, None, "x.json")
    assert "NEXT FAMILY: 2  (lowest family with failures)" in text and "TIME-11   FAIL  2" in text


def test_status_closed_family_is_skipped_unless_state_reopens_it():
    status = _load("status")
    rows = status.family_table(_ev({"a": {"STRUCT-03": "FAIL", "TIME-11": "FAIL"}}))
    assert 1 in status.CLOSED
    assert status.next_family(rows) == 2                    # family 1 still fails but is closed
    assert status.next_family(rows, {"family": 1}) == 1     # state.json may reopen it explicitly
    assert status.next_family(rows, {"family": 3}) == 3
    text = status.render(_ev({"a": {"STRUCT-03": "FAIL"}}), {"family": 3}, "x")
    assert "NEXT FAMILY: 3  (from state.json)" in text and "[CLOSED:" in text


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
