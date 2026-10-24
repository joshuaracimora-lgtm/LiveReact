"""One-screen orientation for the nightly engine agent (and for Josh in the morning).

    python tools/agent/status.py                      # from results/baseline/evaluation.json
    python tools/agent/status.py --baseline <file>    # any evaluation json
    python tools/agent/status.py --json               # machine-readable

Prints the commit, the baseline summary, FAIL counts per rule grouped by the
rule families in docs/HANDOFF_engine_work.md (in dependency order), the next
family that still has failures, each track's failing rules, and the agent's
state file (results/agent/state.json) if present. Reads only; never evaluates.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# docs/HANDOFF_engine_work.md, in dependency order. Rules not listed are "other".
FAMILIES: list[tuple[int, str, list[str]]] = [
    (1, "drop detection", ["STRUCT-03", "STRUCT-01"]),
    (2, "dwell and transitions", ["TIME-11", "TIME-02", "TIME-13"]),
    (3, "missing behaviours: pre-drop blackout, build ramp, breakdown strip", ["TIME-04", "TIME-05", "TIME-06"]),
    (4, "Josh's vocabulary: power-off, groove, whiteout, escalation, weak roll, fading build, clap-driven",
     ["STRUCT-05", "STRUCT-07", "STRUCT-04", "STRUCT-02", "STRUCT-08", "STRUCT-06", "STRUCT-09"]),
]


def head() -> str:
    try:
        return subprocess.run(["git", "log", "--oneline", "-1"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:  # pragma: no cover - git missing
        return "?"


def fail_counts(ev: dict) -> dict[str, dict]:
    """rule -> {"tuning": n_fail, "holdout": n_fail, "tracks": [names]} over an evaluation json."""
    out: dict[str, dict] = {}
    for t in ev["tracks"]:
        for rule, status in t["verdicts"].items():
            d = out.setdefault(rule, {"tuning": 0, "holdout": 0, "tracks": []})
            if status == "FAIL":
                d["holdout" if t["holdout"] else "tuning"] += 1
                d["tracks"].append(t["track"])
    return out


def family_table(ev: dict) -> list[dict]:
    counts = fail_counts(ev)
    rows = []
    seen: set[str] = set()
    for num, name, rules in FAMILIES:
        for r in rules:
            c = counts.get(r, {"tuning": 0, "holdout": 0, "tracks": []})
            rows.append({"family": num, "name": name, "rule": r, "tuning": c["tuning"], "holdout": c["holdout"],
                         "fail": c["tuning"] + c["holdout"]})
            seen.add(r)
    for r in sorted(set(counts) - seen):
        c = counts[r]
        rows.append({"family": 0, "name": "other", "rule": r, "tuning": c["tuning"], "holdout": c["holdout"],
                     "fail": c["tuning"] + c["holdout"]})
    return rows


# Families Josh has closed: their remaining FAILs need a rule change or his input, not more engine
# work. A state.json `family` set explicitly to one of these reopens it.
CLOSED: dict[int, str] = {
    1: "closed by Josh 2026-10-22 (commit c4e2159): the remaining STRUCT-03/STRUCT-01 cases (SWEETER clap "
       "drops, messy 128.31, One More Time 232.55, Latch 64.14, Sweet Nothing 22.21; fills vs cut-then-drop) "
       "are not separable by any audio rule",
}


def next_family(rows: list[dict], state: dict | None = None) -> int | None:
    """The family to work on: the state file's `family` when set, else the lowest
    family that still has failures and is not in CLOSED."""
    if state and state.get("family") is not None:
        return int(state["family"])
    for num, _, _ in FAMILIES:
        if num in CLOSED:
            continue
        if any(r["family"] == num and r["fail"] > 0 for r in rows):
            return num
    return None


def render(ev: dict, state: dict | None, baseline_path: str) -> str:
    rows = family_table(ev)
    s = ev["summary"]
    L = [f"HEAD: {head()}", f"baseline: {baseline_path} (rules {ev.get('rules', '?')}, generated {ev.get('generated', '?')})",
         f"tuning  n={s['tuning']['n']} compliance={s['tuning']['compliance']} critical_fails={s['tuning']['critical_fails']}",
         f"holdout n={s['holdout']['n']} compliance={s['holdout']['compliance']} critical_fails={s['holdout']['critical_fails']}",
         "", "FAIL counts per rule (tuning+holdout tracks), by family in dependency order:"]
    cur = None
    for r in rows:
        if r["family"] != cur:
            cur = r["family"]
            closed = f"  [CLOSED: {CLOSED[cur]}]" if cur in CLOSED else ""
            L.append(f"  family {cur}: {r['name']}{closed}" if cur else "  other rules:")
        flag = "" if r["fail"] else "  (clean)"
        L.append(f"    {r['rule']:9} FAIL {r['fail']:2}  (tuning {r['tuning']}, holdout {r['holdout']}){flag}")
    nf = next_family(rows, state)
    src = "from state.json" if state and state.get("family") is not None else "lowest family with failures"
    L += ["", f"NEXT FAMILY: {nf if nf else 'none'}  ({src})", "", "per track (failing rules):"]
    for t in sorted(ev["tracks"], key=lambda t: (t["holdout"], t["track"])):
        fails = sorted(r for r, st in t["verdicts"].items() if st == "FAIL")
        L.append(f"  {'H' if t['holdout'] else 'T'} {t['compliance']!s:6} {t['track'][:44]:44} {' '.join(fails)}")
    L.append("")
    if state:
        L.append("agent state (results/agent/state.json):")
        L.append(json.dumps(state, indent=1))
    else:
        L.append("agent state: none yet (results/agent/state.json missing)")
    return "\n".join(L)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--baseline", default="results/baseline/evaluation.json")
    p.add_argument("--state", default="results/agent/state.json")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    bp = Path(a.baseline)
    if not bp.exists():
        print(f"no baseline at {bp}; run tools/loop/baseline.py first")
        return 1
    ev = json.loads(bp.read_text(encoding="utf-8"))
    state = json.loads(Path(a.state).read_text(encoding="utf-8")) if Path(a.state).exists() else None
    if a.json:
        rows = family_table(ev)
        print(json.dumps({"head": head(), "summary": ev["summary"], "rules": rows, "next_family": next_family(rows, state),
                          "state": state}, indent=1))
    else:
        print(render(ev, state, str(bp)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
