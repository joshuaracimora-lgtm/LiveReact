# UX backlog: what a user of LiveReact runs into (the User-Tester agent's list)

Started 2026-10-30. Josh: "I'm the only user of LiveReact, so it takes me a lot of time to go through the
app, look for all the bugs, and think about discrepancies. I want the agents to act like that ... see where
the app can be improved in terms of more professional ability, overall UI look, and bug fixes, and where
things can be improved to make it more integratable."

The nightly **User-Tester agent** (`docs/AGENT_BRIEF_UI.md`) walks the app as a new user every night and
keeps this file. Three kinds of entry (bugs live under "## Open" so `tools/agent/status.py --role ui` can count them), each with the screen, the steps, what happened, what a user expects:

- **Bug** — the app does the wrong thing. The agent fixes it (test first) and moves it to **Fixed** with the
  test name. Crashes and data loss first.
- **UX** — it works but a new user would stumble: unclear label, no feedback, hidden step, inconsistent
  wording, a dialog that needs a manual. Small and safe ones (a label, a tooltip, an enabled/disabled state,
  an error message) the agent fixes; anything that moves or redesigns a screen stays a **proposal** here
  until Josh approves it.
- **Integration** — something a different rig, venue, OS or DJ setup would need and cannot do today
  (fixture formats, DMX outputs, importing a rig, paths, device loss). Proposals only; the App agent and
  Josh pick them up through `docs/APP_ROADMAP.md`.

Line numbers drift; the screen and function names do not. Never name Josh's tracks or venues here.

## Open (bugs, highest risk first)

