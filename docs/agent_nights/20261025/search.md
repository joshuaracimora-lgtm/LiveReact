# Search summary — 2026-10-25 03:20

- stopped: wall-clock budget
- trials: 66, accepted: 2, 1.52 h
- tuning compliance: 0.3226 → **0.367**
- hold-out compliance: 0.32 → **0.32**
- critical fails (tuning): 26 → 23

## Best parameters (vs default)

- `BUILD_CUE_HOLD_BARS`: 32.0 → **25.4393**
- `ROLL_ARM_HOLD_BARS`: 4.0 → **2.7133**
- `ROLL_ARM_MIN_S`: 1.5 → **1.8668**
- `ROTATION_BARS`: 16 → **24**

## Rule changes per track

- **messy _laszewo edit_ - Åaszewo - Sound** (tuning) 0.31→0.517: TIME-11: FAIL→PASS; TIME-13: FAIL→PASS
- **Michael_Jackson_-_Thriller_BVRNOUT_Haunt** (tuning) 0.333→0.333: no change
- **Namerakana_2_0_KLICKAUD.mp3** (hold-out) 0.471→0.471: no change
- **One_More_Time_Radio_Edit_KLICKAUD.mp3** (tuning) 0.5→0.5: no change
- **Sam Smith, Disclosure - Latch (Original ** (hold-out) 0.179→0.179: no change
- **Somebody_To_Love_CHALANT_Close_Friends_O** (tuning) 0.343→0.343: no change
- **Sweet_Nothing_2025_Edit_KLICKAUD.mp3** (tuning) 0.25→0.25: no change
- **SWEETER_KLICKAUD.mp3** (tuning) 0.241→0.345: TIME-02: FAIL→PASS
- **Talk_To_You_ft_54_Ultra_KLICKAUD.mp3** (hold-out) 0.31→0.31: no change
- **Zedd, Jon Bellion - Beautiful Now (Exten** (tuning) 0.281→0.281: no change

## Still failing (best)

- messy _laszewo edit_ - Åaszewo - Sound: TIME-02, TIME-04, TIME-05, TIME-06, STRUCT-03, STRUCT-05
- Michael_Jackson_-_Thriller_BVRNOUT_Haunt: TIME-11, TIME-02, TIME-04, TIME-05, LOOK-05, STRUCT-02, STRUCT-04, STRUCT-07, STRUCT-08
- Namerakana_2_0_KLICKAUD.mp3: TIME-11, TIME-02, STRUCT-01
- One_More_Time_Radio_Edit_KLICKAUD.mp3: TIME-02, TIME-04, TIME-06, STRUCT-01, STRUCT-03, STRUCT-05
- Sam Smith, Disclosure - Latch (Original : TIME-11, TIME-02, TIME-13, TIME-04, TIME-05, LOOK-05, TIME-06, STRUCT-03, STRUCT-08, STRUCT-05
- Somebody_To_Love_CHALANT_Close_Friends_O: TIME-11, TIME-02, TIME-04, TIME-05, TIME-06, STRUCT-03, STRUCT-04, STRUCT-07, STRUCT-05, STRUCT-06
- Sweet_Nothing_2025_Edit_KLICKAUD.mp3: TIME-11, TIME-02, TIME-13, TIME-04, TIME-05, LOOK-05, TIME-06, STRUCT-03, STRUCT-04, STRUCT-07, STRUCT-08, STRUCT-05
- SWEETER_KLICKAUD.mp3: TIME-11, TIME-13, TIME-04, TIME-05, STRUCT-03, STRUCT-04, STRUCT-07, STRUCT-09
- Talk_To_You_ft_54_Ultra_KLICKAUD.mp3: TIME-11, TIME-02, TIME-13, TIME-04, TIME-05, TIME-06, STRUCT-07, STRUCT-05
- Zedd, Jon Bellion - Beautiful Now (Exten: TIME-11, TIME-02, TIME-13, TIME-04, TIME-05, TIME-06, STRUCT-01, STRUCT-05, STRUCT-06
