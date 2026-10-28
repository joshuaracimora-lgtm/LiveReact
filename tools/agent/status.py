"""One-screen orientation for the nightly agent (and for Josh in the morning).

    python tools/agent/status.py                      # from results/baseline/evaluation.json
    python tools/agent/status.py --baseline <file>    # any evaluation json
    python tools/agent/status.py --json               # machine-readable
    python tools/agent/status.py --role ui            # the Bugs & UI agent's screen (2026-10-26)
    python tools/agent/status.py --role app           # the App agent's screen (2026-10-27)

Prints the commit, the baseline summary (mode, drop placement, sweep set), FAIL
counts per rule grouped by the planner-era rule families, the family to work on,
each track's failing rules, the latest sweep's serious findings, and the agent's
state file (results/agent/state.json) if present. Reads only; never evaluates.

Families (2026-10-25, the rekordbox planner era -- docs/AGENT_BRIEF.md):
  0  sweep findings       a crash / invariant / plan finding in the latest SWEEP.md: the Bugs & UI
                          agent's (docs/AGENT_BRIEF_UI.md) since 2026-10-26, never the Track Classifier's
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


UI_BUGS = ROOT / "docs" / "UI_BUGS.md"
APP_ROADMAP = ROOT / "docs" / "APP_ROADMAP.md"
# roadmap items the App agent skips (docs/APP_ROADMAP.md): Josh's, score-moving, or waiting on another item
APP_SKIP_TAGS = ("[JOSH]", "[SHOW-CHANGE]", "[BLOCKED")


def open_items(path: Path) -> list[str]:
    """The numbered items under '## Open...' in a work list (first line of each)."""
    if not path.exists():
        return []
    out, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line.strip().startswith("## Open")
            continue
        if inside and line[:1].isdigit() and ". " in line[:5]:
            out.append(line.split(". ", 1)[1].strip())
    return out


def open_ui_bugs(path: Path = UI_BUGS) -> list[str]:
    """The numbered items under '## Open' in docs/UI_BUGS.md (first line of each)."""
    return open_items(path)


def app_items(path: Path = APP_ROADMAP) -> tuple[list[str], list[str]]:
    """(actionable, waiting) open roadmap items; waiting = tagged for Josh, a show change, or blocked."""
    items = open_items(path)
    waiting = [i for i in items if any(t in i for t in APP_SKIP_TAGS)]
    return [i for i in items if i not in waiting], waiting


def next_family(rows: list[dict], state: dict | None = None, sweep: dict | None = None,
                role: str = "tracks") -> int | None:
    """The family to work on.

    Track Classifier ("tracks"): the state file's `family` when set, else the lowest open family >= 1
    that still has failures -- never 0. Bugs & UI ("ui"): 0 while the latest sweep has serious
    findings (crash / invariant / plan), else None = work docs/UI_BUGS.md."""
    if role == "ui":
        return 0 if sweep_serious(sweep) else None
    if state and state.get("family") is not None:
        return int(state["family"])
    for num, _, _ in FAMILIES:
        if num == 0 or num in CLOSED:
            continue
        if any(r["family"] == num and r["fail"] > 0 for r in rows):
            return num
    return None


def _role_view(state: dict | None, role: str) -> dict | None:
    """The state a role reads: tracks = the top level (without roles.*), ui/app = roles.<role>; that role's nights."""
    if not state:
        return None
    if role in ("ui", "app"):
        view = dict((state.get("roles") or {}).get(role) or {})
    else:
        view = {k: v for k, v in state.items() if k not in ("roles", "nights", "updated")}
    view["nights"] = [n for n in state.get("nights", []) if (n.get("role") or "tracks") == role][-5:]
    return view


