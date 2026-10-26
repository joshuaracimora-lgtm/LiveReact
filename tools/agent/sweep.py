"""Nightly bug / regression / invariant sweep over EVERY track on the USB export copy.

    python tools/agent/sweep.py [--usb usb_export] [--minutes 40] [--out results/agent/<date>] [--limit N]
                                [--order rotate|name] [--rig rigs/josh_rig.json] [--fuzz-ext DIR]

Runs each track through the real pipeline in drive mode with its own plan and reports, per track:
crashes (any exception), WARNING+ log records, marker-free scorer rules (dwell, look hold, legal
transitions, restraint, static cut, strobe safety -- the rules that need no ear markers), plan
sanity (``plan_checks``) and plan invariants (no laser or strobe where the plan forbids them).
Scored tracks (ear timelines) go first, the rest rotate by date so a short budget covers a
different slice each night. Writes ``sweep.json`` and ``SWEEP.md``. Exit 0 clean, 1 findings
(crash / invariant / rule / plan), 2 no export or tool error. The nightly agent reads SWEEP.md
first: a crash or invariant finding is family 0 and beats every other sub-goal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

KINDS_ORDER = ("crash", "invariant", "rule", "plan", "warning", "info")


def plan_checks(plan, look, rows: list[dict] | None) -> list[str]:
    """Pure sanity checks on a TrackPlan (+ the run's rows when available). Returns finding codes."""
    out: list[str] = []
    if plan is None:
        return ["no_plan"]
    secs = list(plan.sections)
    if not secs:
        return ["empty_plan"]
    no_drop_mood = bool(look is not None and getattr(look, "no_drop", False))
    drops = [s for s in secs if s.kind == "drop"]
    if not drops and not no_drop_mood:
        out.append("no_drop_section")
    if drops and plan.big_moment_index is None and not no_drop_mood and not (look is not None and look.big_moment == "none"):
        out.append("no_big_moment")
    for i, s in enumerate(secs):
        if 0 < i < len(secs) - 1 and (s.end_beat - s.beat) < 16:
            out.append(f"short_interior_section:{s.label}@{s.beat}")
        if i and s.beat < secs[i - 1].end_beat:
            out.append(f"overlap:{s.label}@{s.beat}")
        if s.end_beat <= s.beat:
            out.append(f"empty_section:{s.label}@{s.beat}")
    if rows:
        active = [bool(r.get("plan_active")) for r in rows]
        frac = sum(active) / len(active)
        if frac < 0.5:
            out.append(f"plan_inactive:{frac:.2f}")
    return out


def invariant_checks(run, rows: list[dict]) -> list[str]:
    """Plan-vs-frames invariants: nothing the plan forbade may have reached the fixtures."""
    out: list[str] = []
    n = min(len(rows), run.n)
    laser_bad = strobe_bad = 0
    for i in range(n):
        r = rows[i]
        if not r.get("plan_active"):
            continue
        if run.laser_any[i] and r.get("laser_allowed") is False:
            laser_bad += 1
        if run.strobe_any[i] and r.get("strobe_allowed") is False:
            strobe_bad += 1
    if laser_bad:
        out.append(f"laser_where_forbidden:{laser_bad}_ticks")
    if strobe_bad:
        out.append(f"strobe_where_forbidden:{strobe_bad}_ticks")
    return out


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.levelname} {record.name}: {record.getMessage()}"[:300])


