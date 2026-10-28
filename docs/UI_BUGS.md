# UI bugs: the Bugs & UI agent's work list

Found 2026-10-26 by reading the code and by `tools/agent/ui_snapshot.py` / `tools/agent/venue_check.py`
(the Venue tab had never been tested on the Windows desktop: PySide6 was not installed, every UI test
skipped). The nightly Bugs & UI agent (`docs/AGENT_BRIEF_UI.md`) works this list top to bottom: a
failing test first, then the fix, then the entry moves to **Fixed** with the test's name.

Line numbers are as of 2026-10-26 and drift; the function names don't.

## Open (highest risk first)

1. **Calibration is not tied to what it was solved for.**
   - Moving a head or changing its facing after SET AIM keeps the old offsets (solved against the old
     facing, `geometry.calibrate_from_capture`), so every aim silently rotates. The tab now WARNS
     (fixed below); whether to re-solve from the stored capture (right if the old numbers were a typo)
     or keep the offsets (right if the head was re-hung the same way) is Josh's call. Moving a TARGET a
     capture was taken on now warns too (fixed below); same open question for it.
2. **Overlapping labels** (`ui_snapshot.py` counts them; the gate compares against harness). Causes in
   `FloorPlanView._layout_labels`:
   - captions are fixed 8/7 pt in scene pixels while the room is 40 px/m: small rooms run out of slots;
   - leader lines, beams and wedges are not obstacles;
3. **Show-tab mini-map is stale** until you leave and re-enter the Show tab (`ConsoleWindow._tab_changed`
   is the only refresh of body positions there). 2026-10-26: could not reproduce -- a Venue-tab move and a
   room resize both showed on returning to Show (`_tab_changed` rebuilds it on index 0). Needs the exact
   steps Josh saw it with.
4. **Slew limit uses the placeholder pan/tilt range** (`MovingHead.apply_slew_limit` reads
    `PAN_RANGE_DEG`/`TILT_RANGE_DEG`), not the two-point solved range in `calibration.ranges()`.
5. **Legacy layout flips.** The legacy `layout_y` fallback (`FloorPlanView._fallback_xy`) maps the old
    canvas top (the spots row) to the back wall, while unplaced fixtures otherwise hug the booth.

## Fixed

- **On a small room an edge target's caption landed on another caption** (junior_crib@small:
  venue_edge_right on back_wall's "7′ up"): every slot beside a right-wall target ran past the scene, so
  the least-overlap fallback was used. Before that fallback, `_layout_labels` now tries the same slots up
  to one caption-width past the side of the scene and grows the scene to take it. Only reached when no
  slot inside is free: caption positions on all eight real rigs are unchanged (compared before/after);
  junior_crib@small overlaps 1 -> 0, though several of its captions now sit further from their bodies
  (leaders). Test: `tests/test_floorplan.py::test_an_edge_targets_caption_does_not_land_on_another_caption`
  (2026-10-28).

- **Captions stayed behind during a drag** until the mouse was released. `FloorPlanView.mouseMoveEvent`
  now carries each moved body's caption and leader with it; the release re-lays them out as before (also
  when the drop snaps back to where it started). Tests:
  `tests/test_floorplan.py::test_captions_follow_a_drag_before_release`,
  `::test_a_drag_that_snaps_back_puts_the_caption_back` (2026-10-28).

- **A target under a fixture was hidden and unclickable**: `_mark_stacks` ignored targets, so
  junior_crib's dj_booth / booth_edge diamonds were drawn beneath the wash bar and lasers and a click
  there picked the fixture. A target covered by a fixture glyph is now drawn above the fixtures
  (re-checked after every drop / box edit); elsewhere it stays under them. The dashed-ring stack stays
  fixtures-only, as the legend says. Test:
  `tests/test_floorplan.py::test_a_target_under_a_fixture_stays_visible_and_clickable` (2026-10-28).

- **A fixture's or target's tooltip kept its old position after a drag** (only `refresh_layout` set
  tooltips; a drop does not rebuild the plan). The drop commits and the placement-box path now rewrite
  the tooltip from the new position. Test: `tests/test_floorplan.py::test_tooltips_follow_a_drag`
  (2026-10-28).

