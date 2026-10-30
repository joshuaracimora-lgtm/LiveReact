# App roadmap: the App agent's work list

Goal (Josh, 2026-10-26): publish LiveReact as a Windows + macOS app that works with **anyone's** lights.
That means user-addable fixtures (Open Fixture Library first, then an AI reader for the fixture's PDF
manual, then hand entry, always confirmed on a channel-test screen), a rig manager instead of `--rig`
paths, lasers that run themselves after one arm per session, a Manual Override panel (cues, laser
overrides, colour scheme, genre, event type), and installers. Josh's `rigs/*.json` are test inputs,
not product content: only `src/livereact/fixtures/default_rig.json` ships, as the demo rig.

The nightly **App agent** (`docs/AGENT_BRIEF_APP.md`) works the Open list top to bottom, skipping any
item tagged `[JOSH]`, `[SHOW-CHANGE]` or `[BLOCKED: ...]`. Per item it writes a failing test first
(new tests go in `tests/test_app_*.py`), implements, runs `tools/agent/gate_app.py`, commits once,
then moves the item to **Done** with the test names and the date. Untagged items assume nothing from a
`[JOSH]` item that is still open.

Tags:
- `[JOSH]`: an interactive session with Josh. Lighting, merge, engine, sim or `app.py` code (CLAUDE.md:
  read `spec/LIGHTING_BIBLE.md` + `spec/conformance.md` first), money, accounts, legal wording.
- `[SHOW-CHANGE]`: the simulator's scores move, so Josh accepts a new baseline. Never agent work.
- `[BLOCKED: ...]`: waits on the item named.

Hard rules for every item, whoever does it:
- **The show does not change.** A refactor leaves the DMX output byte-identical (the golden-frame
  gate, item 1, once it exists; until then the lighting gate in no-regression mode).
- **Lasers fail safe.** Nothing bypasses `ManualParams.laser_enable`. A laser from an unconfirmed
  (or AI-only) fixture definition never outputs. Blackout always wins.
- **No network, no API key in tests.** `gate_app.py` runs them with `LIVEREACT_OFFLINE=1`, which blocks
  non-loopback sockets and hides `ANTHROPIC_API_KEY`. Network code is tested against recorded data.
- **Live == sim** (D-26). A preset or catalog effect reaches the show through `ManualParams` and
  `show_catalog()`, so the simulator can replay it.
- Never ship or name Josh's tracks or venues in a committed file (the repo is public).

## Open (in order)

1. [JOSH] **P0 golden-frame gate.** `tools/agent/golden_frames.py` + `tests/test_golden_frames.py`: run the
   deterministic harness (the `test_harness_determinism` path) over 3 synthetic clips x every `rigs/*.json`
   and `fixtures/default_rig.json`, hash the DMX output per frame, compare `--base` (temp worktree) with
   HEAD. Byte-identical or REJECT. `gate_app.py` already prefers it over the lighting gate once it exists.
2. [JOSH] [SHOW-CHANGE] **P0 one laser factory for live and sim.** `engines/laser_factory.py`
   `make_laser_engine(profile, clock, ...) -> (engine, arm_at_launch)`, used by `app.py` (laser selection)
   and `sim/harness.py` (today it always builds the accent `LaserEngine`, whatever the rig says, so the
   gate never scores the profile a non-accent rig actually plays). Re-baseline once.
3. [JOSH] **P0 wire the user data folder.** After item 9: `AppConfig` paths, `config.DEFAULT_PATH`,
   `__main__.py` defaults, recordings, logs resolve through `userdata.paths()`; first run copies an
   existing `./config.json` / `./midi_map.json` across. A source checkout keeps today's paths.
4. [JOSH] **P0 app seams.** `App.switch_rig(path)` (blackout, stop the show thread, rebuild rig + engines,
   restart, always disarm lasers), `App.apply_preset(effect)`, `PhraseEngine.set_catalog(catalog)`
   (takes effect at the next phrase). No behaviour change.
5. [JOSH] **P0 vendor python-prodj-link.** Check its license first. `src/livereact/_vendor/prodj/` at a
   pinned SHA with its LICENSE; drop the PyQt5 / PyOpenGL imports (PyQt5 is GPL and must not ship).
