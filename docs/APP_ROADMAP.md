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
22. **P2 channel-test screen.** Extend `ui/fixture_test.py` for a definition-driven fixture:
    - one slider per channel, labelled by function (not just kind);
    - a capability picker ("Red: 10-19", click to send);
    - a pass/fail mark per channel and a **Confirm** button that sets `source.confirmed` + `confirmed_at`
      and saves through item 13;
    - for lasers, the user picks the values for blank / static line / fan into `laser_intents`.
    Accept: UI tests (Confirm writes the definition; an unconfirmed laser cannot be marked confirmed without
    all three intents).
23. **P2 fixture library browser + add-fixture wizard.** `ui/fixture_library.py` (browse builtin / user /
    imported definitions) and `ui/fixture_wizard.py` with three pages: Search Open Fixture Library (item 25),
    Upload manual (item 28), Enter by hand (a channel table editor producing a `user` definition). Every
    page ends on the channel-test screen. Pages whose backend is not built yet say so. Accept: UI tests.
24. **P3 OFL converter.** `src/livereact/fixtures/ofl.py`: a pure function from OFL fixture JSON to a
    definition. Covers `availableChannels`, `modes`, fine channel aliases, capability
    `dmxRange`/`type`/`color`/speed, and `physical`. Unknown capability types become `generic`. Test data:
    small OFL-format files written for the tests under `tests/data/ofl/` (not copied from OFL). Accept: tests.
25. **P3 OFL search + fetch service.** A cached copy of OFL's fixture register (manufacturer/model index)
    in the user folder, fuzzy search, and on-demand fetch of one fixture's JSON, all in a `QThread` worker
    with timeouts. Offline it reports "Open Fixture Library unavailable" and the wizard offers the other
    two pages. OFL data is fetched per user, never bundled; attribution is stored on each definition and
    listed in About. The exact register/fetch URLs are a constant to check by hand (mark it in the
    report). Accept: tests with a recorded register and a fake transport (no network).
26. **P4 manual page finder.** `src/livereact/fixtures/manual_reader/extract.py`:
    - Per-page text with `pypdf`, then score pages for DMX charts ("DMX", "CH1", "channel", value ranges
      like `000-255`, "mode").
    - Flag scanned pages (sparse text), and write a trimmed PDF of the best pages (at most 15) to
      `manuals/<sha256>/`.
    Accept: tests on a small generated PDF.
27. **P4 manual reader client (mocked).** `manual_reader/schema.py` (the JSON schema Claude must fill: a
    definition subset plus per-capability `confidence` and `source_page`) and `manual_reader/client.py`:
    - Anthropic Python SDK, model constant `claude-opus-5-5`.
    - The trimmed PDF goes as a base64 `document` block; the output must validate against the schema.
    - A cost estimate is shown before sending; typed errors.
    - The API key comes from `keyring` (Windows Credential Manager / macOS Keychain), else
      `ANTHROPIC_API_KEY`; it is never written to a file or a log.
    Accept: tests with a recorded response; the tests must not read a real key.
28. **P4 manual reader worker + review.** `manual_reader/worker.py` (`QThread`, cancellable, result cached
    by PDF sha256 so a manual is never paid for twice) and `review.py`: validate, save a `manual_ai` draft
    (`confirmed: false`), open the channel-test screen with low-confidence cells highlighted and the source
    page shown. Upload page of the wizard: one-time consent ("the PDF is sent to Anthropic"); greyed out
    with no key or offline. Accept: UI tests with a fake client.
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
32. **P6 packaging files.** `packaging/livereact.spec` (PyInstaller `--onedir`):
    - Exclude PyQt5/PyOpenGL and unused Qt modules.
    - Collect `fixtures/builtin/*.json`, `default_rig.json` and the icons.
    Also `packaging/windows/livereact.iss` (Inno Setup) and `packaging/macos/entitlements.plist`
    (network client/server for DJ Link UDP 50000-50002 and Art-Net, microphone, not sandboxed, so USB serial
    works). Add `.github/workflows/release.yml` building Windows, macOS arm64 and macOS x86_64 artifacts;
    signing steps are present but skipped without secrets. Accept: a test that parses the spec/workflow for
    the excludes and data files (nothing is built nightly).
33. **P6 first-run setup + About.** `ui/first_run.py`: output (Enttec / Art-Net / sACN / demo), audio
    device, DJ Link network interface, then demo rig or "add my fixtures"; `settings.py` remembers it is
    done. `ui/about.py`: version + third-party notices (PySide6/Qt LGPL, prodj-link, OFL attribution,
    Anthropic SDK). Accept: UI tests.
34. [JOSH] **P6 signing + clean-machine tests.** Apple Developer account and notarization; Windows Azure
    Trusted Signing or an OV certificate; CI secrets; install on a clean Windows PC and a clean Mac.
35. [JOSH] **P7 publish.** User guide, laser disclaimer / EULA and arm-checklist wording, privacy note for
    the manual reader, README screenshots, 1.0 tag and release notes.

## Decisions only Josh can make (each blocks the items named)

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
