"""Append one night's outcome to results/agent/state.json (the agent's memory across nights).

    python tools/agent/record_night.py --date 20261024 --branch agent/20261024 --commits 1 \
        --gate ACCEPT --tests ok --family 2 --note "TIME-11 dwell in bars: 6 tracks flipped"

state.json shape:
  {"family": <int|null>,            # the family the agent is working on (status.py's next_family if null)
   "subgoal": "<free text>",         # what to continue tomorrow
   "open_questions": ["..."],        # things only Josh can answer; the agent appends, Josh clears
   "nights": [{"date","branch","role","commits","gate","tests","family","note"}],
   "roles": {"ui": {...}, "app": {...}}}       # each {"family", "subgoal", "open_questions"}

Three agents run each night (2026-10-27): the Track Classifier (role "tracks") keeps the top-level
family/subgoal/open_questions, as before; the Bugs & UI agent (role "ui") and the App agent (role
"app") have their own under roles.<role>, so none can steer another's next night.
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


ROLES = ("tracks", "ui", "app")


def role_state(state: dict, role: str) -> dict:
    """The dict holding a role's family / subgoal / open_questions (the top level for "tracks")."""
    if role == "tracks":
        return state
    return state.setdefault("roles", {}).setdefault(role, {"family": None, "subgoal": "", "open_questions": []})


def record(state: dict, **night) -> dict:
    """Merge one record into the night keyed by (date, branch).

    Both the agent (family, subgoal, its own note) and the orchestrator (verified
    commits/gate/tests) record the same night; neither may erase the other's
    fields. Non-empty values win, notes are concatenated.
    """
    questions = night.pop("questions", None) or []
    role = night.get("role") or "tracks"
    night["role"] = role
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
    rs = role_state(state, role)
    if night.get("family") is not None:
        rs["family"] = night["family"]
    if night.get("subgoal"):
        rs["subgoal"] = night["subgoal"]
    for q in questions:
        if q not in rs.setdefault("open_questions", []):
            rs["open_questions"].append(q)
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
    p.add_argument("--role", choices=ROLES, default="tracks")
    a = p.parse_args()
    sp = Path(a.state)
    sp.parent.mkdir(parents=True, exist_ok=True)
    st = record(load(sp), date=a.date, branch=a.branch, role=a.role, commits=a.commits, gate=a.gate,
                tests=a.tests, family=a.family, subgoal=a.subgoal, note=a.note, questions=a.question)
    sp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(st["nights"][-1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
