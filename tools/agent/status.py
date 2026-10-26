"""One-screen orientation for the nightly agent (and for Josh in the morning).

    python tools/agent/status.py                      # from results/baseline/evaluation.json
    python tools/agent/status.py --baseline <file>    # any evaluation json
    python tools/agent/status.py --json               # machine-readable

Prints the commit, the baseline summary (mode, drop placement, sweep set), FAIL
counts per rule grouped by the planner-era rule families, the family to work on,
each track's failing rules, the latest sweep's serious findings, and the agent's
state file (results/agent/state.json) if present. Reads only; never evaluates.

Families (2026-10-25, the rekordbox planner era -- docs/AGENT_BRIEF.md):
  0  sweep findings       a crash / invariant / plan finding in the latest SWEEP.md beats everything
  1  drop placement       drop_offset_bars_mean (loop/metrics.py), STRUCT-03, TIME-04
  2  section shape        TIME-05, LOOK-05, TIME-06, STRUCT-04, STRUCT-05, STRUCT-06, STRUCT-07
  3  per-drop character   STRUCT-02, STRUCT-08, STRUCT-09
  4  generalisation       the marker-free rules over the USB sweep set
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

try:
    from livereact.loop.metrics import MARKER_FREE_RULES
except Exception:  # pragma: no cover - keeps the tool importable from odd cwds
    MARKER_FREE_RULES = ("TIME-11", "TIME-02", "TIME-13", "TIME-21", "LOOK-03", "SAFE-04", "SAFE-09")

FAMILIES: list[tuple[int, str, list[str]]] = [
    (0, "sweep findings (crash / invariant / plan sanity in the latest SWEEP.md)", []),
    (1, "drop placement (bars from the ear drops; roll start vs rekordbox chorus)", ["STRUCT-03", "TIME-04"]),
    (2, "section shape: build ramp + hold-back, breakdown strip, whiteout, power-off, fading build, groove",
     ["TIME-05", "LOOK-05", "TIME-06", "STRUCT-04", "STRUCT-05", "STRUCT-06", "STRUCT-07"]),
    (3, "per-drop character: escalation, weak roll, clap-driven", ["STRUCT-02", "STRUCT-08", "STRUCT-09"]),
    (4, "generalisation: marker-free rules over the USB sweep set", list(MARKER_FREE_RULES)),
]
SERIOUS_SWEEP_KINDS = ("crash", "invariant", "plan")

# Families Josh has closed: their remaining FAILs need a rule change or his input, not more engine
# work. A state.json `family` set explicitly to one of these reopens it. (The classifier-era closure
# of family 1 ended with the planner: drop placement is open again, measured differently.)
CLOSED: dict[int, str] = {}


def head() -> str:
    try:
        return subprocess.run(["git", "log", "--oneline", "-1"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:  # pragma: no cover - git missing
        return "?"


def fail_counts(ev: dict) -> dict[str, dict]:
    """rule -> {"tuning": n_fail, "holdout": n_fail, "sweep": n_fail, "tracks": [names]} over an evaluation json."""
    out: dict[str, dict] = {}
    for t in ev["tracks"]:
        for rule, status in t["verdicts"].items():
            d = out.setdefault(rule, {"tuning": 0, "holdout": 0, "sweep": 0, "tracks": []})
            if status == "FAIL":
                d["holdout" if t["holdout"] else "tuning"] += 1
                d["tracks"].append(t["track"])
    for t in ev.get("sweep", []):
        for rule, status in t["verdicts"].items():
            d = out.setdefault(rule, {"tuning": 0, "holdout": 0, "sweep": 0, "tracks": []})
            if status == "FAIL":
                d["sweep"] += 1
                d["tracks"].append(t["track"])
    return out


def family_table(ev: dict) -> list[dict]:
    counts = fail_counts(ev)
    rows = []
    seen: set[str] = set()
    for num, name, rules in FAMILIES:
        for r in rules:
            c = counts.get(r, {"tuning": 0, "holdout": 0, "sweep": 0, "tracks": []})
            # family 4 counts the sweep set; the scored set's marker-free fails count there too
            rows.append({"family": num, "name": name, "rule": r, "tuning": c["tuning"], "holdout": c["holdout"],
                         "sweep": c.get("sweep", 0),
                         "fail": c["tuning"] + c["holdout"] + (c.get("sweep", 0) if num == 4 else 0)})
            seen.add(r)
    for r in sorted(set(counts) - seen):
        c = counts[r]
        rows.append({"family": -1, "name": "other", "rule": r, "tuning": c["tuning"], "holdout": c["holdout"],
                     "sweep": c.get("sweep", 0), "fail": c["tuning"] + c["holdout"]})
    return rows


def latest_sweep(agent_dir: Path = ROOT / "results" / "agent") -> dict | None:
    """The newest results/agent/<date>/sweep.json, or None."""
    if not agent_dir.is_dir():
        return None
    cands = sorted(agent_dir.glob("*/sweep.json"))
    if not cands:
        return None
    try:
        return json.loads(cands[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sweep_serious(sweep: dict | None) -> int:
    if not sweep:
        return 0
    return sum(int(sweep.get("counts", {}).get(k, 0)) for k in SERIOUS_SWEEP_KINDS)


def next_family(rows: list[dict], state: dict | None = None, sweep: dict | None = None) -> int | None:
    """The family to work on: the state file's `family` when set; else 0 when the latest sweep has
    serious findings; else the lowest open family that still has failures."""
    if state and state.get("family") is not None:
        return int(state["family"])
    if sweep_serious(sweep):
        return 0
    for num, _, _ in FAMILIES:
        if num == 0 or num in CLOSED:
            continue
        if any(r["family"] == num and r["fail"] > 0 for r in rows):
            return num
    return None


def render(ev: dict, state: dict | None, baseline_path: str, sweep: dict | None = None) -> str:
    rows = family_table(ev)
    s = ev["summary"]
    L = [f"HEAD: {head()}",
         f"baseline: {baseline_path} (rules {ev.get('rules', '?')}, mode {ev.get('mode', 'classifier')}, "
         f"analysis {ev.get('analysis_fingerprint') or '-'}, generated {ev.get('generated', '?')})",
         f"tuning  n={s['tuning']['n']} compliance={s['tuning']['compliance']} critical_fails={s['tuning']['critical_fails']}"
         + (f" drop_offset_bars={s['tuning'].get('drop_offset_bars_mean')} missed={s['tuning'].get('drops_missed')}" if 'drop_offset_bars_mean' in s['tuning'] else ""),
         f"holdout n={s['holdout']['n']} compliance={s['holdout']['compliance']} critical_fails={s['holdout']['critical_fails']}"
         + (f" drop_offset_bars={s['holdout'].get('drop_offset_bars_mean')} missed={s['holdout'].get('drops_missed')}" if 'drop_offset_bars_mean' in s['holdout'] else "")]
    sw = s.get("sweep") or {}
    if sw.get("n"):
        L.append(f"sweep   n={sw['n']} compliance={sw['compliance']} critical_fails={sw['critical_fails']} (marker-free rules, USB sample {ev.get('sweep_set') or '-'})")
    if sweep is not None:
        L.append(f"latest SWEEP ({sweep.get('date', '?')}): {sweep.get('tracks_ran', '?')}/{sweep.get('tracks_total', '?')} tracks, "
                 + "  ".join(f"{k}={v}" for k, v in sweep.get("counts", {}).items()))
    L += ["", "FAIL counts per rule, by family:"]
    cur = None
    for r in rows:
        if r["family"] != cur:
            cur = r["family"]
            closed = f"  [CLOSED: {CLOSED[cur]}]" if cur in CLOSED else ""
            L.append(f"  family {cur}: {r['name']}{closed}" if cur >= 0 else "  other rules:")
        flag = "" if r["fail"] else "  (clean)"
        extra = f", sweep {r['sweep']}" if r.get("sweep") else ""
        L.append(f"    {r['rule']:9} FAIL {r['fail']:2}  (tuning {r['tuning']}, holdout {r['holdout']}{extra}){flag}")
    if not any(r["family"] == 0 for r in rows):
        L.append(f"  family 0: {FAMILIES[0][1]}  -> {sweep_serious(sweep)} serious finding(s) in the latest sweep")
    nf = next_family(rows, state, sweep)
    src = ("from state.json" if state and state.get("family") is not None
           else ("the latest sweep has serious findings" if nf == 0 else "lowest family with failures"))
    L += ["", f"NEXT FAMILY: {nf if nf is not None else 'none'}  ({src})", "", "per track (failing rules):"]
    for t in sorted(ev["tracks"], key=lambda t: (t["holdout"], t["track"])):
        fails = sorted(r for r, st in t["verdicts"].items() if st == "FAIL")
        drv = "d" if t.get("driven") else " "
        L.append(f"  {'H' if t['holdout'] else 'T'}{drv} {t['compliance']!s:6} {t['track'][:44]:44} {' '.join(fails)}")
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
    sweep = latest_sweep()
    if a.json:
        rows = family_table(ev)
        print(json.dumps({"head": head(), "summary": ev["summary"], "mode": ev.get("mode", "classifier"), "rules": rows,
                          "next_family": next_family(rows, state, sweep), "sweep_serious": sweep_serious(sweep),
                          "state": state}, indent=1))
    else:
        print(render(ev, state, str(bp), sweep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
