# Hardware Testing & Feedback Guide

This is the **exact procedure** for testing LiveReact on the real rig, and the
system for capturing what happens so you can hand it straight back to Claude for
analysis. Pair it with [`HARDWARE.md`](HARDWARE.md) (the deeper setup reference).

> First contact between this code and real devices **will** surface mismatches
> (channel maps, MIDI CCs) — that's expected and exactly what these tools are for.
> Don't try to fix code yourself; **record the session and send it back.**

---

# Part 0 — Total beginner setup (start here)

**Never used a "command line" or installed code from the internet before? Read this
part.** It walks you through everything from a blank computer to running your first
test. You do this whole part **once**. After that you only use the tests further
down the page.

You do **not** need to understand any of it — just follow the steps in order and
copy-paste the commands exactly. Each step tells you what to type, why, and what you
should see. If a step doesn't look right, stop and send a screenshot back to Claude.

> **A few words you'll see, in plain English:**
> - **Terminal** / **PowerShell** — a window where you type commands instead of
>   clicking buttons. That's the black or blue text window you'll open below.
> - **Python** — the programming language LiveReact is written in. The computer
>   needs it installed to run the app, like needing a music player to play an MP3.
> - **A command** — one line of text you paste into the terminal and press Enter.
> - **The project folder** — the `LiveReact` folder containing all the code, which
>   you'll download in Step 2.

---

## Step 1 — Install Python (version 3.12)

This gives your computer the engine that runs LiveReact. **You must use version
3.12** — newer or older versions can cause errors with the lighting hardware.

**Windows**
1. Go to **https://www.python.org/downloads/release/python-3127/**
2. Scroll to the bottom, under "Files" click **Windows installer (64-bit)** to
   download it.
3. Open the downloaded file. On the first screen, **tick the box that says
   "Add python.exe to PATH"** (bottom of the window) — this is important.
4. Click **Install Now** and let it finish, then click Close.

**Mac**
1. Go to **https://www.python.org/downloads/release/python-3127/**
2. At the bottom under "Files" click **macOS 64-bit universal2 installer**.
3. Open the downloaded `.pkg` file and click through Continue → Agree → Install.

**Check it worked.** Open a terminal (see Step 3 for exactly how) and paste this:

```
# Windows
py -3.12 --version
```
```
# Mac
python3.12 --version
```

You should see something like `Python 3.12.7`. If you instead see "not found" or a
different version number, Python didn't install correctly — redo this step (on
Windows, the most common cause is forgetting to tick "Add python.exe to PATH").

---

## Step 2 — Download the LiveReact code

This puts a copy of the app on your computer.

**The simple way (no extra tools):**
1. Go to **https://github.com/danfirsten/LiveReact**
2. Click the green **`< > Code`** button, then **Download ZIP**.
3. Find the downloaded `LiveReact-main.zip` (usually in your **Downloads** folder),
   right-click it → **Extract All** (Windows) or double-click it (Mac).
4. You now have a folder called `LiveReact-main` (or `LiveReact`). **Remember where
   it is** — for example your Desktop or Downloads. This is your **project folder**.

> Whenever Claude sends you a fix later, you'll come back here, download a fresh ZIP,
> and replace the folder. (If you happen to know `git`, `git clone
> https://github.com/danfirsten/LiveReact.git` and later `git pull` is smoother —
> but the ZIP method works perfectly.)

---

## Step 3 — Open a terminal *inside* the project folder

Every command on this page must be run **from inside the project folder**, or the
computer won't know where the app is.

**Windows**
1. Open the `LiveReact` folder in File Explorer so you can see files like
   `README.md` and `TESTING.md` inside it.
2. Hold **Shift** and **right-click** an empty area inside the folder.
3. Click **"Open PowerShell window here"** (or "Open Terminal here"). A blue or
   black window opens — that's your terminal.

**Mac**
1. Open the **Terminal** app (press `Cmd+Space`, type `Terminal`, press Enter).
2. Type `cd ` (the letters c, d, then a space) but **don't press Enter yet**.
3. Drag the `LiveReact` folder from Finder onto the Terminal window — it pastes the
   location for you — then press Enter.

To confirm you're in the right place, paste this and press Enter:

```
# Windows
dir
```
```
# Mac
ls
```

