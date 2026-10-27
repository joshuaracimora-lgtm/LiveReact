# UI bugs: the Bugs & UI agent's work list

Found 2026-10-26 by reading the code and by `tools/agent/ui_snapshot.py` / `tools/agent/venue_check.py`
(the Venue tab had never been tested on the Windows desktop: PySide6 was not installed, every UI test
skipped). The nightly Bugs & UI agent (`docs/AGENT_BRIEF_UI.md`) works this list top to bottom: a
failing test first, then the fix, then the entry moves to **Fixed** with the test's name.

Line numbers are as of 2026-10-26 and drift; the function names don't.

## Open (highest risk first)

1. **Calibration is not tied to what it was solved for.**
   - Moving a head or changing its facing after SET AIM keeps the old offsets (solved against the old
     facing, `geometry.calibrate_from_capture`), so every aim silently rotates. Invalidate (or warn and
     re-solve) on a position/facing change.
2. **Overlapping labels** (`ui_snapshot.py` counts them; the gate compares against harness). Causes in
   `FloorPlanView._layout_labels`:
   - captions are fixed 8/7 pt in scene pixels while the room is 40 px/m: small rooms run out of slots;
   - the last-resort placement ignores the reserved bands and the scene rectangle and only moves down;
   - leader lines, beams and wedges are not obstacles;
   - captions don't follow a drag until release; tooltips are never refreshed after a drop;
   - `_mark_stacks` ignores targets (a fixture on a target is not drawn as a stack).
3. **The floor plan is small at 1280 x 800**: the targets grid under it takes ~40% of the left
   column's height, so a tall room is drawn ~330 px high with tiny captions. (The property panel's
   clipping is fixed, below.) Note: `ui_snapshot.py` renders without the console's theme, which is what
   makes widgets wider in the real app.
4. **Show-tab mini-map is stale** until you leave and re-enter the Show tab (`ConsoleWindow._tab_changed`
   is the only refresh of body positions there).
5. **Save overwrites the rig's description** with a fixed string (`VenueTab._save`, `FixtureTestPanel`,
   `RigConfigTab._collect`), and the console keeps the old `rig_path` after "Save rig..." to a new file.
6. **`side` gets stuck.** `Rig.refresh_geometry` overwrites `fx.side` from x and never restores it: after
   Reset positions the sides keep their last derived values; exactly at the centre line it is never
   updated. The Rig Config tab's Side column is built once and can save a stale value back.
7. **Resizing the room leaves fixtures and targets outside it** (`VenueTab._venue_changed`); it also
    writes `height_m` into files that had none.
8. **Unplaced fixture shows X/Y but SET AIM refuses** ("place the head first"): `_load_fixture` shows the
    fallback position while `fx.position` stays None.
9. **Slew limit uses the placeholder pan/tilt range** (`MovingHead.apply_slew_limit` reads
    `PAN_RANGE_DEG`/`TILT_RANGE_DEG`), not the two-point solved range in `calibration.ranges()`.
10. **Legacy layout flips.** The legacy `layout_y` fallback (`FloorPlanView._fallback_xy`) maps the old
    canvas top (the spots row) to the back wall, while unplaced fixtures otherwise hug the booth.

## Fixed

- **Property panel clipped at 1280 x 800** (button labels cut, pan/tilt readouts "0.", help text off the
  right edge, a horizontal scrollbar). With the console's theme the panel needed 602 px in a 474 px
  column: the "Light the selected head ..." checkbox and the ADD BOUND POINT + Clear box row, then
  ADD 2ND POINT. Those three labels now take two lines (same words); the panel needs 418 px. Test:
  `tests/test_console.py::test_venue_panel_fits_a_1280x800_window_without_clipping` (2026-10-26).

- **The glyph legend was cropped** on every room under ~33 ft wide (a 436 px line placed at the room's
  left edge). It now wraps onto as many lines as the scene needs, the top band grows to fit, and the
  caption layout reserves the legend's real rectangle. Overlap counts (harness -> now): 0 on six rigs,
  junior_crib 4->1, testing_setup 4->2 (both axis numbers under a fixture), junior_crib@small 7->4. Test:
  `tests/test_floorplan.py::test_legend_fits_the_scene_in_a_narrow_room` (2026-10-26).

