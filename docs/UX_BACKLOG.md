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

5. **The channel-test screen does not reach the lights.** Fixture library -> Test / confirm, or the wizard's last
   step: the sliders publish to a private buffer nothing reads (`ChannelTestPanel(frame_buffer=None)`; neither host
   passes one, and the address is always 1). Confirm therefore records what the user says, not what a fixture did;
   that includes a laser's captured Blank, which is what keeps a generic laser dark. 2026-11-06 the screen says so in
   amber and the DMX range and laser box are off, which is the honest half. Wiring it is [JOSH]: the only safe route is
   `CalibrationPins` (safety clamps and the laser gate still apply), and "pins exist only while the Venue tab is on
   screen" is a standing decision. Exact change if yes: a default-off "Send to the rig" box plus an address spin box on
   the panel, the console passes an `on_pins` callback, pins clear on close, on untick and on any rig switch, and
   `_tab_changed` must stop clearing them while the panel is up. Journey: with the show running, moving a slider
   with that box ticked changes the next frame, and closing the panel clears it.
6. **A library fixture on the wash role is dark in the show.** Every wash path in scenes and engines filters on
   `isinstance(fx, (Par, WashBar))`, and a new Rig Config row starts on wash. Measured 2026-11-06, demo, 12 s: generic
   par on wash all 0 while a native par moved; a generic head on spot played. This is roadmap item 15 ([JOSH],
   show path). The wizard now says so after Confirm; the Rig Config row itself still says nothing. Journey: a
   definition patched as wash gets non-zero output, or the row warns.
7. **The channel test only tests a definition's first mode.** No mode picker, so a multi-mode definition (common
   from Open Fixture Library) is confirmed whole after testing one mode. Journey: a two-mode definition offers both.
8. **`--fixture-test` has no laser guard.** `ui/fixture_test.py` gives raw sliders on every channel with no arm
   switch. It is a bring-up tool, but tonight's note deliberately does not send laser users there. [JOSH]
9. **`--fixture-test` "Save as Home Position" to an unwritable path raises.** `_save_home_position` catches only
   `RigError`, as Rig Config and Venue did until 2026-11-06. One-line fix: `except (RigError, OSError)`.

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

3. **Saved-as rigs keep the old rig's name.** Rig Config has no name field, so a file saved under a new name still
   announces "Now playing rig: LiveReact default rig", and the fixture delete box lists that name for it. Proposal:
   a Name field in Rig Config. Journey: save a rig as a new file and the status bar names it.
4. **Laser Confirm is possible with nothing seen.** See Open 5: Capture Blank works on all-zero sliders no one
   watched. Proposal: until the screen reaches the rig, a confirmed laser definition stays visibly "not verified".
5. **"Confirmed: no" on a built-in reads as a job to do,** but a built-in can never be confirmed. Proposal: "built-in"
   in that column, or an empty cell.

## Integration (proposals)

1. **Audio source selection in the UI**, by device name, with the XDJ pre-selected when present, instead of
   `--audio-device` on the command line.
2. **Network interface selection** for Pro DJ Link in the UI, with re-detection when the adapter changes.
3. **Rig import/export** of any fixture definition the app knows, so a second operator can reproduce a rig.

4. **Calibration for heads from the library.** `GenericMovingHead` is not a `MovingHead`, so the Venue tab has no
   Calibrate group, SET AIM or motion box for it (roadmap item 15). The tab now says so.

## Fixed

- **The calibration beam was black on an RGBW beam head** (Bug, 2026-11-06). Venue tab, select the head: "Light the
  selected head" pinned the SHEHDS wheel's open slot at address+7, which on that head is Red, and left G/B/W alone.
  With no music the frame was dimmer full, RGB 0,0,0: nothing to aim. The pins now come from the head's own `set_color`;
  a wheel head's pins are unchanged. Show output is untouched: this is only the calibration pin.
  `test_selecting_an_rgbw_head_pins_it_open_white`, `test_the_rgbw_calibration_beam_is_lit_in_the_next_frame_with_no_music`,
  `test_a_wheel_head_keeps_the_same_calibration_pins`.
- **A second hand entry silently replaced a saved fixture** (Bug, 2026-11-06). Same make and model, new channels:
  Confirm overwrote the file, and every rig using that id moved to the new layout. All three wizard routes now ask
  first and name the rigs. `test_entering_the_same_name_again_does_not_silently_replace_a_saved_fixture`,
  `test_replacing_a_saved_fixture_on_purpose_works_and_names_the_rigs_using_it`,
  `test_confirming_the_same_channels_again_asks_nothing`.
- **A fixture added after a rig switch never reached Rig Config** (Bug, 2026-11-06). The open library held the old tab's
  reload, so the type list stayed stale until the next launch.
  `test_a_fixture_added_after_a_rig_switch_reaches_the_new_rig_config_tab`.
- **Hand entry confirmed a moving head the loader refuses** (Bug, 2026-11-06). No pan or tilt: saved, then it failed at
  Rig Config's Save. The wizard says so at entry.
  `test_hand_entry_refuses_a_moving_head_the_rig_loader_would_refuse`.
- **Saving a rig to an unwritable folder did nothing visible** (Bug, 2026-11-06). Rig Config and Venue raised an
  uncaught OSError from the button. Both now show "Not saved" with the reason.
  `test_rig_config_save_to_a_folder_it_cannot_write_says_so`, `test_venue_save_to_a_folder_it_cannot_write_says_so`.
- **Marking every channel of a built-in, then "Not saved"** (UX, 2026-11-06). The screen says up front that a built-in
  cannot be confirmed and points at Duplicate. `test_a_built_in_says_up_front_that_it_cannot_be_confirmed`,
  `test_a_copy_of_a_built_in_can_still_be_confirmed`.
- **Channel test claimed a DMX address and a laser output box that reach nothing** (UX, 2026-11-06). See Open 5.
  `test_channel_test_from_the_library_says_it_does_not_reach_the_lights`,
  `test_a_panel_given_a_real_buffer_shows_no_such_note`,
  `test_the_laser_output_box_is_off_on_a_screen_that_reaches_nothing`.
- **No next step after Confirm** (UX, 2026-11-06). The wizard names Rig Config, Add fixture, Type, and the wash-role
  limit. `test_confirm_in_the_wizard_says_where_the_fixture_goes_next`,
  `test_a_fixture_added_by_hand_warns_about_the_wash_role`.
- **A library head had no Calibrate group and no reason given** (UX, 2026-11-06). The Venue tab says why.
  `test_a_library_head_says_why_it_has_no_calibrate_panel`, `test_a_built_in_head_shows_no_such_note`.
- **Test / confirm was clickable with nothing selected** (UX, 2026-11-06).
  `test_test_confirm_is_off_until_a_fixture_is_selected`.
- **Hand-entry errors in schema words** ("id is empty", "has no fine_of") (UX, 2026-11-06).
  `test_hand_entry_explains_its_errors_in_plain_words`.
- **The manual page said "in Settings"; there is no Settings menu** (UX, 2026-11-06).
  `test_the_manual_page_points_at_the_menu_that_exists`.
