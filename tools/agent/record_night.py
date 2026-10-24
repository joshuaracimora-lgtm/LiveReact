"""Append one night's outcome to results/agent/state.json (the agent's memory across nights).

    python tools/agent/record_night.py --date 20261024 --branch agent/20261024 --commits 1 \
        --gate ACCEPT --tests ok --family 2 --note "TIME-11 dwell in bars: 6 tracks flipped"

state.json shape:
  {"family": <int|null>,            # the family the agent is working on (status.py's next_family if null)
   "subgoal": "<free text>",         # what to continue tomorrow
   "open_questions": ["..."],        # things only Josh can answer; the agent appends, Josh clears
   "nights": [{"date","branch","commits","gate","tests","family","note"}]}
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"family": None, "subgoal": "", "open_questions": [], "nights": []}


def record(state: dict, **night) -> dict:
    """Merge one record into the night keyed by (date, branch).

    Both the agent (family, subgoal, its own note) and the orchestrator (verified
    commits/gate/tests) record the same night; neither may erase the other's
    fields. Non-empty values win, notes are concatenated.
    """
    questions = night.pop("questions", None) or []
    nights = state.setdefault("nights", [])
    prev = next((n for n in nights if n.get("date") == night["date"] and n.get("branch") == night["branch"]), None)
    if prev is None:
        nights.append(dict(night))
    else:
        for k, v in night.items():
            if k == "note":
                if v and v not in (prev.get("note") or ""):
                    prev["note"] = f"{prev['note']}; {v}" if prev.get("note") else v
            elif v not in (None, ""):
                prev[k] = v
    if night.get("family") is not None:
        state["family"] = night["family"]
    if night.get("subgoal"):
        state["subgoal"] = night["subgoal"]
    for q in questions:
        if q not in state.setdefault("open_questions", []):
            state["open_questions"].append(q)
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    return state


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state", default="results/agent/state.json")
    p.add_argument("--date", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--commits", type=int, required=True)
    p.add_argument("--gate", required=True, help="ACCEPT | REJECT | NO-CHANGE | ERROR")
    p.add_argument("--tests", required=True, help="ok | FAIL | skipped")
    p.add_argument("--family", type=int, default=None)
    p.add_argument("--subgoal", default="")
    p.add_argument("--note", default="")
    p.add_argument("--question", action="append", default=[])
    a = p.parse_args()
    sp = Path(a.state)
    sp.parent.mkdir(parents=True, exist_ok=True)
    st = record(load(sp), date=a.date, branch=a.branch, commits=a.commits, gate=a.gate, tests=a.tests,
                family=a.family, subgoal=a.subgoal, note=a.note, questions=a.question)
    sp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    print(json.dumps(st["nights"][-1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