- **"DJ BOOTH WALL" overlapped an x-axis number on every rig** (fixed offset 10 px under the numbers).
  It is now centred one text line below them and the scene grows to fit it. Overlap counts
  (`ui_snapshot.py`, harness -> fix): 2->1 on six rigs, 4->2 junior_crib / testing_setup, 7->5
  junior_crib@small. Test:
  `tests/test_floorplan.py::test_booth_wall_title_clears_the_axis_numbers_and_stays_in_the_scene`
  (2026-10-26).

- **Set home showed a value that was silently ignored** whenever a motion box exists (the box wins in
  `MovingHead.apply_home_position`). The calibration readout now says "(unused: the motion box wins)"
  and Set home's message says to Clear box to use it; the box buttons refresh that readout. Rendering is
  unchanged (applying home inside the box is a look change: Josh's call). Test:
  `tests/test_console.py::test_set_home_says_it_is_unused_while_a_motion_box_exists` (2026-10-26).

- **The Venue tab's calibration guidance never showed**: SET AIM, ADD 2ND POINT (including its "pick
  points further apart" warning), Set home and the motion-box buttons each set a status message and then
  `VenueTab._mark_dirty()` replaced it with "Unsaved changes" (found 2026-10-26). `_mark_dirty(note)` now
  keeps the action's message in front. Test:
  `tests/test_console.py::test_venue_action_messages_are_not_overwritten_by_unsaved` (2026-10-26).

- **SET AIM / ADD 2ND POINT / ADD BOUND POINT stored a pose the real head was not in**: with Jog live off
  (or Freeze on) the sliders do not move the head, yet the buttons captured them. They now refuse with a
  status line saying to tick Jog live (or unfreeze). Test:
  `tests/test_console.py::test_set_aim_and_bound_point_need_jog_live` (2026-10-26).

- **Editing any placement box nudged the fixture** (and any target box the target): the boxes show
  0.1 ft, and `VenueTab._placement_edited` / `_target_edited` rebuilt the whole position from all three
  boxes, so a file value like 16.25 ft became 16.2/16.3 on a height or facing edit. Each box now writes
  only its own axis. (Facing on an unplaced fixture still places it at its drawn spot, as before.) Test:
  `tests/test_console.py::test_venue_tab_box_edit_writes_only_its_own_axis` (2026-10-26).

- **A plain click moved fixtures**: clicking an unplaced fixture gave it a real position (fallback xy,
  z = 1 m) and so a new `side`; clicking a placed one snapped it to 0.5 ft; clicking a target snapped it.
  `FloorPlanView.mouseReleaseEvent` committed a drop for every selected item; it now records positions
  on press and commits only items the drag moved. Test:
  `tests/test_floorplan.py::test_a_plain_click_moves_no_fixture` (2026-10-26).

- **The process died (Windows access violation) when a floor plan with a fixture selected was dropped**
  (found 2026-10-26 while testing #2: any test that clicked a body crashed pytest at teardown; in the
  console, replacing a Venue tab). The `QGraphicsScene` had no parent, so it outlived its view and
  reported the selection change to the deleted view. The scene is now a child of the view. Test:
  `tests/test_floorplan.py::test_dropping_a_plan_with_a_fixture_selected_does_not_crash` (2026-10-26).

- **Python error ("Internal C++ object (PlanItem) already deleted") printed whenever the plan was torn
  down with a fixture selected** (closing the tab / app, every `venue_check.py` run). Not the position edit
  itself: destroying the scene deletes the items and then fires selection-changed, and
  `FloorPlanView._on_selection` walked `_bodies`' deleted items. The slot now does nothing while the
  scene is being destroyed. Test: `tests/test_floorplan.py::test_destroying_the_plan_with_a_fixture_selected_raises_nothing`
  (2026-10-26).

- **Launching the console lit the first moving head full white and held off the auto-blackout**
  (`VenueTab` built inside the hidden tab selected its first fixture). The tab now pins only while on
  screen; re-entering re-lights the selected head and re-highlights it. Test:
  `tests/test_console.py::test_console_launch_pins_nothing_until_the_venue_tab_is_opened` (2026-10-26).

## How the grid drives the lights (`venue_check.py`, 2026-10-26)

Verified: aiming lands on every venue target; an X-box edit changes the very next frame of an aim scene
to the new geometry; moving a head across the centre line flips its `side` and the mirrored sweep.
4 of 27 scenes aim at grid targets (Drop Strobe Stab, Peak Full Rig, Break Single Spot Special, Outro
Single Beam -- only on heads calibrated with SET AIM); 7 only mirror by side; 16 ignore the grid. Josh
chose (2026-10-26) to verify and fix these links, not to make every sweep grid-aware.