def fuzz_ext(root: Path) -> list[dict]:
    from livereact.rekordbox.pssi import parse_pqtz, parse_pssi
    from livereact.rekordbox.waveform import parse_pwv5
    findings = []
    n = 0
    for p in sorted(root.rglob("ANLZ*.*")):
        if p.suffix.upper() not in (".DAT", ".EXT"):
            continue
        n += 1
        try:
            data = p.read_bytes()
            if p.suffix.upper() == ".EXT":
                parse_pssi(data)
                parse_pwv5(data)
            else:
                parse_pqtz(data)
        except Exception:  # noqa: BLE001
            findings.append({"kind": "crash", "track": str(p), "detail": traceback.format_exc()[-400:]})
    findings.append({"kind": "info", "track": str(root), "detail": f"fuzz: {n} analysis files parsed, {len(findings)} crashed"})
    return findings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--usb", default=None)
    p.add_argument("--minutes", type=float, default=40.0)
    p.add_argument("--out", default=None, help="default results/agent/<date>")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--order", choices=("rotate", "name"), default="rotate")
    p.add_argument("--rig", default="rigs/josh_rig.json")
    p.add_argument("--tracks", default="tracks")
    p.add_argument("--fuzz-ext", default=None, help="also parse every ANLZ file under DIR (e.g. a rekordbox share folder)")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = p.parse_args()

    from livereact.fixtures.rig import load_rig
    from livereact.loop.evaluate import ear_timeline_for, is_ear_verified, labeled_tracks, resolve_usb
    from livereact.loop.metrics import MARKER_FREE_RULES
    from livereact.rekordbox.look_notes import load_look_notes, look_notes_path_for
    from livereact.rekordbox.planner import plan_track
    from livereact.sim.harness import default_seed_range, run_sim
    from livereact.sim.score import Run, score_run
    from livereact.sim.timeline import Timeline

    out_dir = Path(a.out) if a.out else ROOT / "results" / "agent" / a.date
    out_dir.mkdir(parents=True, exist_ok=True)
    usb = resolve_usb(a.usb)
    if usb is None:
        print("no rekordbox export (usb_export/ or $LIVEREACT_USB); nothing to sweep")
        return 2
    rig_obj = load_rig(a.rig)
    findings: list[dict] = []
    per_track: list[dict] = []
    if a.fuzz_ext:
        findings += fuzz_ext(Path(a.fuzz_ext))

    scored_paths = labeled_tracks(a.tracks, usb)
    scored_names = {p.name for p in scored_paths}
    tracks = usb.tracks()
    scored = [t for t in tracks if t.name in scored_names]
    rest = [t for t in tracks if t.name not in scored_names]
    if a.order == "rotate":
        salt = a.date
        rest.sort(key=lambda t: hashlib.sha256((salt + t.name).encode("utf-8")).hexdigest())
    else:
        rest.sort(key=lambda t: t.name.lower())
    order = scored + rest
    if a.limit:
        order = order[: a.limit]
    deadline = time.monotonic() + a.minutes * 60.0
    cap = _Capture()
    logging.getLogger("livereact").addHandler(cap)
    ran = 0
    for t in order:
        if time.monotonic() > deadline:
            break
        t0 = time.monotonic()
        rec = {"track": t.name, "id": t.info.id, "scored": t.name in scored_names, "driven": False,
               "marker_free_compliance": None, "drops": 0, "big_moment_t": None, "plan_active_pct": None, "secs": 0.0,
               "findings": []}
        cap.records.clear()
        try:
            if not t.complete:
                missing = [k for k, v in (("audio", t.audio), ("dat", t.dat), ("ext", t.ext)) if v is None]
                rec["findings"].append(f"info:missing_{'_'.join(missing)}")
                per_track.append(rec)
                continue
            table, grid, wave = usb.load_analysis(t)
            for k, v in (("no_pssi", table), ("no_grid", grid), ("no_waveform", wave)):
                if v is None:
                    rec["findings"].append(f"info:{k}")
            look = None
            try:
                look = load_look_notes(look_notes_path_for(t.stem, a.tracks))
            except ValueError as exc:
                rec["findings"].append(f"plan:invalid_look_notes:{str(exc)[:80]}")
            if rec["scored"]:
                tl = ear_timeline_for(t.audio, usb, a.tracks)
            else:
                tl = usb.timeline(t, grid)
            r = run_sim(t.audio, tl, a.rig, seed_range=default_seed_range(), phrases=table, drive=table is not None,
                        grid=grid, waveform=wave, track_id=t.info.id, look=look)
            rec["driven"] = table is not None
            plan = plan_track(table, track_id=t.info.id, title=t.info.title, bpm=tl.bpm, grid=grid, waveform=wave,
                              look=look) if table is not None else None
            run = Run(r.frames, r.t, r.signals, tl, rig_obj)
            sc = score_run(run)
            mf = [v for v in sc["verdicts"] if v["rule"] in MARKER_FREE_RULES]
            scorable = [v for v in mf if v["status"] in ("PASS", "FAIL")]
            if scorable:
                rec["marker_free_compliance"] = round(sum(1 for v in scorable if v["status"] == "PASS") / len(scorable), 3)
            for v in mf:
                if v["status"] == "FAIL":
                    rec["findings"].append(f"rule:{v['rule']}:{v['severity']}:" + "; ".join(str(e[1]) for e in v["evidence"][:2]))
            if plan is not None:
                rec["drops"] = len(plan.drops)
                if plan.big_moment_index is not None:
                    from livereact.rekordbox.pssi import beat_to_seconds
                    try:
                        rec["big_moment_t"] = round(beat_to_seconds(plan.sections[plan.big_moment_index].beat, grid, bpm=tl.bpm), 1)
                    except ValueError:
                        pass
                rows = r.signals
                if rows and "plan_active" in rows[0]:
                    rec["plan_active_pct"] = round(100 * sum(1 for x in rows if x["plan_active"]) / len(rows))
                for c in plan_checks(plan, look, rows if rows and "plan_active" in rows[0] else None):
                    rec["findings"].append(f"plan:{c}")
                for c in invariant_checks(run, rows):
                    rec["findings"].append(f"invariant:{c}")
            for w in cap.records:
                rec["findings"].append(f"warning:{w}")
        except Exception:  # noqa: BLE001 - the whole point: record it and carry on
            rec["findings"].append("crash:" + traceback.format_exc()[-600:].replace("\n", " | "))
        rec["secs"] = round(time.monotonic() - t0, 1)
        per_track.append(rec)
        ran += 1
        for f in rec["findings"]:
            kind, _, detail = f.partition(":")
            findings.append({"kind": kind, "track": t.name, "detail": detail})
    logging.getLogger("livereact").removeHandler(cap)

    counts = {k: sum(1 for f in findings if f["kind"] == k) for k in KINDS_ORDER}
    serious = counts["crash"] + counts["invariant"] + counts["rule"] + counts["plan"]
    result = {"date": a.date, "usb": str(usb.root), "tracks_total": len(tracks), "tracks_ran": ran,
              "tracks_scored": len(scored), "minutes": a.minutes, "counts": counts, "findings": findings,
              "tracks": per_track, "coverage": [t.name for t in order[:ran]]}
    (out_dir / "sweep.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    L = [f"# Sweep {a.date} -- {ran}/{len(tracks)} USB tracks in {a.minutes:.0f} min budget "
         f"({len(scored)} scored first, then rotating)", "",
         "counts: " + "  ".join(f"{k}={v}" for k, v in counts.items()), ""]
    if serious:
        L.append("## Findings (crash > invariant > rule > plan)")
        for kind in ("crash", "invariant", "rule", "plan"):
            for f in findings:
                if f["kind"] == kind:
                    L.append(f"- **{kind}** `{f['track'][:50]}`: {f['detail'][:300]}")
        L.append("")
    if counts["warning"]:
        L.append("## Warnings (log records at WARNING or above)")
        seen = set()
        for f in findings:
            if f["kind"] == "warning" and f["detail"] not in seen:
                seen.add(f["detail"])
                L.append(f"- `{f['track'][:40]}`: {f['detail'][:200]}")
        L.append("")
    L += ["## Per track", "", "| track | scored | driven | marker-free | drops | big moment | plan active | secs | findings |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in per_track:
        L.append(f"| {r['track'][:40]} | {'EAR' if r['scored'] else ''} | {'y' if r['driven'] else '-'} | "
                 f"{r['marker_free_compliance'] if r['marker_free_compliance'] is not None else '-'} | {r['drops']} | "
                 f"{r['big_moment_t'] if r['big_moment_t'] is not None else '-'} | "
                 f"{str(r['plan_active_pct']) + '%' if r['plan_active_pct'] is not None else '-'} | {r['secs']} | "
                 f"{len([f for f in r['findings'] if not f.startswith('info')])} |")
    (out_dir / "SWEEP.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:12]))
    print(f"-> {out_dir / 'SWEEP.md'}")
    return 1 if serious else 0


if __name__ == "__main__":
    raise SystemExit(main())
