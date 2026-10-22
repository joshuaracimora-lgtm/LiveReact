"""Snare-roll START detection from sub-beat periodicity (2026-10-22, engine
family 1 / STRUCT-03).

Josh's drop begins at the ROLL, not at the slam (spec/PENDING_RULES.md:
roll -> cut -> groove), and the scorer's STRUCT-03 asks the engine to be in
DROP within two beats of the labeled roll start. The rolls in the labeled
tracks are QUIET: Somebody To Love's three rolls sit at energy_norm ~0.1
with the kick on every other beat, Sweet Nothing's second at 0.07, Beautiful
Now's at 0.1-0.2. Nothing level-based sees them -- the loudness classifier,
the hit lead, the rise detector (high band + onset density) and the roll
accent (core/snare_trigger.py, which reads 8th-note hats as a roll and
saturates on a real one) were all tried against the dumps and none fires
within two beats of a labeled roll.

What a roll IS in this project's own features: a train of snare hits at the
8th or 16th subdivision, and a snare's body lives at 150-500 Hz
(inputs/audio.py SNARE_HZ, `band_snare_abs`). The autocorrelation of that
band's block-rate envelope at the 8th-note and 16th-note lags steps from
~0.05 to 0.5-0.9 the bar the roll starts (offline study over the ten
ear-labeled tracks, 2026-10-22):

    Somebody To Love  rolls at bars 44-46 / 68-71 / 100-103:  54 59 86 / 49 56 78 87 / 41 70 71 81
                      everything else in the track <= 31
    Thriller          rolls at bars 40-46 / 76-82:            68 45 54 47 40 70 64 / 51 55 77 64 54 65 58
    Beautiful Now     bars 60-62 / 92-98:                     86 89 92 / 84 83 78 78 83 93 93
    SWEETER (clap-driven, no roll), Latch, messy, One More Time's fills: nothing sustained

(x100, max of the two lags over one bar.) The known confounder is an
8th-note bassline in the same band -- Sweet Nothing's piano-house intro
reads 0.3-0.7 for twenty bars -- so a roll START is a STEP: the score must
clear ROLL_PERIOD_ON after ROLL_PERIOD_BASE_BARS bars of median score under
ROLL_PERIOD_BASE_MAX, hold for ROLL_PERIOD_HOLD_BEATS, and the band must
carry real level (near-silence is scale-free noise and correlates with
itself: Namerakana's three-beat gap before its fill read 0.56). With those
guards the study gave zero false starts on eight of the ten tracks, two on
Sweet Nothing (its bassline texture, 16 s and 82 s) and one on Beautiful Now
at 242 s past the last label (a roll by the strip; most likely a real third
drop). Latency on the real rolls: Thriller +1.5/+2.2 beats, Somebody To Love
+2.2/+10/+5 (its second and third rolls start softer), Beautiful Now
+1.6/+1.6. Thresholds are first guesses from that study -- params.json
knobs, for the loop to tune; all read at call time so `livereact.params`
can vary them.

Feed it one sample per AUDIO BLOCK (not per metronome tick: the lags are
in blocks, and a 100 Hz tick would repeat the same block value). Returns
True on the one block a roll start is confirmed; `core/metronome.py`
commits the DROP on it.
"""
from __future__ import annotations

from collections import deque

ROLL_PERIOD_WINDOW_BEATS = 2.0   # autocorrelation window (beats) -- 2 beats = ~86 blocks at 130 BPM
ROLL_PERIOD_ON = 0.5             # score (max of the 8th- and 16th-lag autocorrelations) that reads as a roll ...
ROLL_PERIOD_HOLD_BEATS = 1.0     # ... once held this long (0.46 s at 130 BPM; blips of 0.2-0.3 s are hats)
ROLL_PERIOD_BASE_BARS = 8        # the reference: median score over this many bars, ending one bar ago ...
ROLL_PERIOD_BASE_MAX = 0.30      # ... must be under this: a roll is a step up, a bassline is a texture
ROLL_PERIOD_BASE_MIN_BARS = 4    # no judgement until this much reference exists (a track's first bars are blind)
ROLL_PERIOD_LEVEL_REL = 0.5      # the band's level must be at least this x its median over the reference span
ROLL_PERIOD_COOLDOWN_BARS = 2.0  # after a start: no new start for this long, and the score must fall under ON first
ROLL_PERIOD_MIN_BLOCKS = 16      # fewer samples than this in the window: no score
BEATS_PER_BAR = 4


