"""The Bugs & UI agent's gate: may this branch's UI / bug-fix commits stand?

    python tools/agent/gate_ui.py [--base harness] [--baseline results/agent/<date>/baseline_before.json]
                                  [--out results/agent/<date>/ui/gate_ui.json]

ACCEPT needs all of:
1. the UI test modules RUN (PySide6 present, nothing skipped) and pass, plus every test file the branch
   touched;
2. ``venue_check.py`` passes (the grid still drives the lights);
3. label overlap (``ui_snapshot.py``) is not worse on any rig than ``--base``, measured in this same run
   from a temporary worktree of ``--base`` (same machine, same fonts -- a stored number would drift);
4. no test file loses assert lines (a fix proven by deleting the check that caught it is not a fix);
5. when anything outside ``src/livereact/ui/``, ``tests/`` and ``docs/UI_BUGS.md`` changed: the
   lighting gate in no-regression mode (``gate_head.py --no-regress``) against ``--baseline``.

Exit 0 ACCEPT, 1 REJECT, 2 ERROR. Never writes the baseline. The orchestrator runs it after the
agent; the agent runs it before each commit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
UI_TEST_MODULES = ("tests/test_floorplan.py", "tests/test_console.py", "tests/test_visualizer.py",
                   "tests/test_fixture_test.py")
UI_ONLY = re.compile(r"^(src/livereact/ui/|tests/|docs/UI_BUGS\.md$)")


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def changed_files(base: str) -> list[str]:
    return [f for f in git("diff", "--name-only", f"{base}...HEAD").splitlines() if f.strip()]


def run_ui_tests(extra: list[str]) -> tuple[bool, str]:
    mods = list(UI_TEST_MODULES) + [f for f in extra if f.startswith("tests/test_") and f.endswith(".py")
                                    and (ROOT / f).exists() and f not in UI_TEST_MODULES]
    r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", *mods],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    ok = r.returncode == 0 and "skipped" not in tail and "passed" in tail
    return ok, tail if ok else (tail + "\n" + r.stdout[-1500:])


def assert_losses(base: str) -> list[str]:
    out = []
    for f in changed_files(base):
        if not (f.startswith("tests/") and f.endswith(".py")):
            continue
        diff = git("diff", f"{base}...HEAD", "--", f)
        removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---") and "assert" in ln)
        added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++") and "assert" in ln)
        if not (ROOT / f).exists():
            out.append(f"{f}: test file deleted")
        elif removed > added:
            out.append(f"{f}: {removed} assert lines removed, {added} added")
    return out


def overlap(src: Path | None) -> dict:
    cmd = [PY, str(ROOT / "tools" / "agent" / "ui_snapshot.py"), "--json", "--no-png"]
    if src is not None:
        cmd += ["--src", str(src)]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ui_snapshot failed: {r.stderr[-800:]}")
    return json.loads(r.stdout)


def overlap_vs_base(base: str) -> tuple[list[str], dict]:
    tmp = Path(tempfile.mkdtemp(prefix="gate_ui_"))
    wt = tmp / "base"
    try:
        git("worktree", "add", "--detach", str(wt), base)
        before, after = overlap(wt / "src"), overlap(None)
    finally:
        git("worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
    worse = []
    table = {}
    for rig, a in after.items():
        b = before.get(rig)
        score = lambda r: r["text_text"] + r["text_body"] + r["text_outside"]   # noqa: E731
        table[rig] = {"base": None if b is None else score(b), "head": score(a)}
        if b is not None and score(a) > score(b):
            worse.append(f"{rig}: label overlaps {score(b)} -> {score(a)} {a['examples']}")
    return worse, table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="harness")
    ap.add_argument("--baseline", default=str(ROOT / "results" / "baseline" / "evaluation.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    reasons: list[str] = []
    report: dict = {}
    try:
        files = changed_files(a.base)
        report["changed"] = files
        ok, tail = run_ui_tests(files)
        report["ui_tests"] = tail.splitlines()[0] if tail else ""
        if not ok:
            reasons.append(f"UI tests did not all run and pass: {tail[:600]}")
        vc = subprocess.run([PY, str(ROOT / "tools" / "agent" / "venue_check.py")], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
        report["venue_check"] = "OK" if vc.returncode == 0 else vc.stdout[-1200:]
        if vc.returncode != 0:
            reasons.append("venue_check failed: " + vc.stdout[-600:])
        worse, table = overlap_vs_base(a.base)
        report["overlap"] = table
        reasons += worse
        losses = assert_losses(a.base)
        report["assert_losses"] = losses
        reasons += losses
        non_ui = [f for f in files if not UI_ONLY.match(f)]
        report["non_ui_files"] = non_ui
        if non_ui:
            g = subprocess.run([PY, str(ROOT / "tools" / "agent" / "gate_head.py"), "--baseline", a.baseline,
                                "--no-regress"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            report["lighting_gate"] = g.stdout[-2000:]
            if g.returncode != 0:
                reasons.append(f"lighting gate (no-regress) {'REJECT' if g.returncode == 1 else 'ERROR'} for "
                               f"{non_ui}: " + g.stdout[-500:])
    except Exception as exc:  # noqa: BLE001 - a broken gate is an ERROR, never an ACCEPT
        print(f"GATE ERROR: {exc}")
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps({"gate": "ERROR", "error": str(exc), **report}, indent=1), encoding="utf-8")
        return 2
    verdict = "ACCEPT" if not reasons else "REJECT"
    print(f"UI tests: {report.get('ui_tests')}")
    print(f"venue check: {'OK' if report.get('venue_check') == 'OK' else 'FAILED'}")
    for rig, t in report.get("overlap", {}).items():
        print(f"overlap {rig:38} {t['base']} -> {t['head']}")
    print(f"\nGATE: {verdict}")
    for r in reasons:
        print(f"  - {r}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"gate": verdict, "reasons": reasons, **report}, indent=1,
                                          ensure_ascii=False), encoding="utf-8")
    return 0 if verdict == "ACCEPT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