You should see a list that includes `README.md`, `TESTING.md`, and a `src` folder.
If you don't, you're not in the project folder — redo this step.

---

## Step 4 — One-time install (sets up the app)

This creates a private workspace for the app and downloads the pieces it needs to
talk to your lighting and DJ gear. It can take a couple of minutes and will print a
lot of text — that's normal. **Copy and paste each line one at a time, pressing
Enter after each, and wait for it to finish before the next.**

**Windows** (paste these one at a time):
```
py -3.12 -m venv .venv
```
```
.venv\Scripts\python -m pip install -e ".[hardware,ui]"
```

**Mac** (paste these one at a time):
```
python3.12 -m venv .venv
```
```
.venv/bin/python -m pip install -e ".[hardware,ui]"
```

When the second command finishes you'll see a line like
`Successfully installed livereact ...`. That means setup worked. If it ends in red
"ERROR" text instead, copy the whole window and send it back to Claude.

---

## Step 5 — "Turn on" the workspace (do this each time you open a new terminal)

Before running any test, you "activate" the workspace you just made. **You repeat
this one step every time you open a fresh terminal window** (it's not permanent).

**Windows**
```
.venv\Scripts\Activate.ps1
```

> **If Windows shows a red error about "running scripts is disabled on this
> system":** paste the line below once, press Enter, type `Y` and Enter if asked,
> then run the activate command above again. (This just allows the workspace to
> switch on; it's safe.)
> ```
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

**Mac**
```
source .venv/bin/activate
```

**How you know it worked:** the start of your terminal line now shows `(.venv)`. As
long as you see `(.venv)`, the plain `python ...` commands in the rest of this guide
will work.

> **Shortcut if activation keeps giving you trouble:** you can skip this step
> entirely and instead put `.venv\Scripts\` (Windows) or `.venv/bin/` (Mac) in front
> of the word `python` in any command below. For example, instead of
> `python -m livereact --doctor`, type `.venv\Scripts\python -m livereact --doctor`.

---

## Step 6 — Your first command (the health check)

Let's make sure everything is working and see what gear your computer can detect.
With `(.venv)` showing, paste:

```
python -m livereact --doctor
```

This runs a self-check and writes a file called `diagnostics/doctor.txt` inside your
project folder. **This file is the single most useful thing to send Claude first** —
it lists exactly what your computer sees (lighting interface, DJ mixer, versions).

🎉 **That's the setup done.** From here on, you only open a terminal in the folder
(Step 3), turn on the workspace (Step 5), and run the tests below. The rest of this
guide is the actual testing procedure.

---

# Part 1 — The testing procedure

## The feedback loop (how this works)

```
  1. Run a test with --record   →  creates recordings/session-<timestamp>/
  2. Watch the lights, fill in     NOTES.md in that folder (what you saw)
  3. Hand the folder back to Claude ("analyze recordings/session-<timestamp>")
  4. Claude reads logs + state + your notes, proposes/makes fixes
  5. Repeat
```

Every recorded session folder contains:

| File | What it is |
|------|------------|
| `session.log` | All output (info/warnings/errors + tracebacks) |
| `errors.log`  | Just warnings/errors — quick scan for problems |
| `state.jsonl` | What the show did, sampled ~10×/sec (BPM, beat, energy, inputs, active DMX channels). `tools/simulations/replay_state.py <session dir>` feeds it back through the real classifier to check a change against the capture |
| `meta.json`   | Your machine + detected devices + run settings at startup |
| `summary.json`| Totals (frames sent, samples) at shutdown |
| `NOTES.md`    | **You fill this in** — what you expected vs. what actually happened |

To send it back: either point Claude at the folder path, or zip it:
```
python -m livereact --bundle          # zips the latest session
```

---

## Before you start

First time on this machine? Do **Part 0** above once. Every time after that, open a
terminal in the project folder and turn on the workspace (Steps 3 and 5) so you see
`(.venv)`, then run the health check:

```
# Confirm the environment + detected devices:
python -m livereact --doctor
```

`--doctor` writes `diagnostics/doctor.txt` — **send that first**; it tells Claude
exactly what your machine sees (serial ports with VID/PID, MIDI inputs, versions).

```
# Only if you're using Pro DJ Link (the Ethernet beat-sync), install this extra
# library one time:
python -m pip install "git+https://github.com/flesniak/python-prodj-link"
```

---

## Test 1 — Fixtures, one at a time (safest; no DJ gear needed)

Goal: confirm each of the 8 fixtures is wired/addressed correctly and that every
channel does what the profile claims.

```
python -m livereact --hardware --fixture-test --record --rig rigs/josh_rig.json
```

> **Always pass `--rig rigs/josh_rig.json`** (found 2026-09-14): without it the
> app silently loads the packaged 8-fixture `default_rig.json`, whose addresses
> don't match the real rig — the MH goes dark and the laser receives MH-shaped
> data on its own address and cycles patterns by itself. Only the wash bar
> (address 69 in both files) behaves. If the wash bar is physically out, use
> `rigs/josh_rig_no_bar.json` instead.
>
> Since 2026-09-15 the show rotates only the six core looks (one per energy
> state, `scenes/library.py`'s `CORE_SCENES`); add `--full-catalog` to rotate
> the whole library again.

For **each** fixture (MH L/R, Laser L/R, Par L/R, Bar L/R):
1. Click its **Flash** button — the right fixture should light up. Note in `NOTES.md`
   if the *wrong* fixture lights (addressing problem).
2. Drag each channel slider and confirm the behavior matches the label
   (pan / tilt / dimmer / red / green / blue / strobe / …). Note any mismatch.

⚠️ **Laser safety:** raise laser channels only briefly, with beams pointed safely
above head height. Confirm the laser is OFF at slider 0.

**Expected:** every fixture identifiable; every channel does what its label says.
**Record:** per-fixture observations in `NOTES.md`.

---

## Test 2 — Network / Pro DJ Link

```
python -m livereact --net-check          # check the link-local IP + firewall first
python -m livereact --hardware --visualize --record --rig rigs/josh_rig.json
```

> **Laser arming:** arming is per track: the laser is DISARMED at every
> launch (not saved in config.json any more), press **L** (or tick "Arm lasers") for the tune
> you want it on. Once armed it sweeps once per 8-bar block in PEAK only, one bar per sweep
> (`engines/laser.py`), and never at a show with this flag.
>
> **Trigger profile (2026-10-06, `rigs/testing_setup.json`):** the lasers are
> punctuation. Dark by default; a 2-bar MOMENT lights them on the drop hit, on
> each 8-bar block of a sustained PEAK, or when the low-band wub spikes, with 8
> bars of darkness between automatic moments and at most 10 % lit over any
> 2 minutes. **J** (LASER HIT) fires one moment now; **K** (CUE LASER) holds
> the line while pressed; both skip the spacing rule. The status chip reads
> TRIGGER · READY / DROP / PEAK / WUB / HIT / CUE and the lit share. Still
> behind **L** (arm). `laser_trigger` / `laser_budget` are in `state.jsonl`.
>
> **Manual laser cue (2026-09-30):** with the laser armed, hold **CUE LASER (K)** on the
> console (a toggle — press again to release) and the dotted line shows at once, in any
> bucket, with its sweep speed following the mids + highs (synth stabs / hats): quiet
> passages leave the line all but still, a stab pushes it to full speed. Releasing hands the
> laser back to the automated PEAK sweeps. To drive it from the XDJ instead: run
> `--midi-monitor`, turn/press the control you want, and add its `"channel:cc"` to
> `midi_map.json` as `"laser_cue"` — past half-way = cued, back below = released. First live
> check: does the idle read stationary enough, and does the speed track the stabs? Read
> `laser_source` / `laser_stab` / `laser_sweep` off `state.jsonl`.

Play a track on the XDJ-XZ.

**Expected:** in the console, BPM shows the track tempo, the beat/bar counters
advance, and "clock=locked / PDL fresh". The rig pulses on the **real** beats.
**If you see "No DJ-Link device seen":** check the static IP (`169.254.50.50/16`),
that Link is on, and the firewall (UDP 50000–50002) — see `HARDWARE.md`.
**Record:** `state.jsonl` captures whether `pdl_fresh` / `bpm` / `bar` are correct.

---

## Test 2b — Pro DJ Link status packets + rekordbox phrases (2026-10-25)

Why: LiveReact has only ever read *beat* packets from the XZ. Its CDJ *status*
packets (which deck has which rekordbox track loaded, playing/on-air/master, and
the **beat counter from the start of the track**) are unicast only to peers that
announce themselves — the earlier "the XZ never sends status" note came from a
day our announce failed. With status + the phrase tables rekordbox already puts
on the USB, the lights can know "chorus in 12 beats" instead of guessing from
loudness. Nothing acts on it yet (shadow mode); this test is the proof.

```
python -m livereact --net-check                 # if it lists 169.254/16 routes on TWO interfaces:
sudo route delete 169.254.0.0/16 -ifscope en0   #   ...delete the Wi-Fi one (en0), then re-check
python -m livereact --pdl-probe --pdl-interface <nic>              # step 1: status packets
python -m livereact --pdl-probe --pdl-no-announce --duration 30    # step 2: the A/B control
python -m livereact --pdl-probe --pdl-fetch --pdl-dump tests/fixtures/pdl_dump   # step 3: phrase table
```

Load and play a track that rekordbox analyzed **with Phrase on** (rekordbox:
Preferences → Analysis → Phrase; the waveform shows Intro/Up/Chorus bars).

**Expected, step 1:** within ~5 s `hellos` climbs and `hellos_answered` ≥ 1 in the
summary; within ~10 s lines like
`P1 sd  track=175  playing  ONAIR  bpm=125.00 pitch=+1.55% beat_count=1234 beat=2`
appear for each deck and **keep coming for 90 s+** (the XZ de-trusts an
unanswered peer after ~60 s). **Step 2:** with `--pdl-no-announce` they stop —
that proves the announce is what makes the difference. **Step 3:** `[fetch]
export.pdb ... N tracks`, then the track's phrase table, and every status line
ends `| up(build) -> chorus(drop) in 17 beats`.

**The one number this test exists to measure:** play from the top and watch the
countdown hit 0 — does it land on the downbeat you hear (and the XZ's own phrase
display)? If it is early/late by a beat, note it: that becomes `PSSI_BEAT_OFFSET`
in `core/metronome.py`. Also note what `beat_count` does on a cue, a hot cue and a
loop, and whether `ONAIR`/`MASTER` ever show with both decks playing.

**Drive mode (the track plan runs the lights).** Once the probe passes, launch with
`--phrases drive`: at load the plan is logged (`rekordbox plan: ... hook=...`), each
section boundary logs `rekordbox plan: chorus(drop) -> drop BIG MOMENT | scene ...`,
and `state.jsonl` has `plan_active`, `planned_scene`, `big_moment`, `beats_to_drop`.
Expect: every chorus the same look + colour; only the loudest chorus gets the fan /
PEAK; the strobe counts in over the last 16 beats and the rig goes black on the last
beat before the drop; intro/outro dim with no laser. Pull the Ethernet cable
mid-track: after ~3 s (a 1 s status gap is ridden out) `plan_active` drops and the
loudness classifier carries on; plug it back and the plan re-applies its section.
The probe cannot run beside the app: only one process may own UDP 50000-50002.
Offline first: `python tools/harness/run_sim.py tracks/<x>.mp3 --pssi <ANLZ0000.EXT> --drive`
then `tools/harness/score.py` on the result -- the same scorer as the nightly agent.

**If no status after 15 s** the probe prints the checklist (routes, NIC, firewall,
Link). **Record:** the probe's summary + the `--pdl-dump` folder (small; commit
the PSSI slices as fixtures). Then a normal `--hardware --record` run: `state.jsonl`
now has `track_id`, `track_beat`, `phrase_label`, `section_kind`, `beats_to_next`,
and `session.log` has one `rekordbox: ... | classifier ... | agree/DISAGREE` line
per section — that log is what decides whether Stage 3 (phrases drive the look)
is worth building.

---

## Test 3 — MIDI (the XDJ mixer)

The default CC map is a guess and almost certainly needs remapping for your unit.

```
python -m livereact --midi-monitor --record
```

Move each control and watch the printed `channel`/`cc`:
- channel faders A and B
- crossfader
- EQ low/mid/high on each channel

Write down which `channel`/`cc` each control reports. (This is captured to
`midi-monitor.txt` in the session folder.) Then either send it to Claude to build
your `midi_map.json`, or fill it in yourself (keys are `"channel:cc"`):
```json
{ "5:31": "crossfader", "5:23": "fader_a", "5:24": "fader_b" }
```

**Expected:** moving controls prints CC events. (Some controls may be HID and not
appear at all — note which.)

---

## Test 4 — Full end-to-end

```
python -m livereact --hardware --visualize --record --rig rigs/josh_rig.json
```

Play a set: a track, a build, a drop, then mix into another track.

**Expected:**
- lights pulse on the beat, color follows the EQ
- scenes rotate every ~16 bars
- a drop's PEAK (8 bars in) brings one laser fan sweep per 8-bar block (once armed with **L**) + bigger movement
- press **C** ("Cue build") when a build starts that the lights aren't reacting to: BUILD commits
  now and holds (strobe ramp, wash ramp) until the drop lands, 32 bars pass, or you press C again.
  Exists because a kick-only intro reads louder than a mid-heavy build to a loudness classifier
  (2026-09-16) — the DJ sees the build, the RMS doesn't
- **the drop is armed, not guessed; the strobe follows the rise** (2026-09-17, after runs 7-10):
  press **D** ("Arm drop") any time in the 32 bars before a drop — it changes nothing on stage by
  itself (C is the build cue). From then on LiveReact watches
  for the *rise* — the high band's level and hi-hat/snare density climbing against what the track
  was doing a few bars earlier — and runs the strobe only while that is happening, starting slow
  and getting faster with the intensity (`rising` / `rise_index` in `state.jsonl`). The drop is
  cut by the first kick *pattern* (two kicks within a beat) at drop level — once a drop has been
  heard, at least 60 % of its kick peak. The strobe stops the instant a snare roll ends (the
  breath) and stays off until the hit. A snare roll arms the
  drop by itself; so does a strong rise once the hats/snares clearly thicken (console: "DROP
  ARMED (intensity rising)"). Neither changes the look until the drop cuts. Unarmed, the loudness
  classifier still commits a drop, but only after two full bars of drop-level sound — late rather
  than wrong. A loudness-guessed BUILD gets its wash ramp and look but no strobe.
- **hard cut (D again)** (2026-09-19, after a kick re-entry that was a drop by every measure spent
  the arm 37 s before the real drop): press **D a second time** while armed and the drop cuts on
  the **next downbeat** whatever the audio says — count it in like a DJ. A press within the first
  three quarters of beat 1 counts as that downbeat; later waits for the next bar. Console shows
  "DROP CUT — lands on the next downbeat". A third press cancels both the cut and the arm. Use
  this on riser-only tracks where the build has no kick or snare markers; for the pre-drop strobe
  on those tracks, use the manual STROBE BURST in the last bar. Without Pro DJ Link the cut is
  immediate.
- after the set's first drop, a build the loudness can't see may cue ITSELF (2026-09-17,
  `core/build_rule.py`): once a drop has set the track's kick level, four bars of the kick back at
  30-65 % of it while the classifier still says BREAK raises the same held BUILD (console shows
  "auto"; `build_cue_src` = "auto" in `state.jsonl`). It lets go by itself if the kick disappears
  for 4 bars without a drop. Pressing C during an auto cue confirms it (never cancels). It cannot
  see a set's FIRST build — no drop to measure against — so C is still the tool there. Scored on
  every capture to date with zero false starts; if it ever raises a build that isn't one, note
  the time and hand over the recording — that's a threshold to re-score, not to tune by ear.
- a snare roll into the drop pushes the build strobe to its max with a full-brightness flash
  (`snare_roll_active` in `state.jsonl`, `roll_path` says whether the LEVEL or the onset
  DENSITY path caught it). New 2026-09-17: `onset_rate` (high-band onsets/s, measured at
  audio block rate) is what the density path reads — expect ~2-5 on a groove, ~8-9 on
  16th-note hats, higher on a roll. **First run with this column tunes it**: note the time
  of every real roll you hear, then compare `onset_rate` there against the bars before it.
- **Synth Sync is ON at every startup** (Josh, 2026-09-19): the par + wash bar pulse with the
  synth stabs instead of following the bucket model. The automated strobes (build ramp, roll
  accent, drop-tail burst) cut through it and hand the wash straight back afterward. Press **S**
  (or untick "Synth Sync") when you want the mellower bucket-model look; press S again to return.
- crossfading into the next track is detected (console shows TRANSITION) and the
  show settles into a new look afterward
- the operator controls (brightness, blackout, freeze, skip) all respond
- the classifier reacts from the first seconds of music, not after ~40 s: the loudness
  normalizer is seeded from the last real session's range (saved to `config.json` at
  shutdown once it has heard 40 s of music; demo runs never save it). `state.jsonl`'s
  `norm_seeded` is true while that seed still carries weight. The seed is only used when
  the input gain and audio device match the session that saved it — after re-gaining the
  mixer, at a new venue, or on the laptop-mic fallback, launch with `--no-norm-seed` (or
  just let the first 40 s be BREAK, as before). Cost, disclosed: a kick-only intro that the
  RMS reads as loud will now read as a DROP from the start instead of hiding inside the
  warm-up — same limitation as the C cue exists for, just no longer masked.

**Record:** the whole thing. Note anything that felt wrong (timing, color, a fixture
misbehaving) in `NOTES.md`.

---

## Quick troubleshooting

| Symptom | First thing to check |
|--------|----------------------|
| No lights at all | `--doctor` shows the Enttec (FTDI VID `0x0403`)? DMX terminator on? |
| Wrong fixture responds | DMX address/mode on that fixture vs. `default_rig.json` |
| A channel does the wrong thing | Channel map in `fixtures/profiles.py` (send the session, Claude fixes it) |
| "No DJ-Link device seen" | Static IP `169.254.50.50/16`, Link on, firewall UDP 50000–50002 |
| Beat not locking | `state.jsonl` — is `bpm` right and `pdl_fresh` true? Else network/PDL issue |
| MIDI does nothing | `--midi-monitor` — do controls show up? Enable Utility→MIDI on the XZ |
| Lasers won't light | Arm them (**L**) — disarmed at every launch; they only sweep on 8-bar boundaries in PEAK. **K** (CUE LASER) shows the line by hand at any time once armed |

---

## What to send back to Claude

For any issue, the most useful bundle is:
1. `diagnostics/doctor.txt` (or `.json`)
2. the whole `recordings/session-<timestamp>/` folder (especially `NOTES.md`,
   `errors.log`, and `state.jsonl`)
3. for MIDI: the `midi-monitor.txt` capture

Then just say: *"analyze this session and tell me what's wrong / fix it."*

## Test 5 — Venue floor plan + moving-head aim calibration (2026-09-18)

The console's **Venue** tab draws the rig on a scaled floor plan (you type
feet; the rig file stores meters) and calibrates a moving head so scenes can
aim it at real places (the DJ booth, the dance-floor centre). Nothing is
written to disk until you click **Save rig…**.

Launch as usual, with the head connected:

```
python -m livereact --hardware --visualize --rig rigs/josh_rig.json
```

1. **Venue** tab → type the room's Width / Length (ft). Drag the orange
   `dj_booth` diamond to where the booth really is (its Height box is the
   tabletop, ~4 ft).
2. Pick the moving head in **Fixture → Select**. Type its X (along the booth
   wall), Y (out into the crowd), Height (lens above the floor) and Facing
   (0 if the body points straight into the crowd).
3. Selecting the head lights it (open white, full; "Light the selected head"
   is ticked by default, and the no-music blackout is held off while a head
   is pinned, so this works with the decks stopped -- 2026-10-05). Tick
   **Jog live** (Freeze must be off). Slide Pan/Tilt until the real beam
   sits on the booth. It moves through the normal slew limit, so it glides.
4. Target = `dj_booth` → click **SET AIM**. The label under it shows the
   captured offsets.
5. Target = `dancefloor_centre` → click **Aim at target**. The head should
   land on the floor centre within about a foot. Swings the wrong way → tick
   **Invert pan** (or tilt), SET AIM again on the booth, re-verify.
   Consistently short or long on every target → the profile's
   `PAN_RANGE_DEG`/`TILT_RANGE_DEG` placeholders are wrong for this unit
   (note by how much).
6. Optional: **Set home = target** with `dancefloor_centre` so every other
   scene's ±10 % motion is centred on the floor. Untick Jog live. **Save rig…**

Also worth checking while the head is pinned: nudge Pan by the smallest
slider step and watch for jitter. If the unit ignores or twitches on the
fine channels (CH2/CH4), set `MovingHead.USE_FINE_CHANNELS = False` in
`src/livereact/fixtures/profiles.py` and say so in the session notes.

**Laser CH7 probe** (bench only, no crowd, Arm lasers on):
select the laser, tick **Probe static line live**, sweep **H offset** 0 → 127.
Record which of these happens: the line moves sideways (0-127 really is a
static position), it stays put (it isn't), or it starts sweeping (both halves
are speed). Untick, let a real sweep fire on a drop, and note whether the
sweep's centre moved. Put the answer in `Laser`'s docstring.

## Test 6 — The bar: laser bench probe + the feature laser profile (2026-10-01)

At the bar the lasers are the show: `rigs/bar_rig.json` sets
`"laser_profile": "feature"`, and `engines/laser_feature.py` keeps both lasers
lit for the whole set — a slow line that changes colour through breaks and
builds, the fast fan after an intense drop. Same audio/beat analysis as home.
The console's status strip shows **Laser: FEATURE · CALM / TENSION / GROOVE /
FAN** beside the bucket, and the banner says what each laser is showing.

Three parts, in order. (a) and (b) happen at home; (c) is the first night.

### (a) Bench probe — map the bytes the new looks need (home, one laser)

Only the dotted line + sweep is verified on this unit. The "solid line",
"curvy line", a line held still, and colours drifting on their own are all
unmapped. `tools/laser_probe.py` holds raw channels through the Enttec with
NO safety gate in the loop — bench only, no crowd, beam path confirmed safe,
you watching the unit. It blacks out on every exit, including Ctrl-C, and
appends each hold to `docs/laser_findings.md`.

```
# the verified line first, so you know the unit is listening:
python tools/laser_probe.py --preset line --seconds 10

# then each candidate (press Enter between steps, type what you saw first):
python tools/laser_probe.py --candidate solid_line
python tools/laser_probe.py --preset line --sweep 3 --from 0 --to 240 --step 16    # curvy line?
python tools/laser_probe.py --preset line --sweep 7 --from 0 --to 120 --step 24    # still line, where?
python tools/laser_probe.py --candidate colour_drift
python tools/laser_probe.py --candidate rainbow_line
python tools/laser_probe.py --preset line --sweep 4 --from 0 --to 120 --step 24    # rotated line?
python tools/laser_probe.py --candidate painting
```

Skip `raise_line` (CH8) unless the unit is on its real mount: it may move the
beam vertically. Every candidate is listed with the QLC+ file's claim in
`Laser.PROBE_CANDIDATES` (`src/livereact/fixtures/profiles.py`); `--dry-run`
prints the frames without hardware. A value becomes a real constant only
with a findings row saying what it actually did.

### (b) First feature run — home mount, with a track

Arm with L once the track is playing.

```
python -m livereact --hardware --visualize --record --rig rigs/bar_rig.json
```

Watch for: a lit, slowly moving line from the first bar (CALM, blue / cyan);
the colour stepping every 8 bars with the two sides one step apart; the
sweep picking up on a rising build (TENSION); the fan on the big drop and
holding through it (FAN; a softer drop reads GROOVE, a medium sweep); the
line settling back at the breakdown. K forces the fan if a drop was under-
read. The replay says what to expect first:

```
python tools/simulations/replay_laser.py recordings/<the new session>
python tools/simulations/replay_laser.py recordings/session-2026091*     # every capture so far
```

Hand the session folder back as usual; `laser_mood`, `laser_target`,
`laser_color_l/r` are in `state.jsonl`.

### (c) The bar — venue entry, the beam check, then arm

1. Set the units to the bar rig's addresses (a fresh rig, laid out in
   order): wash bar **1** (6-channel mode), Laser L **7**, Laser R **19**
   (12-channel mode). `rigs/bar_rig.json` already says so.
2. Launch: `python -m livereact --hardware --visualize --record --rig rigs/bar_rig.json`.
3. **Venue** tab: type the room's Width / Length (ft), place the bar and
   both lasers (X, Y, Height 9 ft, Facing), tilt 0. **Save rig…**
4. Arm lasers (L) once the first track is playing. Everything else is
   automatic; C / D / K are the cues.
5. Afterwards: notes + the session folder.