def render_ui(state: dict | None, sweep: dict | None) -> str:
    view = _role_view(state, "ui") or {}
    bugs = open_ui_bugs()
    nf = next_family([], state, sweep, role="ui")
    L = [f"HEAD: {head()}", "Bugs & UI agent (docs/AGENT_BRIEF_UI.md)"]
    if sweep is not None:
        L.append(f"latest SWEEP ({sweep.get('date', '?')}): {sweep.get('tracks_ran', '?')}/{sweep.get('tracks_total', '?')} "
                 "tracks, " + "  ".join(f"{k}={v}" for k, v in sweep.get("counts", {}).items()))
        for f in [f for f in sweep.get("findings", []) if str(f.get("kind", "")) in SERIOUS_SWEEP_KINDS][:8]:
            L.append(f"  {f.get('kind')}: {str(f.get('track', ''))[:40]}  {str(f.get('detail', f.get('code', '')))[:100]}")
    L.append(f"open UI bugs (docs/UI_BUGS.md): {len(bugs)}")
    for i, b in enumerate(bugs[:5], 1):
        L.append(f"  {i}. {b[:110]}")
    L += ["", "NEXT: " + ("family 0 -- fix the sweep's serious findings first" if nf == 0
                          else (f"UI bug 1: {bugs[0][:90]}" if bugs else "nothing open: report and stop")), ""]
    L.append("agent state (results/agent/state.json, roles.ui):")
    L.append(json.dumps(view, indent=1, ensure_ascii=False))
    return "\n".join(L)


def render_app(state: dict | None) -> str:
    view = _role_view(state, "app") or {}
    todo, waiting = app_items()
    L = [f"HEAD: {head()}", "App agent (docs/AGENT_BRIEF_APP.md, work list docs/APP_ROADMAP.md)",
         f"actionable roadmap items: {len(todo)}   waiting on Josh / blocked: {len(waiting)}"]
    for i, item in enumerate(todo[:5], 1):
        L.append(f"  {i}. {item[:110]}")
    if waiting:
        L.append("waiting:")
        L += [f"  - {w[:110]}" for w in waiting[:8]]
    L += ["", "NEXT: " + (f"{todo[0][:100]}" if todo else "nothing actionable: report and stop"), ""]
    L.append("agent state (results/agent/state.json, roles.app):")
    L.append(json.dumps(view, indent=1, ensure_ascii=False))
    return "\n".join(L)


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
        L.append(f"  family 0: {FAMILIES[0][1]}  -> {sweep_serious(sweep)} serious finding(s) in the latest sweep"
                 " (the Bugs & UI agent's)")
    nf = next_family(rows, state, sweep)
    src = "from state.json" if state and state.get("family") is not None else "lowest family with failures"
    L += ["", f"NEXT FAMILY: {nf if nf is not None else 'none'}  ({src})", "", "per track (failing rules):"]
    for t in sorted(ev["tracks"], key=lambda t: (t["holdout"], t["track"])):
        fails = sorted(r for r, st in t["verdicts"].items() if st == "FAIL")
        drv = "d" if t.get("driven") else " "
        L.append(f"  {'H' if t['holdout'] else 'T'}{drv} {t['compliance']!s:6} {t['track'][:44]:44} {' '.join(fails)}")
    L.append("")
    if state:
        L.append("agent state (results/agent/state.json, the Track Classifier's part):")
        L.append(json.dumps(_role_view(state, "tracks"), indent=1, ensure_ascii=False))
    else:
        L.append("agent state: none yet (results/agent/state.json missing)")
    return "\n".join(L)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--baseline", default="results/baseline/evaluation.json")
    p.add_argument("--state", default="results/agent/state.json")
    p.add_argument("--json", action="store_true")
    p.add_argument("--role", choices=("tracks", "ui", "app"), default="tracks")
    a = p.parse_args()
    if a.role == "app":
        state = json.loads(Path(a.state).read_text(encoding="utf-8")) if Path(a.state).exists() else None
        if a.json:
            todo, waiting = app_items()
            print(json.dumps({"head": head(), "actionable": todo, "waiting": waiting,
                              "state": _role_view(state, "app")}, indent=1, ensure_ascii=False))
        else:
            print(render_app(state))
        return 0
    if a.role == "ui":
        state = json.loads(Path(a.state).read_text(encoding="utf-8")) if Path(a.state).exists() else None
        sweep = latest_sweep()
        if a.json:
            print(json.dumps({"head": head(), "next_family": next_family([], state, sweep, role="ui"),
                              "sweep_serious": sweep_serious(sweep), "open_ui_bugs": open_ui_bugs(),
                              "state": _role_view(state, "ui")}, indent=1, ensure_ascii=False))
        else:
            print(render_ui(state, sweep))
        return 0
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
