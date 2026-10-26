# Look notes — telling LiveReact how ONE track should look

`tracks/<song>.look.json` sits beside the song's `.timeline.json` and is committed like it.
The planner (`--phrases drive`) honours it exactly for that track; the nightly agent reads the
notes you wrote for your labelled tracks to shape the DEFAULTS for every other track on the USB.

Start from `tracks/LOOK_NOTES_TEMPLATE.look.json` (or `tools/agent/propose_markers.py
--scaffold-look-notes`, which writes one per unlabelled track with `"_todo": true`). Remove
`_todo` when you have filled it in; a `_todo` file is ignored.

| field | values | what it does |
|---|---|---|
| `hook_scene` | a scene name from `scenes/library.py` (e.g. `"Drop Strobe Stab"`, `"Drop Wash Slam Clear"`) or `null` | the look every chorus/drop of this track uses |
| `hook_palette` | a palette name from `engines/palette.py` (`blood_red`, `cold_steel`, `deep_blue`, `uv_violet`, `hot_magenta`, `deep_amber`, `cyan_ice`, `toxic_green`, `acid_yellow`, `warm_white`, `neutral_wash`) or `null` | the hue of the drop family for this track (otherwise the track's seed picks one) |
| `big_moment` | `"auto"` (loudest drop by the waveform) · `"none"` · `{"drop": 2}` (second drop section) · `{"beat": 417}` · `{"t": 172.8}` (seconds) | which drop gets the full hit: laser fan, PEAK |
| `intro` / `outro` | `{"laser": true/false/null, "strobe": true/false/null}` | `null` = planner default (quiet edges: no laser, no strobe) |
| `strobe` | `"hard"` (count-in strobe in the last 2 bars of a build, strobe in drops) · `"soft"` (strobe only inside drops) · `"none"` | still clamped by the safety knobs either way |
| `mood` | free words; the tools understand `no-drop` / `tool-track` (no big moment, no strobe) and `clap-driven` | anything else is for the agent's report |
| `notes` | free text | what you heard, what the lights should follow |

Typos do not pass silently: an unknown key or value makes the loader raise, and
`tools/harness/import_usb.py` shows `!!` in the look column for that track.