def _autocorr(x: list[float], lag: int) -> float:
    n = len(x)
    if lag < 1 or n <= lag + 2:
        return 0.0
    mean = sum(x) / n
    var = sum((v - mean) * (v - mean) for v in x)
    if var <= 1e-12:
        return 0.0
    acc = 0.0
    for i in range(n - lag):
        acc += (x[i] - mean) * (x[i + lag] - mean)
    # normalise by the full-window variance scaled to the overlap, like numpy's
    # corrcoef on the two slices would to first order; exact equality is not
    # needed -- the thresholds were tuned on this same estimator (the study).
    return acc / (var * (n - lag) / n)


class RollPeriodDetector:
    """Stateful; call ``on_block(t, level, beat_dur)`` once per audio block.
    ``score`` is this block's periodicity, ``hot`` whether it currently
    clears the threshold with real level, ``started`` True only on the block
    a roll start was confirmed."""

    def __init__(self) -> None:
        self._t: deque[float] = deque()
        self._lev: deque[float] = deque()
        self._hist: deque[tuple[float, float, float]] = deque()   # (t, score, level), reference span
        self.score = 0.0
        self.base: float | None = None   # the reference median score, None until enough history
        self.hot = False
        self.started = False
        self._hot_since: float | None = None
        self._cooldown_until = 0.0
        self._wait_below = False

    def reset(self) -> None:
        self.__init__()

    def on_block(self, t: float, level: float, beat_dur: float) -> bool:
        self.started = False
        if beat_dur <= 0:
            return False
        bar = BEATS_PER_BAR * beat_dur
        window_s = ROLL_PERIOD_WINDOW_BEATS * beat_dur
        self._t.append(t)
        self._lev.append(float(level))
        while self._t and t - self._t[0] > window_s:
            self._t.popleft()
            self._lev.popleft()
        n = len(self._lev)
        if n < ROLL_PERIOD_MIN_BLOCKS:
            self.score = 0.0
            self.hot = False
            return False
        dt = (t - self._t[0]) / (n - 1)
        if dt <= 0:
            return False
        x = list(self._lev)
        lag8 = max(1, round(beat_dur / 2.0 / dt))
        lag16 = max(1, round(beat_dur / 4.0 / dt))
        self.score = max(_autocorr(x, lag8), _autocorr(x, lag16))

        # Reference span: the (BASE_BARS + 1) bars behind this block; the
        # last bar is the gap so a roll can't erode its own reference.
        self._hist.append((t, self.score, float(level)))
        span = (ROLL_PERIOD_BASE_BARS + 1) * bar
        while self._hist and t - self._hist[0][0] > span:
            self._hist.popleft()
        ref_scores = [s for (tt, s, _) in self._hist if tt <= t - bar]
        levels = [lv for (_, _, lv) in self._hist]
        if not ref_scores or (t - self._hist[0][0]) < (ROLL_PERIOD_BASE_MIN_BARS + 1) * bar:
            self.base = None
        else:
            srt = sorted(ref_scores)
            self.base = srt[len(srt) // 2]
        lev_med = sorted(levels)[len(levels) // 2] if levels else 0.0
        # The WINDOW's mean level, not this block's: a roll's band dips between
        # hits (Somebody To Love: 9.8 -> 1.0 within a beat) and a per-block
        # floor kept breaking the hold mid-roll (first roll confirmed at +6
        # beats instead of +1.8 when tried).
        win_mean = sum(x) / n
        level_ok = win_mean >= ROLL_PERIOD_LEVEL_REL * lev_med and win_mean > 0.0

        if self.score < ROLL_PERIOD_ON:
            self._wait_below = False
        self.hot = self.score >= ROLL_PERIOD_ON and level_ok
        if not self.hot:
            self._hot_since = None
            return False
        if self._hot_since is None:
            self._hot_since = t
        if (self.base is None or self.base >= ROLL_PERIOD_BASE_MAX
                or self._wait_below or t < self._cooldown_until):
            return False
        if t - self._hot_since >= ROLL_PERIOD_HOLD_BEATS * beat_dur:
            self.started = True
            self._wait_below = True
            self._cooldown_until = t + ROLL_PERIOD_COOLDOWN_BARS * bar
            self._hot_since = None
            return True
        return False
