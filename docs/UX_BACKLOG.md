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
2. **The app does not exit on SIGTERM.** `kill <pid>` logs "Received signal 15" and the process is still
   alive 20 s later; only `kill -9` works (seen three times on 2026-10-29/30). Expected: a clean shutdown in
   under 3 s. Find the thread that does not join (audio stream close? Qt loop?).
3. **First-run offers outputs that do not exist.** Steps: Help -> Run first-time setup again -> step 1 lists
   "Art-Net (network)" and "sACN / E1.31 (network)". What happened: `src/livereact/output/` has only the
   Enttec and null-log backends; a user who picks Art-Net or sACN gets no lights and no error. Expected:
   either the backends (roadmap) or the options greyed with "coming later", and a plain error if a saved
   setting names a backend the build lacks. (Found by the offscreen walk-through, 2026-10-30.)
4. **A numeric `--audio-device` is an index that moves.** The iPhone mic appearing or vanishing shifts every
   index; `1` was the MacBook mic one minute and the speakers (0 inputs) the next, and `start()` died on
   `max() of an empty iterable`. Expected: a clear error ("device 1 has no inputs; available: ...") and a
   recommendation to use the name.

## UX (open)

0. **Rig manager opens empty with no next step.** A new user sees an empty "Recent rigs" box and five buttons;
   nothing says "pick Demo rig to start, or New rig". Proposal: placeholder text in the empty list and the
   Demo rig button as the default focus. (Walk-through, 2026-10-30.)
0b. **Demo mode launches with every status dot red and the lasers ARMED in red.** A first-time user reads LINK /
   MIDI / AUDIO / DMX all red as "broken" and "DISARM LASERS — ARMED" as dangerous. Proposal: in demo mode
   the dots say "demo" in grey, and the arm button is not pre-armed red on a rig with no laser. (Walk-through.)
1. **No operator-facing status for the inputs.** The Show tab shows PDL / MIDI / audio as dots in a log
   line; a new user cannot tell "no XDJ audio" from "quiet". Proposal: a status strip naming each input and
   its source ("Audio: XDJ-XZ ch 5-6" / "Audio: none"), red when a required one is missing.
2. **Synth Sync label.** "Synth Sync (S) — wash follows the synths" no longer describes the thresholded
   behaviour (D-32). Proposal: "Synth hits (S) — the bar lifts on stabs above the threshold".

## Integration (proposals)

1. **Audio source selection in the UI**, by device name, with the XDJ pre-selected when present, instead of
   `--audio-device` on the command line.
2. **Network interface selection** for Pro DJ Link in the UI, with re-detection when the adapter changes.
3. **Rig import/export** of any fixture definition the app knows, so a second operator can reproduce a rig.

## Fixed

(none yet)