15. [JOSH] [BLOCKED: item 1] **P1 swap isinstance checks for traits.** `scenes/base.py`,
    `scenes/library.py`, `engines/reactive.py`, `engines/transition.py`, `scenes/movement.py`, `rig.py`
    `dj_pose`, `ui/visualizer.py`, `sim/score.py` (wash bar by name, `PAN_MAX_DEG_PER_S`). Each commit
    golden-frame identical. After this, generic fixtures take part in the show.
16. [JOSH] [BLOCKED: item 1] **P1 laser driver.** `fixtures/laser_driver.py`: `ShehdsLaserDriver` (today's
    bytes exactly) and `IntentLaserDriver` (a confirmed definition's `laser_intents`). Every laser engine,
    `engines/color.py` and the laser scenes call the driver instead of SHEHDS methods.
37. [JOSH] **P3 check the OFL URLs + data license.** `fixtures/ofl.py` `REGISTER_URL`, `MANUFACTURERS_URL`,
    `FIXTURE_URL` point at OFL's GitHub `master` branch and were not checked by hand (the agent has no
    internet). Confirm them (or OFL's own API), and decide the `license` string the wizard passes to
    `import_fixture()` (OFL data license; see Decisions). Then list OFL attribution in About (item 33).
29. [JOSH] **P4 manual reader evaluation (costs money).** `tools/eval_manual_reader.py` against the three
    known manuals (SHEHDS 180W, SHEHDS 3W laser, OK103), scored per channel against `builtin/`.
30. [JOSH] [BLOCKED: items 2, 4] **P5 automatic lasers.** New `"auto"` laser profile built on
    `LaserTriggerEngine` (the only engine meeting LASER-14: duty 10%/120 s, gap >= 8 bars, 2-bar moments,
    cut on audio/PDL loss), drawing the show engine's follow-mode looks inside allowed moments.
    - D-24: an automatic moment needs live audio (drop hit / PEAK) AND a structural signal (rekordbox
      plan or Pro DJ Link section/bar).
    - Arm once per session behind a checklist modal; re-arm after a rig switch or restart.
    - Remove arm-at-launch from the show/manual profiles (assumption B3).
    - Update the conformance rows.
31. [JOSH] [SHOW-CHANGE] [BLOCKED: item 30] **P5 make auto the default** laser profile; colour-scheme hook
    in `engines/palette.py pick()`; genre catalog hook via `show_catalog()`; the sim takes `--preset`.
39. [JOSH] **P6 first build.** Run the release workflow once by hand (or `pyinstaller packaging/livereact.spec`
    locally), add icons under `src/livereact/ui/icons/` (`livereact.ico`, `livereact.icns`), pick the macOS
    `bundle_identifier` (placeholder `org.livereact.app`), and check `macos-13` (x86_64) is still a GitHub
    runner.
40. [JOSH] **P6 show first-run + About.** `__main__` opens `FirstRunDialog` when `needs_first_run()` and
    maps `settings.extra["setup"]` onto `AppConfig` (output backend, `audio_device`, `pdl_interface`; item 3);
    a Help -> About entry in the console.
34. [JOSH] **P6 signing + clean-machine tests.** Apple Developer account and notarization; Windows Azure
    Trusted Signing or an OV certificate; CI secrets; install on a clean Windows PC and a clean Mac.
35. [JOSH] **P7 publish.** User guide, laser disclaimer / EULA and arm-checklist wording, privacy note for
    the manual reader, README screenshots, 1.0 tag and release notes.

49. **P3 library filter box.** A search field above `ui/fixture_library.py`'s table that filters rows as
    the user types, matching manufacturer, model, id or category case-insensitively; clearing it shows
    everything, and the details pane / buttons keep following the (filtered) selection. Matters once a
    library holds more than a screenful of imported fixtures. Tests first.

## Decisions only Josh can make (each blocks the items named)

- **Answered 2026-10-28** (the App night's "could not decide" list; Josh: "go with your suggestions"):
  the Manual Override group opens expanded; the ARM/DISARM button wording stays; an event type that bans
  lasers disarms them at once; preset numbers are accepted as first guesses; the builtin par and wash bar
  stay confirmed, the head and laser not; a builtin id can't be redefined by a rig file; amber/UV held at 0;
  generic head slew 180 deg/s; the manual reader on claude-opus-5-5 with the user's own key; the channel
  test needs the arm switch too (item 41); `/fixtures/`, `/manuals/`, `/events/` are git-ignored.

- **Laser liability** (30, 35): checklist + EULA wording; whether audio-only users (no Pro DJ Link)
  get lasers at all; whether lasers ship as "experimental" in 1.0.
- **D-24** (30): does the Pro DJ Link bar grid alone count as the second signal, or is rekordbox
  phrase/plan data required?
- **Manual reader** (27-29): bring-your-own API key (the plan) or a hosted key you pay for. A rough
  estimate, not measured: $0.10-0.30 per manual.
- **Signing** (34): Apple Developer $99/yr; Windows certificate.
- **Licenses** (5, 25): Open Fixture Library's data license (attribution / share-alike) and
  python-prodj-link's license.
- **Public rig files**: `rigs/*.json` descriptions name venues; decide whether that is fine in a public repo.

## Done

- **6. P1 fixture definition format** (2026-10-28). `fixtures/definition.py`: frozen dataclasses,
  `load_definition()` (dict or JSON file), `validate()`, `kind_of(function, category)`, `to_dict()`
  round-trip. Offsets are 0-based per mode; a laser's every channel is LASER; capability types add
  `maintenance` and `no_function` to the function list; `validate()` also checks `laser_intents`
  offsets/values and duplicate mode names. Tests: `tests/test_app_fixture_definition.py`.
- **7. P1 the four built-in models as definitions** (2026-10-28). `fixtures/builtin/{shehds_180w_mh,
  shehds_3w_laser,rgb_par,ok103_wash_bar}.json`, generated once from `profiles.py` (wheel slots, laser
  colour bands, MH ranges/speed). Par and wash bar are `confirmed` (verified on hardware 2026-08-31);
  the MH and the laser are not (the laser has no `laser_intents`, so a definition-driven laser built
  from it stays dark). Tests: `tests/test_app_builtin_definitions.py` (count, `kind_of()` per channel
  and boot defaults equal the native class).
- **8. P1 fixture traits** (2026-10-28). `fixtures/traits.py` `traits_of(fx, mode=None)`: native classes
  by class (Par -> rgb_wash+dimmer, WashBar -> rgb_wash+dimmer+strobe_channel, MovingHead -> pan_tilt+
  color_wheel+dimmer+strobe_channel, Laser -> laser), equal to their builtin definitions' traits;
  definitions (or a fixture carrying `.definition`) from channel functions; a laser is only ever
  `laser`. Nothing on the show path reads it (item 15). Tests: `tests/test_app_traits.py`.
- **9. P1 user data folder module** (2026-10-28). `userdata.paths()` (platformdirs, %LOCALAPPDATA% not
  Roaming; directories created, files not), `LIVEREACT_HOME` override (wins over dev mode),
  `dev_mode()` (`pyproject.toml` two levels above the package, or `--dev`) -> the working folder.
  `settings.py` `Settings`: recent rigs (10, most recent first), last event, onboarding done; corrupt
  file -> defaults, unknown keys kept, atomic save. Not wired (item 3). Tests: `tests/test_app_userdata.py`.
- **10. P1 GenericFixture** (2026-10-28). `fixtures/generic.py` `GenericFixture(definition, mode,
  address, label, role, side)`: refuses an invalid definition; boot = definition defaults with dimmer 0
  and shutter at its "open" capability; intensity through the native gamma+floor curve, or RGB scaling
  when there is no dimmer (the fixture remembers the last colour/level for that); RGB(+W) with W folded
  in when absent, amber/UV held at 0; nearest wheel slot (middle of its range); strobe into the strobe
  capability range (0 = open); master brightness, strobe cap, colour bias, `visual_state()`.
  Tests: `tests/test_app_generic_fixture.py` (par, RGBW bar, wheel-only).
- **11. P1 GenericMovingHead** (2026-10-28). 16-bit pan/tilt when the definition pairs fine channels
  (8-bit otherwise), `set_aim` marking absolute movement, home-position remap, and a slew limit from
  `physical.pan_max_deg_s` (tilt uses the same deg/s over its own range; default 180 deg/s, slower
  than the native 260 placeholder; ranges default 540/270). `make_fixture()` picks the class. No motion
  box (`bounds`) or aim calibration yet. Tests: `tests/test_app_generic_moving_head.py`.
- **12. P1 GenericLaser** (2026-10-28). Driven only by `set_intent(buf, blank|line_static|fan)`; not a
  GenericFixture, so wash/dimmer/strobe setters cannot light it; every channel LASER. `ready` needs
  `source.confirmed` AND all three intents; until then every write, boot and `apply_safety` (even with
  `laser_enabled=True`) leaves the head at zero. Disarmed zeroes everything; the gate writes `blank`
  (only the `chop_channel` when one is set, like the native CH1-only gate).
  Tests: `tests/test_app_generic_laser.py`.
- **13. P1 definition library** (2026-10-28). `fixtures/library.py`: `parse_ref`, `resolve(ref,
  embedded)` (rig snapshot, user folder, builtin; a builtin id always resolves to the packaged model, so
  neither a user file nor a rig snapshot can redefine it), `list_definitions()` (bad user files
  skipped), `save_user_definition()` (validated, safe lowercase ids, atomic), `refs()`.
  Tests: `tests/test_app_fixture_library.py`.
- **14. P1 rig file v2** (2026-10-28). `rig.py`: a slot's `"fixture": "<id>@<mode>"` builds a Generic*
  fixture through the library; save writes `fixture` (not `fixture_type`) for it and embeds a top-level
  `definitions` snapshot of every non-builtin definition used; both keys on one slot, an unknown ref or
  an invalid definition are `RigError`s. v1 rigs: every `rigs/*.json` and the default rig save
  byte-for-byte as before (expected output hashes in `tests/data/rig_v1_save_hashes.json`, no names).
  Left for later: motion box / aim calibration / `dj_pan_tilt` for GenericMovingHead slots (MovingHead-only
  today). Tests: `tests/test_app_rig_v2.py`, lighting gate no-regress.
- **17. P5 presets** (2026-10-28). `presets/__init__.py` + `presets/presets.json`: pure
  `resolve(PresetEvent) -> PresetEffect(manual, catalog_kwargs, palette, + ceilings: master_brightness_max,
  strobe_cap_max, lasers_allowed, movement)`; `PresetEffect.apply_to(ManualParams)` applies and only ever
  lowers (brightness, strobe cap) or disarms; no preset ever arms lasers. Auto is a no-op (empty
  overrides). Named events save/load/list under `userdata.paths().events`. `userdata.paths(create=False)`
  added for read-only lookups. The numbers in presets.json (biases, ceilings, which event types forbid
  lasers) are design defaults for Josh to tune; genre only sets a `catalog_kwargs` token until item 31.
  Tests: `tests/test_app_presets.py`.
- **18. P5 Manual Override panel (widget)** (2026-10-28). `ui/override_panel.py` `OverridePanel(app)`:
  Lasers block (arm/disarm writes `manual.laser_enable`; status armed / disarmed / blocked by event; an
  event that forbids lasers disarms and blocks arming), Manual Override group collapsed by default (CUE
  BUILD, ARM DROP, STROBE BURST, SKIP, Tap, Laser Cue, Laser Hit, and Laser Show follow/rotate/hold + look
  grid on `show` rigs), Presets block (three combos, Save/Load event). Calls `app.apply_preset(effect)`
  only when the app has it, else stores the choice. Tests: `tests/test_app_override_panel.py`.
- **19. P5 put the panel in the console** (2026-10-28). The Show tab's Cues group and the "Arm lasers"
  checkbox are replaced by the override panel; `b_cue`, `b_arm`, `b_burst`, `b_skip`, `b_tap`,
  `b_laser_cue`, `b_laser_hit` and `cb_laser` are the panel's widgets, so every hotkey (C, D, Space, ->,
  T, K, J, L) and every console test is unchanged; tap tempo and strobe burst share the console's
  handlers; `refresh()` mirrors the panel. The console opens the Manual Override group expanded (the
  widget's default is collapsed) so cues stay one press away mid-set: a question for Josh. The `manual`
  laser profile's scene grid, Blackout/Freeze and Feel stay where they were. Tests: `tests/test_console.py`
  (all green), gate UI checks.
- **20. P2 rig manager dialog** (2026-10-28). `ui/rig_manager.py` `RigManagerDialog`: New (an empty rig in
  the user's rigs folder), Open (file picker there), Recent (settings.py, missing files hidden), Demo rig
  (packaged `default_rig.json`, read-only) and "Save demo rig as…" (a copy in the user folder). A chosen rig
  is validated through the loader, remembered, and emitted as `rig_chosen(path)`. The console has a Rig menu
  -> "Rig manager…"; it calls `app.switch_rig(path)` when the app has it (item 4), else says "restart to
  apply". Tests: `tests/test_app_rig_manager.py`.
- **21. P2 patch view** (2026-10-28). `ui/rig_config.py`: the type list adds the user's (and the rig's
  embedded) definitions with a Mode column; "Add fixture" takes the next free DMX block that fits; rows whose
  blocks collide or run past 512 are marked inline, with the loader's `RigError` on the status line; an
  "Address set" checklist column is saved as the slot's `address_set` (unticked = v1 slot unchanged);
  definition rows save as v2 `fixture` slots with the `definitions` snapshot. The channel reference lists a
  definition's channels. Tests: `tests/test_app_patch_view.py`.
- **22. P2 channel-test screen** (2026-10-28). `ui/channel_test.py` `ChannelTestPanel(definition, mode,
  address, frame_buffer, save)`: a slider per channel labelled by function, a capability picker ("Red: 10-19",
  sends the middle of the range), a pass/fail mark per channel, and Confirm (every channel pass; sets
  `confirmed` + `confirmed_at`, saves through the library). For lasers: capture blank / static line / fan from
  the sliders into `laser_intents`; Confirm needs all three. Laser channels send zeros unless "Enable laser
  output on this screen" is ticked (this screen is outside the show's arm switch). `ui/fixture_test.py` labels
  definition fixtures' channels by function. Tests: `tests/test_app_channel_test.py`.
- **23. P2 fixture library browser + add-fixture wizard** (2026-10-28). `ui/fixture_library.py`
  `FixtureLibraryBrowser` (built-in / yours / imported, confirmed or not; Add fixture…, Test / confirm…) and
  `ui/fixture_wizard.py` `FixtureWizard`: three routes (OFL, manual, by hand), the hand-entry page (manufacturer,
  model, category, mode, a channel table; fine channels paired automatically) producing a `user` definition,
  every route ending on the channel-test screen. Tests: `tests/test_app_fixture_wizard.py`. Not yet in a
  console menu (item 36).
- **24. P3 OFL converter** (2026-10-28). `fixtures/ofl.py` `ofl_to_definition(ofl, key, manufacturer=None,
  license=None)`: `availableChannels`, `modes` (shortName), first-level fine aliases (`pan_fine`/`tilt_fine`, or
  the coarse function with `fine_of`), capability `dmxRange` / type / colour (wheel slots resolved through
  `wheels`) / Hz speeds, `defaultValue` (number or %), `physical.focus` pan/tilt, categories; unknown types,
  OFL nulls, matrix inserts and deeper fine bytes become `generic`. Unconfirmed, `source.kind = ofl`,
  attribution with the OFL key and authors; `license` is left to the caller (Josh's decision). Test data:
  `tests/data/ofl/test-*.json`, written for the tests. Tests: `tests/test_app_ofl.py`.
- **25. P3 OFL search + fetch service** (2026-10-28). `fixtures/ofl.py` `OflService(cache_dir, transport)`:
  the register (+ manufacturer names) cached under `fixtures/ofl_cache/` in the user folder, refresh falling
  back to the cache, `search()` (every word, then close spellings), `fetch(key)` (cached, strict key format),
  `import_fixture()`; any network or parse failure is `OflUnavailable`. `ui/ofl_worker.py`: a `QThread`
  worker and the wizard's search page ("Open Fixture Library unavailable" offline, attribution line).
  Tests: `tests/test_app_ofl_search.py` (recorded register, fake transport), wizard route tests.
- **26. P4 manual page finder** (2026-10-28). `fixtures/manual_reader/extract.py`: per-page text (pypdf),
  `score_text()` (DMX, CHn, channel, mode/personality, nCH, 0-255 ranges), pages under 40 characters flagged
  scanned, `choose_pages()` (best text pages, at most 15; scanned pages fill in when text finds fewer than
  two), `find_dmx_pages()` writes `manuals/<sha256>/trimmed.pdf` + `pages.json` (page map back to the
  original). Tests: `tests/test_app_manual_extract.py` (a PDF generated by the test).
- **27. P4 manual reader client (mocked)** (2026-10-28). `manual_reader/schema.py` (`MANUAL_SCHEMA`, strict:
  every object closed and fully required; `check()`; `to_definition()` -> unconfirmed `manual_ai` with
  per-capability confidence and original `source_page`) and `manual_reader/client.py` (`ManualReader`:
  `claude-opus-5-5`, base64 PDF `document` block, `output_config.format` json_schema, effort high, server-side
  refusal fallback `fallbacks="default"`; `estimate()` via token counting at $4/$20 per MTok; key from
  keyring else `ANTHROPIC_API_KEY`, never written or logged; typed errors). Tests:
  `tests/test_app_manual_client.py` (recorded response, fake client and keyring).
- **28. P4 manual reader worker + review** (2026-10-28). `manual_reader/worker.py` (`read_manual()` +
  `ManualReaderWorker` QThread: cancel before the paid call, answer cached by PDF sha256 so a manual is never
  paid for twice), `review.py` (`draft()`, `save_draft()` refuses confirmed, `doubts()` below 0.6 confidence),
  `ui/manual_upload.py` (the wizard's upload page: greyed out without a key or offline, checked only when
  shown; one-time consent remembered in settings; page list + cost estimate before sending). The channel-test
  screen flags low-confidence rows with their manual page. Tests: `tests/test_app_manual_review.py`.
  An API key entry in Settings is not built yet (item 38).
- **32. P6 packaging files** (2026-10-28). `packaging/livereact.spec` (PyInstaller one-folder from
  `packaging/launch.py`; excludes PyQt5/PyQt6/OpenGL/tkinter and unused Qt modules; collects
  `fixtures/builtin/*.json`, `default_rig.json`, `presets/presets.json` and `ui/icons/` when it exists;
  macOS bundle with microphone / local-network usage strings), `packaging/windows/livereact.iss` (per-user
  Inno Setup install of `dist/LiveReact`, user data left on uninstall), `packaging/macos/entitlements.plist`
  (not sandboxed; network client/server, audio input; library-validation keys for the bundled Python),
  `.github/workflows/release.yml` (windows-x64, macos-arm64, macos-x86_64; Azure Trusted Signing / codesign /
  notarize only when their secrets exist). Nothing is built nightly. Tests: `tests/test_app_packaging.py`.
- **33. P6 first-run setup + About** (2026-10-28). `ui/first_run.py` `FirstRunDialog`: output (Enttec /
  Art-Net / sACN / demo), audio input, DJ Link interface (device lists injectable), demo rig or add my
  fixtures; Finish saves `settings.extra["setup"]` and `onboarding_done`; `needs_first_run()`. `ui/about.py`
  `AboutDialog`: version (package metadata) and notices (PySide6/Qt, Anthropic SDK, platformdirs, pypdf,
  keyring from their metadata; prodj-link and OFL marked "to confirm"), plus OFL attribution per imported
  fixture. Tests: `tests/test_app_first_run.py`. Showing them at start-up / in a Help menu is item 40.
- **36. P2 fixtures menu** (2026-10-28). The console has a Fixtures menu: "Fixture library…" opens
  `FixtureLibraryBrowser` (which now emits `library_changed`), "Anthropic API key…" opens the key dialog
  (item 38). `RigConfigTab.reload_definitions()` adds newly saved definitions to every row's type list.
  Tests: `tests/test_app_fixtures_menu.py`.
- **38. P4 API key settings** (2026-10-28). `ui/api_key.py` `ApiKeyDialog`: save / replace (password
  field, cleared after saving) and remove the key in the OS keychain through `store_api_key()`; shows only
  whether a key is saved; keychain errors are reported without the key. Tests: `tests/test_app_api_key.py`
  (fake keyring).
- **41. P2 the channel-test screen needs the console's laser arm switch** (2026-10-28). Laser channels on
  `ui/channel_test.py` stay at zero unless BOTH the screen's "Enable laser output" box is ticked AND
  `laser_armed()` (the console passes `manual.laser_enable` through `FixtureLibraryBrowser` and
  `FixtureWizard`) is on; no switch passed = never armed. The switch is polled every 100 ms, so a disarm
  from anywhere (button, L, MIDI, an event type) zeroes the screen; a label shows the console's arm state.
  Tests: `tests/test_app_channel_test_arm.py` (both orders, disarm zeroes, polling, no console, par
  unaffected, console wiring); `test_app_channel_test.py`'s single-key test now runs with the console armed.
- **42. P6 console Help menu** (2026-10-28). The console has a Help menu: "About LiveReact…" opens
  `AboutDialog`; "Run first-time setup again…" opens `FirstRunDialog` with the saved choices, saves only,
  and says the setup applies at the next start (wiring into `AppConfig` stays item 40).
  Tests: `tests/test_app_help_menu.py`.
- **43. P6 Help -> Keyboard shortcuts** (2026-10-28). `ui/console.py` `SHORTCUTS` (key, target, what it
  does) is the one table: `_install_shortcuts()` binds exactly these (same keys and targets as before) and
  Help -> "Keyboard shortcuts…" lists them. Tests: `tests/test_app_shortcuts_help.py` (installed keys equal
  the table, B and L still drive blackout and the arm switch, the dialog lists every key).
- **44. P6 Help -> Open my LiveReact folder** (2026-10-28). Help -> "Open my LiveReact folder…" opens
  `userdata.paths().root` in Explorer / Finder (`ConsoleWindow.open_url`, `QDesktopServices.openUrl` by
  default, a fake in tests); when that fails the status bar says where the folder is.
  Tests: `tests/test_app_data_folder.py`; `test_app_help_menu.py` now expects the fourth Help entry.
- **45. P2 delete a fixture from the library** (2026-10-29). "Delete…" in `ui/fixture_library.py` removes
  the selected user/imported definition behind a confirm box (injectable, a QMessageBox by default) that
  names the user-folder rigs still referencing the id and warns which keep loading (own snapshot) and
  which will not; the button is disabled for built-ins and empty selections. `fixtures/library.py` gets
  `delete_user_definition()` (ValueError for a built-in id, KeyError when no user file) and `rigs_using()`
  (unreadable rig files skipped). Tests: `tests/test_app_fixture_delete.py`.
- **46. P2 duplicate a fixture definition** (2026-10-29). "Duplicate…" in `ui/fixture_library.py` copies
  the selected definition — built-ins too, that is how a user adapts a model they cannot edit or delete —
  to a new user id (prompt pre-filled `<id>_copy`, injectable; refusals go to an injectable error box).
  `fixtures/library.py` `copy_definition()`: always saved **unconfirmed** (so the copy goes through the
  channel-test screen before a laser built from it may output); a builtin's copy becomes `kind="user"`,
  an import keeps its provenance (`ofl_key`, attribution); taken or invalid target ids are ValueError.
  Tests: `tests/test_app_fixture_duplicate.py`.
- **47. P2 export / import a fixture definition file** (2026-10-29). "Export…" writes the selected
  definition's JSON wherever the user picks; "Import file…" validates a picked `.json` through
  `fixtures/definition.py` and saves it to the user folder always **unconfirmed** (any confirmation
  happened on someone else's hardware; a built-in id is refused), asking before replacing an existing
  user definition. File dialogs are injectable like the other prompts. `fixtures/library.py` gains
  `export_definition()` / `import_definition_file()`, which round-trip.
  Tests: `tests/test_app_fixture_export.py`.
- **48. P3 fixture details pane** (2026-10-29). Selecting a row in the library browser shows the
  definition read-only beside the table (a splitter): `definition_summary()` lists per mode each
  channel's 1-based number, function, fine pairing and capability ranges with labels, plus source /
  ofl key / license / attribution and the confirmed date (or "not confirmed"). Cleared when nothing
  is selected. Tests: `tests/test_app_fixture_details.py`.
