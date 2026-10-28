"""The App agent's gate: may this branch's app-building commits stand?

    python tools/agent/gate_app.py [--base harness] [--baseline results/agent/<date>/baseline_before.json]
                                   [--out results/agent/<date>/app/gate_<n>.json]

The App agent (docs/AGENT_BRIEF_APP.md) builds the publishable app around the show: fixture definitions,
rig manager, presets, packaging. Its one promise is that the show does not change. ACCEPT needs all of:
1. every ``tests/test_app_*.py`` plus every test file the branch touched RUN (nothing skipped) and pass,
   with ``LIVEREACT_OFFLINE=1`` (tests/conftest.py: no internet, no Anthropic key);
2. no test file loses assert lines or is deleted;
3. when ``src/livereact/ui/`` changed: the Bugs & UI gate's checks -- the UI test modules, ``venue_check``,
   label overlap not worse than ``--base`` (tools/agent/gate_ui.py);
4. when any other file under ``src/`` changed: the show is unchanged -- ``golden_frames.py --base`` once it
   exists (docs/APP_ROADMAP.md item 1), until then the lighting gate in no-regression mode
   (``gate_head.py --no-regress``) against ``--baseline``.

Exit 0 ACCEPT, 1 REJECT, 2 ERROR. Never writes the baseline. The orchestrator runs it after the agent;
the agent runs it before each commit.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_ui  # noqa: E402  (same folder; reuses its git / UI / overlap helpers)

PY = sys.executable
# the checks print track names (non-ASCII); a cp1252 pipe crashed gate_head mid-run and read as a REJECT.
# The orchestrator sets these (common.ps1); set them here too so the agent's own runs behave the same.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")


def app_test_files(changed: list[str]) -> list[str]:
    """tests/test_app_*.py plus the test modules the branch touched (that still exist)."""
    mods = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "tests").glob("test_app_*.py"))
    for f in changed:
        if f.startswith("tests/test_") and f.endswith(".py") and (ROOT / f).exists() and f not in mods:
            mods.append(f)
    return mods


def run_offline_tests(mods: list[str]) -> tuple[bool, str]:
    if not mods:
        return True, "no app tests yet"
    env = dict(os.environ, LIVEREACT_OFFLINE="1")
    env.pop("ANTHROPIC_API_KEY", None)
    r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", *mods],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    # the last "N passed" line, not the last line: Qt can print after pytest's summary
    lines = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln or "no tests ran" in ln]
    tail = lines[-1].strip() if lines else (r.stdout.strip().splitlines() or [""])[-1]
    ok = r.returncode == 0 and "skipped" not in tail and "passed" in tail
    return ok, tail if ok else (tail + "\n" + r.stdout[-1500:])


def is_ui(f: str) -> bool:
    return f.startswith("src/livereact/ui/")


def touches_show_code(f: str) -> bool:
    """A source file outside ui/: anything the show or the simulator could import."""
    return f.startswith("src/") and not is_ui(f)


def show_unchanged(base: str, baseline: str) -> tuple[bool, str, str]:
    """(ok, which check ran, output tail)."""
    golden = ROOT / "tools" / "agent" / "golden_frames.py"
    if golden.exists():
        cmd, name = [PY, str(golden), "--base", base], "golden frames"
    else:
        cmd, name = [PY, str(ROOT / "tools" / "agent" / "gate_head.py"), "--baseline", baseline, "--no-regress"], \
            "lighting gate (no-regress)"
    g = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if g.returncode not in (0, 1):
        raise RuntimeError(f"{name} errored: {g.stdout[-600:]} {g.stderr[-600:]}")
    return g.returncode == 0, name, g.stdout[-2000:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="harness")
    ap.add_argument("--baseline", default=str(ROOT / "results" / "baseline" / "evaluation.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    reasons: list[str] = []
    report: dict = {}
    try:
        files = gate_ui.changed_files(a.base)
        report["changed"] = files
        mods = app_test_files(files)
        ok, tail = run_offline_tests(mods)
        report["app_tests"] = {"modules": mods, "result": tail.splitlines()[0] if tail else ""}
        if not ok:
            reasons.append(f"app tests did not all run and pass offline: {tail[:600]}")
        losses = gate_ui.assert_losses(a.base)
        report["assert_losses"] = losses
        reasons += losses
        if any(is_ui(f) for f in files):
            ok, tail = gate_ui.run_ui_tests(files)
            report["ui_tests"] = tail.splitlines()[0] if tail else ""
            if not ok:
                reasons.append(f"UI tests did not all run and pass: {tail[:600]}")
            vc = subprocess.run([PY, str(ROOT / "tools" / "agent" / "venue_check.py")], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
            report["venue_check"] = "OK" if vc.returncode == 0 else vc.stdout[-1200:]
            if vc.returncode != 0:
                reasons.append("venue_check failed: " + vc.stdout[-600:])
            worse, table = gate_ui.overlap_vs_base(a.base)
            report["overlap"] = table
            reasons += worse
        show_files = [f for f in files if touches_show_code(f)]
        report["show_path_files"] = show_files
        if show_files:
            ok, name, out = show_unchanged(a.base, a.baseline)
            report["show_check"] = {"check": name, "ok": ok, "output": out}
            if not ok:
                reasons.append(f"{name} REJECT for {show_files}: " + out[-500:])
    except Exception as exc:  # noqa: BLE001 - a broken gate is an ERROR, never an ACCEPT
        print(f"GATE ERROR: {exc}")
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps({"gate": "ERROR", "error": str(exc), **report}, indent=1), encoding="utf-8")
        return 2
    verdict = "ACCEPT" if not reasons else "REJECT"
    print(f"app tests ({len(report['app_tests']['modules'])} modules, offline): {report['app_tests']['result']}")
    if "ui_tests" in report:
        print(f"UI tests: {report['ui_tests']}")
        print(f"venue check: {'OK' if report.get('venue_check') == 'OK' else 'FAILED'}")
        for rig, t in report.get("overlap", {}).items():
            print(f"overlap {rig:38} {t['base']} -> {t['head']}")
    if "show_check" in report:
        print(f"show unchanged ({report['show_check']['check']}): {'yes' if report['show_check']['ok'] else 'NO'}")
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