1. **Device loss is silent.** Steps: start `--hardware`, unplug the Enttec, the USB-Ethernet adapter or the
   XDJ (2026-10-30: all three re-enumerated or vanished on the bench). What happened: DMX falls to the
   null-log backend, Pro DJ Link sockets stay pinned to the dead interface, audio stays stale; the console
   keeps running with the lights wrong and no message. Expected: the console says what was lost, retries the
   port / re-resolves the interface when it returns, and never needs a relaunch to recover.
   (`inputs/prodj.py start()`, `app.py` backend selection, `inputs/audio.py start()`.)
   *Status 2026-11-05: the Mac's show-night work landed the health banner (`ui/health.py`), audio reconnect and
   the DMX late-plug, so the loss is now said in words. Still open: nothing in `inputs/prodj.py` re-resolves
   the interface after the adapter comes back (sockets are pinned once, at `start()`). [JOSH: outside the
   agent's files.]*
2. **The app does not exit on SIGTERM.** `kill <pid>` logs "Received signal 15" and the process is still
   alive 20 s later; only `kill -9` works (seen three times on 2026-10-29/30). Expected: a clean shutdown in
   under 3 s. Find the thread that does not join (audio stream close? Qt loop?).
   *2026-11-05, could not reproduce in demo mode: `stop_event` set under `run_console` left the Qt loop in
   0.26 s, shutdown 0.0 s, no thread alive. Windows has no real SIGTERM to send, and the hardware path (audio
   stream, DJ Link sockets) cannot run on the agent's desktop, so it needs a try on the Mac with the rig.*
3. **A numeric `--audio-device` is an index that moves.** The iPhone mic appearing or vanishing shifts every
   index; `1` was the MacBook mic one minute and the speakers (0 inputs) the next, and `start()` died on
   `max() of an empty iterable`. Expected: a clear error ("device 1 has no inputs; available: ...") and a
   recommendation to use the name.
   *Exact change, for Josh (`inputs/` is outside the agent's files): in `AudioAnalyzer._open_stream`, right after
   `n_in = int(info["max_input_channels"])`, `if n_in < 1: raise AudioInputError(...)` naming the device and
   listing the input-capable names from `sd.query_devices()`. `failed_inputs` then puts it in the NO AUDIO
   INPUT banner by itself.*

## UX (open)

0b. **Demo mode still shows the lasers ARMED in red.** The dots and the words are fixed (see Fixed); the big red
   "DISARM LASERS / ARMED" stays because `app.py` really arms in demo mode to simulate the operator, and the arm
   switch must never lie. Proposal: a "simulated" caption beside it in demo mode. (Walk-through.)
1. **No operator-facing status for the inputs.** The Show tab shows PDL / MIDI / audio as dots in a log
   line; a new user cannot tell "no XDJ audio" from "quiet". Proposal: a status strip naming each input and
   its source ("Audio: XDJ-XZ ch 5-6" / "Audio: none"), red when a required one is missing.
2. **Clicking New rig blacks out a running show.** Rig manager -> New rig: the file is made and the show switches
   to it at once, so every light goes dark with only a status-bar line. It is what the button says on a fresh
   install, but mid-set it is one click. Proposal: New rig creates the file and opens Rig Config, and the show
   keeps its rig until "Save and use now" (branch agent/20261103-ui). Journey that would catch it: with a
   running show, New rig leaves `app.rig` playing. [UX-structural, for Josh]
3. **Enter does nothing in the Rig manager now.** Deliberate after the bug below: any rig choice changes the
   lights, so nothing fires by itself. If Josh wants Enter = "Use selected" with the list focused, it is one
   line. Space on the focused button and double click work.
4. **An empty Rig Config table says nothing.** After New rig the tab is an empty grid. Proposal: the same kind
   of hint as the Rig manager ("Add fixture to start; each fixture needs a DMX address"). Wait for the 11-03
   branch: its note changes the same screen. Journey: after New rig, the Rig Config tab shows a next step.
5. **`walkthrough.py` `rig_switch` cannot see the empty-state hint.** It tests `recent.count() == 0`, and
   `test_missing_recent_files_are_hidden` pins that count, so the hint is a label in the box, not a row, and the
   journey stays UX with the screen fixed. Proposal: the journey looks for a visible text in `dlg.recent`
   instead (`dlg.l_empty.isVisible()`).
6. **`walkthrough.py` needs conftest's `QT_QPA_FONTDIR`** or every PNG on the desktop is tofu boxes; the orchestrator's
   run also stopped after one PNG with no WALKTHROUGH.md again on 2026-11-05, while the same command run by
   hand finished in 11 s. Journey: none, a `tools/` fix.

## Integration (proposals)

1. **Audio source selection in the UI**, by device name, with the XDJ pre-selected when present, instead of
   `--audio-device` on the command line.
2. **Network interface selection** for Pro DJ Link in the UI, with re-detection when the adapter changes.
3. **Rig import/export** of any fixture definition the app knows, so a second operator can reproduce a rig.

## Fixed

- **Enter on the Rig manager emptied the running show** (Bug, 2026-11-05). "New rig" was the dialog's default button,
  so Enter made an empty rig and switched the show to it: 8 fixtures to 0, lights dark, with or without recent
  rigs listed. No button is a default now. `test_enter_on_a_fresh_rig_manager_leaves_the_running_show_alone`,
  `test_enter_with_recent_rigs_listed_leaves_the_running_show_alone`.
- **Rig manager opens empty with no next step** (UX 0, 2026-11-05). A hint inside the empty box, focus on Demo rig,
  tooltips on every button, and disabled buttons say why. `test_empty_rig_manager_says_what_to_do_first`,
  `test_disabled_rig_buttons_say_why`.
- **Enter went backward in first-run setup** (Bug, 2026-11-05). "Back" was the default button, so Enter on step 2
  returned to step 1. Next is the default now. `test_enter_moves_forward_through_setup`.
- **First-run offered outputs that did not exist** (Bug 3, closed 2026-11-05, already fixed by the Mac's launch
  wiring: Art-Net and sACN are greyed "coming later"). Verified, no change tonight.
  `test_first_run_offers_only_outputs_this_build_has`.
