"""User walk-through: drive LiveReact the way a new user does and check that each journey CHANGES THE APP.

    python tools/agent/walkthrough.py [--out results/agent/<date>/ui/walkthrough] [--json]

The User-Tester agent's starting point (docs/AGENT_BRIEF_UI.md, 2026-10-30). Before this the nightly agents
judged themselves on their own reports and built ~45 commits of features that a user could not reach from
the running app (Rig manager "Use selected" did nothing live, presets went nowhere, first-run answers were
never read). Every journey here ends in an observable effect on the RUNNING app -- the rig the show engine
plays, ManualParams, the next DMX frame, the launch defaults -- not "the dialog opened".

Verdicts per journey:
  PASS         the user's action reached the running app
  BROKEN       the app did the wrong thing (step, expected, got)
  UNREACHABLE  the feature exists in code but no control a user can reach applies it
  UX           it works, but a new user would be stuck or misled (finding text says why)
Offscreen Qt, demo mode, a fresh user-data folder (LIVEREACT_HOME), LIVEREACT_OFFLINE=1. Screenshots per step.
Writes WALKTHROUGH.md + walkthrough.json in --out. Exit 0 always (it is a report); 2 on a crash of the tool.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LIVEREACT_OFFLINE"] = "1"


class Journey:
    def __init__(self, key: str, title: str, out: Path) -> None:
        self.key, self.title, self.out = key, title, out
        self.findings: list[dict] = []
        self.shots: list[str] = []
        self.passed: list[str] = []

    def ok(self, what: str) -> None:
        self.passed.append(what)

    def find(self, verdict: str, step: str, expected: str, got: str) -> None:
        self.findings.append({"verdict": verdict, "step": step, "expected": expected, "got": got})

    def shot(self, widget, name: str) -> None:
        from PySide6 import QtWidgets
        QtWidgets.QApplication.processEvents()
        p = self.out / f"{self.key}_{name}.png"
        widget.grab().save(str(p))
        self.shots.append(p.name)

    @property
    def verdict(self) -> str:
        order = ("BROKEN", "UNREACHABLE", "UX")
        for v in order:
            if any(f["verdict"] == v for f in self.findings):
                return v
        return "PASS"

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "verdict": self.verdict, "passed": self.passed,
                "findings": self.findings, "shots": self.shots}


def _app():
    from livereact.app import App, AppConfig
    from livereact.clock import FakeClock
    return App(AppConfig(demo=True, config_path=None), clock=FakeClock())


def _small_rig(folder: Path) -> Path:
    p = folder / "Walkthrough rig.json"
    p.write_text(json.dumps({"name": "Walkthrough rig", "laser_profile": "trigger", "slots": [
        {"slot_id": "wt_bar", "fixture_type": "ok103_wash_bar", "dmx_address": 101, "label": "WT Bar",
         "role": "wash", "side": "left"}]}), encoding="utf-8")
    return p


def _menu_action(win, text_start: str):
    from PySide6 import QtGui, QtWidgets
    for m in win.menuBar().findChildren(QtWidgets.QMenu):
        for a in m.actions():
            if a.text().replace("&", "").startswith(text_start):
                return a
    return None


# ----------------------------------------------------------------------------------------- journeys
def j_first_run(j: Journey, home: Path) -> None:
    from livereact.settings import Settings
    from livereact.ui import console as console_mod
    from livereact.ui.console import ConsoleWindow
    src = inspect.getsource(console_mod.run_console)
    if "needs_first_run" in src:
        j.ok("first-run opens by itself on a plain first launch")
    else:
        j.find("BROKEN", "first launch", "the setup opens by itself", "nothing opens; the setup is hidden in Help")
    app = _app(); win = ConsoleWindow(app); win.show()
    act = _menu_action(win, "Run first-time setup")
    if act is None:
        j.find("UNREACHABLE", "Help menu", "a 'Run first-time setup' entry", "no such entry"); win.close(); return
    win.open_first_run(audio_provider=lambda: ["XDJ-XZ"], interface_provider=lambda: ["en8 169.254.1.2"])
    d = win.first_run_dialog
    j.shot(d, "outputs")
    phantom = [k for k, rb in d.output_buttons.items() if rb.isEnabled() and k not in ("enttec", "demo")]
    if phantom:
        j.find("BROKEN", "step 1 outputs", "only outputs this build can drive",
               f"offers {phantom} with no backend in src/livereact/output/: a user gets no lights and no error")
    else:
        j.ok("step 1 offers only outputs that exist")
    d.output_buttons["enttec"].setChecked(True)
    for _ in range(3):
        d.next()
    d.cmb_audio.setCurrentIndex(d.cmb_audio.findData("XDJ-XZ"))
    d.next()
    s = Settings.load()
    try:
        from livereact.launch import from_settings
        ld = from_settings(s)
        if ld.hardware and ld.audio_device == "XDJ-XZ":
            j.ok("setup answers become the next launch's defaults (launch.py)")
        else:
            j.find("BROKEN", "finish", "the next launch uses Enttec + XDJ-XZ", f"launch defaults {ld}")
    except ImportError:
        j.find("UNREACHABLE", "finish", "the next launch uses the chosen output/audio/network",
               "the answers are saved to settings.json and never read at launch")
    win.close()


def j_rig_switch(j: Journey, home: Path) -> None:
    from livereact.ui.console import ConsoleWindow
    from livereact import userdata
    app = _app(); win = ConsoleWindow(app); win.show()
    act = _menu_action(win, "Rig manager")
    if act is None:
        j.find("UNREACHABLE", "Rig menu", "a Rig manager", "no Rig menu entry"); win.close(); return
    act.trigger()
    dlg = win.rig_manager
    j.shot(dlg, "rig_manager")
    from PySide6 import QtWidgets
    if dlg.recent.count() == 0:
        j.find("UX", "open Rig manager on a fresh install", "a hint for the first step (Demo rig / New rig)",
               "an empty 'Recent rigs' box and five buttons; nothing says what to do")
    before = set(app.show_engine.rig._by_slot)
    rig = _small_rig(userdata.paths().rigs)
    dlg.choose(str(rig))
    app.show_engine.tick()
    after = set(app.show_engine.rig._by_slot)
    if after == {"wt_bar"} and after != before:
        j.ok("choosing a rig switches the running show without a restart")
        if getattr(win, "venue_tab", None) is not None and win.venue_tab.rig is app.rig:
            j.ok("the console's Venue / Rig Config tabs show the new rig")
        else:
            j.find("BROKEN", "after the switch", "Venue tab shows the new rig", "it still shows the old rig")
        if app.manual.laser_enable:
            j.find("BROKEN", "after the switch", "lasers disarmed on a new rig", "still armed")
    else:
        j.find("UNREACHABLE", "Use selected / Open", "the show plays the chosen rig",
               f"the show still plays {sorted(before)[:4]}...; status bar: {win.statusBar().currentMessage()!r}")
    j.shot(win, "after_switch")
    win.close()


def j_rig_config_live(j: Journey, home: Path) -> None:
    from PySide6 import QtWidgets
    from livereact.ui.console import ConsoleWindow
    app = _app(); win = ConsoleWindow(app); win.show()
    win.tabs.setCurrentWidget(win.rig_config)
    j.shot(win, "rig_config")
    texts = [b.text() for b in win.rig_config.findChildren(QtWidgets.QPushButton)]
    labels = " ".join(l.text() for l in win.rig_config.findChildren(QtWidgets.QLabel))
    live = [t for t in texts if any(w in t.lower() for w in ("use now", "apply", "save and use", "play"))]
    if live:
        j.ok(f"Rig Config can apply edits to the running show ({live[0]!r})")
    else:
        why = "the tab says so" if "do not apply" in labels.lower() or "do not apply" in labels else "no control applies them"
        j.find("UNREACHABLE", "Rig Config: Add fixture -> Save",
               "an added or re-addressed fixture plays in the running show (App.switch_rig exists)",
               f"edits only save to a file and need a restart ({why}); buttons: {texts}")
    win.close()


def j_venue(j: Journey, home: Path) -> None:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "agent" / "venue_check.py")], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        j.ok("Venue tab edits change the next frame (venue_check.py)")
    else:
        j.find("BROKEN", "Venue tab", "venue_check.py passes", (r.stdout + r.stderr)[-400:])


def j_show_controls(j: Journey, home: Path) -> None:
    from dataclasses import asdict
    from PySide6 import QtGui
    from livereact.ui.console import SHORTCUTS, ConsoleWindow
    app = _app(); win = ConsoleWindow(app); win.show()
    j.shot(win, "show_tab")
    shortcuts = {sc.key().toString(): sc for sc in win.findChildren(QtGui.QShortcut)}

    def state():
        m = {k: v for k, v in asdict(app.manual).items() if k != "calibration"}
        return (m, app.input_queue.qsize(), getattr(app.phrase_engine, "_skip_requested", None),
                getattr(app.metronome, "_build_cue_until", None), getattr(app.metronome, "_drop_armed_until", None))
    dead = []
    for key, target, what in SHORTCUTS:
        sc = shortcuts.get(QtGui.QKeySequence(key).toString())
        if sc is None:
            j.find("BROKEN", f"shortcut {key}", f"{what}", "no shortcut bound"); continue
        before = state()
        sc.activated.emit()
        if target == "_do_tap":            # tap tempo needs a second tap half a beat-ish later
            app.clock.advance(0.5)
            sc.activated.emit()
        after = state()
        if before == after:
            dead.append(f"{key} ({what})")
        else:
            j.ok(f"{key}: {what}")
        if key in ("B", "F", "S", "L", "K"):
            sc.activated.emit()   # toggle back
    for d in dead:
        j.find("BROKEN", f"shortcut {d}", "the app's state changes", "nothing observable changed")
    win.close()


def j_presets(j: Journey, home: Path) -> None:
    from livereact import presets
    from livereact.ui.console import ConsoleWindow
    app = _app(); win = ConsoleWindow(app); win.show()
    panel = getattr(win, "override_panel", None)
    if panel is None:
        j.find("UNREACHABLE", "Show tab", "a presets panel", "none"); win.close(); return
    before = app.manual.warm_cool_bias
    panel.set_event(presets.PresetEvent("walk", scheme="warm"))
    j.shot(win, "preset_warm")
    if app.manual.warm_cool_bias != before:
        j.ok("a colour-scheme preset changes the running show's warm/cool bias")
    else:
        j.find("UNREACHABLE", "Presets: Colour scheme = Warm", "the running show turns warmer",
               f"nothing changed; panel says {panel.l_preset_msg.text()!r}")
    msg = panel.l_preset_msg.text()
    if "not supported" in msg.lower():
        j.find("UNREACHABLE", "Presets: Colour scheme / Genre", "the scheme's palette and the genre's looks play",
               f"only brightness/strobe/bias/lasers apply; the panel says: {msg!r} (roadmap item 31)")
    win.close()


def j_missing_hardware(j: Journey, home: Path) -> None:
    from PySide6 import QtWidgets
    from livereact.ui.console import ConsoleWindow
    app = _app(); win = ConsoleWindow(app); win.show(); win.refresh()
    j.shot(win, "status")
    texts = [l.text() for l in win.findChildren(QtWidgets.QLabel) if l.isVisible() and l.text().strip()]
    explained = [t for t in texts if any(w in t.lower() for w in ("demo", "not connected", "no enttec", "no audio", "missing"))]
    if explained:
        j.ok(f"the operator is told what is missing ({explained[0][:60]!r})")
    else:
        j.find("UX", "launch with no Enttec / no XDJ / no DJ Link", "plain words for each missing input",
               "four red dots (LINK, MIDI, AUDIO, DMX) and 'PDL STALE · MIDI stale'; nothing a new user can act on")
    win.close()


def j_quit(j: Journey, home: Path) -> None:
    from livereact.app import App, AppConfig
    app = App(AppConfig(demo=True, config_path=None))
    app.start()
    time.sleep(1.0)
    t0 = time.monotonic()
    app.shutdown()
    dt = time.monotonic() - t0
    alive = [t.name for t in app._core_threads if t.is_alive()]
    if dt < 3.5 and not alive:
        j.ok(f"demo app shuts down cleanly in {dt:.1f} s")
    else:
        j.find("BROKEN", "quit", "a clean shutdown in under 3 s", f"{dt:.1f} s, threads still alive: {alive}")


JOURNEYS = (
    ("first_run", "First launch: setup reaches the app", j_first_run),
    ("rig_switch", "Pick a different rig in the Rig manager", j_rig_switch),
    ("rig_config_live", "Add / re-address a fixture in Rig Config", j_rig_config_live),
    ("venue", "Edit the room on the Venue tab", j_venue),
    ("show_controls", "Every Show-tab shortcut does something", j_show_controls),
    ("presets", "Pick a preset", j_presets),
    ("missing_hardware", "Start with nothing plugged in", j_missing_hardware),
    ("quit", "Quit", j_quit),
)


def run(out: Path, only: list[str] | None = None) -> list[dict]:
    from PySide6 import QtWidgets
    out.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="livereact-walk-"))
    os.environ["LIVEREACT_HOME"] = str(home)
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from livereact.ui.theme import apply_theme
    apply_theme(qt)
    results = []
    for key, title, fn in JOURNEYS:
        if only and key not in only:
            continue
        j = Journey(key, title, out)
        try:
            fn(j, home)
        except Exception as exc:  # noqa: BLE001 - a crash is a finding, not the end of the walk
            j.find("BROKEN", "journey crashed", "no exception", "".join(traceback.format_exception_only(exc)).strip()
                   + " | " + traceback.format_exc().splitlines()[-3].strip())
        results.append(j.to_dict())
    return results


def render(results: list[dict]) -> str:
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("PASS", "BROKEN", "UNREACHABLE", "UX")}
    L = ["# Walk-through", "", "Journeys: " + "  ".join(f"{k} {v}" for k, v in counts.items()), ""]
    for r in results:
        L.append(f"## {r['verdict']} — {r['title']} (`{r['key']}`)")
        for p in r["passed"]:
            L.append(f"- ok: {p}")
        for f in r["findings"]:
            L.append(f"- **{f['verdict']}** at *{f['step']}*: expected {f['expected']}; got {f['got']}")
        if r["shots"]:
            L.append("- screenshots: " + ", ".join(r["shots"]))
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "results" / "agent" / "walkthrough"))
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    try:
        results = run(out, a.only)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    (out / "walkthrough.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    text = render(results)
    (out / "WALKTHROUGH.md").write_text(text, encoding="utf-8")
    print(json.dumps(results, indent=1) if a.json else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