- **Axis numbers were drawn under bodies**: a fixture on the booth wall (testing_setup's wash bar) hung
  over the x-axis "15′"/"20′", and a target on the left wall over the y-axis "25′" (junior_crib); the
  numbers sat at a fixed offset from the room edge. `FloorPlanView._clear_axis_numbers` moves the whole
  row (with the wall title) / column out past whatever hangs over the edge, the scene grows, and the
  caption layout now reserves the numbers where they actually are. Overlaps (harness -> now):
  junior_crib 1->0, testing_setup 2->0, junior_crib@small 2->1, the six others 0->0. Test:
  `tests/test_floorplan.py::test_axis_numbers_are_not_drawn_under_a_body` (2026-10-28).

- **Moving a target a head was captured on was silent** (box edit or drag): the SET AIM / ADD 2ND
  POINT offsets were solved against the target's old spot and are kept. The status line now names the
  head(s) and the target and says to check with Aim at target / SET AIM again. Nothing is re-solved.
  The Venue status line now wraps (two warnings plus "Unsaved changes" outran 1280 px). Test:
  `tests/test_console.py::test_moving_a_target_a_head_was_captured_on_warns` (2026-10-28).

- **Rig Config's Side column was stale**: built once, so after a Venue-tab move it still showed -- and
  Save wrote back -- the side the fixture had at launch. Opening the tab now refreshes the column from
  the rig; a placed fixture's side is shown locked ("Set by where the fixture is placed on the Venue
  tab", its x decides it on load anyway), an unplaced one stays editable. Test:
  `tests/test_console.py::test_rig_config_side_column_follows_a_venue_tab_move` (2026-10-28).

- **`side` got stuck at the centre line**: `Rig.refresh_geometry` set `side` from x and never restored
  it, so a head dragged right and then onto the centre line (or un-placed, or the floor plan removed)
  stayed "right" although the file says "left". The rig now remembers each fixture's declared side and
  gives it back whenever x can't decide. Rigs loaded from file get exactly the sides they got before.
  Test: `tests/test_rig.py::test_side_at_the_centre_line_or_unplaced_falls_back_to_the_declared_side`
  (2026-10-28).

- **On a crowded small room a caption was pushed out of the scene** onto the wall title (the last-resort
  placement stacked it straight down, ignoring the scene and the reserved bands). It now takes the slot
  inside the scene that overlaps least (bodies, captions and bands). junior_crib@small overlaps 7 -> 2
  (harness -> now). Test:
  `tests/test_floorplan.py::test_crowded_small_room_keeps_every_caption_inside_the_scene` (2026-10-26).

- **The floor plan was small at 1280 x 800**: the targets grid under it took ~40% of the left column, so
  a tall room was drawn ~330 px high. The targets grid now sits at the bottom of the scrollable right
  panel (its rebuilt boxes get the panel's wheel guard) and the plan has the whole column (~690 px at
  1280 x 800). Captions are still small on long rooms (see Overlapping labels). Note: `ui_snapshot.py`
  renders without the console's theme. Test:
  `tests/test_console.py::test_floor_plan_gets_the_left_columns_height_at_1280x800` (2026-10-26).

- **Editing one room dimension rewrote all three, and shrinking the room was silent**:
  `VenueTab._venue_changed` rebuilt width, length and ceiling from the boxes (0.1 ft rounding, and a
  `height_m` written into files that had none), and fixtures/targets left outside were not mentioned.
  Each box now writes only its own dimension, and the status line names whatever is outside the room
  (nothing is moved: clamping them would change aims). Test:
  `tests/test_console.py::test_room_box_edit_writes_only_its_dimension_and_warns_about_what_falls_outside`
  (2026-10-26).

- **Moving or re-facing a head after SET AIM was silent** (the offsets solved for the old geometry are
  kept). A box edit or a drag of a captured head now says its aim was captured at the old
  position/facing: check with Aim at target, SET AIM again if the beam misses. Nothing is re-solved.
  Test: `tests/test_console.py::test_moving_a_calibrated_head_warns_that_its_aim_was_captured_elsewhere`
  (2026-10-26).

- **Reset positions left every fixture on the side last derived from its x** (a left head dragged right
  stayed "right" with no position). The Venue tab keeps the sides the rig had when it opened and Reset
  positions gives them back. (2026-10-28: now done by `Rig.refresh_geometry` itself, which gives an
  unplaced fixture its declared side; the tab's own copy was removed.) Test:
  `tests/test_console.py::test_reset_positions_restores_each_fixtures_side` (2026-10-26).

- **An unplaced fixture showed X/Y, yet SET AIM said "Place the head first"**: the boxes show where it
  is drawn, not a position. The Fixture box is now titled "Fixture — not placed yet" until it is placed,
  and the refusal says the X / Y shown is only where it is drawn and to type X, Y, Height or drag it.
  Test: `tests/test_console.py::test_an_unplaced_head_says_so_in_the_panel` (2026-10-26).

- **Save replaced the rig's description with a fixed string, and the next Save still offered the old
  file** (`VenueTab._save`, `RigConfigTab._collect/_save`, `FixtureTestPanel._save_home_position`). The
  rig's own description is kept (the stock line only fills an empty one) and each panel remembers the
  path it just wrote. (Each tab still keeps its own path: a Venue save does not move the Rig Config tab's
  default.) Tests: `tests/test_console.py::test_saving_keeps_the_rig_description_and_remembers_the_new_path`,
  `::test_fixture_test_home_save_keeps_the_rig_description` (2026-10-26).

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
