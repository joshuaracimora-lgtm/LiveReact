# Laser channel findings (Shehds 3W, 12-channel mode)

The evidence trail behind `fixtures/profiles.py`'s `Laser` constants.
`tools/laser_probe.py` appends a row per hold; add rows by hand for anything
seen another way. Only what is written here as SEEN on the real unit is
verified -- the QLC+ fixture file's names have been wrong before.

Verified so far (2026-09-06, isolated channel probing; see the `Laser`
docstring): CH1 42-83 manual; CH2=118 flattens the circle into a line;
CH3=0 held throughout; CH7 128-255 sweep speed (0-127 untested); CH9 must be
0 (any other value self-animates the size); CH11=190 turns the outline into
dots; CH12 green (32-39) confirmed, the other bands inferred from the file.

Open questions for the bench session (TESTING.md Test 6): CH11 < 128 = a
solid outline? CH3 sub-patterns inside the CH2=118 group = a curvy line?
CH7 0-127 = a still line at a position? CH4 0-127 = a rotated line? CH8
0-127 = vertical position (beam HEIGHT: this one matters for the 2.7 m
plane)? CH12 64-127 = colours drifting on their own, 128-191 = a rainbow
along the line? CH10 = "painting" speed?

## The manufacturer's manual (found 2026-10-29)

The unit is the SHEHDS "3000mW RGB Laser" (SKU SHE-La3WRGB-BPUS, the 2023 listing; menu
Setting > Channel = 12CH / 25CH, firmware "lasersystem 2.8"). SHEHDS has since reused the SKU
and the product page for a different board (Constellaser, 16/20CH, CUE groups) and removed the
old manual from its Drive; a copy survives at temposhop.es (`Laser_3W.pdf`) and is saved as
`manuals/shehds_3w_laser/SHEHDS_3000mW_RGB_Laser_12-25CH_manual.pdf` (gitignored; sha256
ad88af4c...). Its 12CH table (page 4) matches the QLC+ `Shehds-3000mW-RGB-Laser.qxf` value for
value and matches every hardware row below. The same OEM board ships as the Levenly M3000
(`OEM_sibling_Levenly_M3000_12-25CH_manual.pdf`, clearer English) and the Newfeel F8.
`fixtures/builtin/shehds_3w_laser.json` now carries every range with `confidence` 1.0 (seen
here) or 0.9 (printed in the manual, not yet seen).

What the manual settles that the rows below could not:

- **CH9 scale**: 0-63 is a STATIC size (0 = full), 64-127 shrink speed, 128-191 grow speed,
  192-255 in-and-out speed. Every nonzero value probed on 2026-09-06 (64, 96, 128) sat in the
  speed bands, so "any nonzero value animates" was an artefact of which values were tried.
  1-63 is still unseen; `Laser.SCALE_STATIC = 0` stays the safe choice until it is.
- **CH11 scan**: 0-127 = scan speed with a solid outline, 128-255 = dots ("Pattern Lattice").
- **CH10 painting**: 1-127 / 128-255 = two styles of the pattern drawing itself in gradually.
- **CH12 colour**: 0-7 the pattern's own colours; 8-63 the seven fixed bands; 64-127 colours
  auto-change; 128-191 "line rainbow" / "flowing water" along the line; 192-255 colour follows
  the painting.
- **CH4/5/6**: 1-127 a static angle (Z rotation, X tilt, Y tilt), 128+ a speed.
- **CH7/8**: 0-127 a static position, 128-255 a sweep speed (CH7 128+ confirmed below).
- **CH2 groups / CH3 patterns**: 8 groups of 32 x 16 patterns of 16. The manual does NOT name
  the shapes; the QLC+ file's names were a guess and were wrong. Mapping which shape sits at
  which value is the only thing still needing the bench.

Rows marked SEEN below remain the authority where they and the manual disagree.

| when | CH1..CH12 | note |
|---|---|---|
| 2026-09-06 | `60,118,0,0,0,0,128,0,0,0,190,35` | the verified dotted line, slowest sweep, green -- "success... this looks really good" |
| 2026-09-06 | `60,118,0,0,0,0,64,0,0,0,190,35` | CH7=64 (static half) during isolation: NOT assessed -- what the line did at 64 was never written down |
| 2026-09-06 | `60,118,0,0,0,0,128,0,128,0,190,35` | CH9=128: size grows steadily on its own (file said "static zoom") |
| 2026-09-06 | `60,118,0,0,0,0,128,0,64,0,190,35` | CH9=64: size shrinks steadily |
| 2026-09-06 | `60,118,0,0,0,0,128,0,96,0,190,35` | CH9=96: sawtooth -- shrink then snap back |
