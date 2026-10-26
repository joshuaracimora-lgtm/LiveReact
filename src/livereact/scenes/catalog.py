"""SceneCatalog: scenes grouped by energy bucket with round-robin selection.

Energy → bucket thresholds map the metronome's energy estimate to a family of
looks; within a bucket, scenes are picked round-robin (no Markov chain in v1,
docs/10 B7).
"""

from __future__ import annotations

from ..fixtures.rig import Rig
from .base import EnergyBucket, Scene
from .library import ALL_SCENES, CORE_SCENES


# Named so other modules (e.g. engines/reactive.py's per-bucket wash model,
# LIGHTING_BIBLE.md §5/§9.1) can normalize energy *within* a bucket's own
# range without duplicating these numbers and risking drift.
#
# Bible-integration Phase 2 (2026-09-11): BREAKDOWN_MAX -> BREAK_MAX (rename
# only). HOLD's old 0.55-0.80 range folds into BUILD (Open Question #1's
# default -- see EnergyBucket's own docstring in scenes/base.py), so
# BUILD_MAX now sits where HOLD_MAX used to. PEAK and OUTRO are NOT
# threshold-based at all -- they're reached only via the stateful escalation
# logic in core/metronome.py's `_derive_bucket` (DROP -> PEAK on sustained
# dwell; BREAK -> OUTRO after a recent DROP/PEAK), never by a raw energy
# reading landing in some range. This function (and _BUCKET_ORDER/
# _BUCKET_BOUNDARIES below) only ever walks among BREAK/BUILD/DROP.
BREAK_MAX = 0.30
BUILD_MAX = 0.80

# Bucket-boundary hysteresis (2026-09-07 real --hardware --record session,
# see [[livereact-project]] memory): BUCKET_MIN_DWELL_S alone (metronome.py)
# only guards against a candidate committing too QUICKLY -- it does nothing
# once a bucket has already committed. A real ~4min capture showed 26 bucket
# flips, mostly DROP<->HOLD/BUILD ping-pong every few (as little as ~1.2s)
# seconds -- energy_norm spending a long stretch hovering right at a boundary
# (DROP sat at 45% of a real song, far more than any actual "drop" section),
# re-triggering a fresh dwell-timer race each time it crossed back. Josh's
# own words: the wash bar "turning off and fluttering... doing some
# randomness." Fix: once a bucket is committed, energy must clear the
# boundary by MARGIN in the direction of travel before even becoming a new
# candidate -- not just touch it. `bucket_for_energy` itself (stateless,
# tested directly in several places) is left untouched; this wraps it with
# the committed bucket as the anchor, used only by metronome.py's debounce.
#
# ASYMMETRIC as of Phase 2 (LIGHTING_BIBLE.md §9.8, TIME-12 -- was a flat
# 0.05 both directions, a confirmed FAIL against the Bible's explicit
# "fast/easy entry, slow/hard exit, ±15% band" design): entering a
# higher-energy bucket clears by the smaller UP margin (keeps the original,
# already-tuned-against-real-data flapping fix for the common case); falling
# back to a lower-energy bucket must clear by the larger DOWN margin, so a
# momentary dip doesn't downgrade a real drop/build. Bible's own number is
# [CONVENTION], not measured -- both margins are tunable by ear against real
# captures, same as everything else here.
HYSTERESIS_MARGIN_UP = 0.05
HYSTERESIS_MARGIN_DOWN = 0.15

_BUCKET_ORDER = (EnergyBucket.BREAK, EnergyBucket.BUILD, EnergyBucket.DROP)
_BUCKET_BOUNDARIES = (BREAK_MAX, BUILD_MAX)  # boundary i sits between order[i]/order[i+1]

# LIGHTING_BIBLE.md §9.8's legal transition matrix (TIME-13): a bucket may
# only move to one of the states listed here; anything else is "ignored,
# state holds" until a legal path opens up. Read by core/metronome.py's
# `_derive_bucket` against its BREAK/BUILD/DROP base (PEAK/OUTRO are
# overlays there and never block the base). The PEAK/OUTRO rows document
# the Bible's full matrix for a future consumer of the overlay states.
#
# DELIBERATE DEVIATION (2026-09-17, D3 in docs/bible_integration/
# SPEC_DEVIATIONS.md): the Bible forbids DROP -> BUILD outright ("a real
# drop doesn't downgrade to still-building; it resolves through BREAK
# first"). On real tracks the section after a drop is often a pad-heavy
# breakdown that never gets quiet enough to read as BREAK (energy_norm
# 0.2-0.5), so the strict matrix held DROP through the whole breakdown AND
# the next build -- no build ramp, no build strobe, and the second drop
# commits with no lead. Josh's call: allow it, but only as a CONFIRMED
# EXIT -- metronome.py gives DROP -> BUILD the same two-bar
# CUT_EXIT_CONFIRM_S it gives DROP -> BREAK, so a between-kick dip or a
# one-bar fill still can't downgrade a drop.
LEGAL_TRANSITIONS: dict[EnergyBucket, set[EnergyBucket]] = {
    EnergyBucket.BREAK: {EnergyBucket.BUILD, EnergyBucket.DROP, EnergyBucket.OUTRO},
    EnergyBucket.BUILD: {EnergyBucket.BREAK, EnergyBucket.DROP},
    EnergyBucket.DROP: {EnergyBucket.BREAK, EnergyBucket.BUILD, EnergyBucket.PEAK, EnergyBucket.OUTRO},
    EnergyBucket.PEAK: {EnergyBucket.BREAK, EnergyBucket.DROP, EnergyBucket.OUTRO},
    EnergyBucket.OUTRO: set(),  # terminal -- no legal exit (a fresh track/transition resets it)
}


def bucket_for_energy(energy: float) -> EnergyBucket:
    """Stateless 3-way threshold check (BREAK/BUILD/DROP only -- PEAK/OUTRO
    are never reachable from a raw level, see the module docstring above)."""
    if energy < BREAK_MAX:
        return EnergyBucket.BREAK
    if energy < BUILD_MAX:
        return EnergyBucket.BUILD
    return EnergyBucket.DROP


def bucket_for_energy_with_hysteresis(
    energy: float,
    committed: EnergyBucket,
    margin_up: float = HYSTERESIS_MARGIN_UP,
    margin_down: float = HYSTERESIS_MARGIN_DOWN,
) -> EnergyBucket:
    """Like `bucket_for_energy`, but anchored on the currently-committed
    bucket: a boundary only counts as crossed once `energy` clears it by the
    direction-appropriate margin, so hovering near a boundary (found live,
    see above) can't even start a new debounce candidate. Walks one boundary
    at a time from `committed` toward wherever `energy` actually is, so a big
    jump (e.g. a real drop) still resolves to the right bucket in one call.

    `committed` may be PEAK or OUTRO (an escalation this module doesn't know
    about, applied by core/metronome.py on top of this 3-way walk) -- treated
    as equivalent to DROP/BREAK respectively for the purposes of the walk
    itself, since PEAK's underlying energy range is DROP's and OUTRO's is
    BREAK's; the caller is responsible for re-applying the escalation/legal-
    transition logic to whatever this returns.
    """
    anchor = {EnergyBucket.PEAK: EnergyBucket.DROP, EnergyBucket.OUTRO: EnergyBucket.BREAK}.get(committed, committed)
    idx = _BUCKET_ORDER.index(anchor)
    while idx < len(_BUCKET_BOUNDARIES) and energy >= _BUCKET_BOUNDARIES[idx] + margin_up:
        idx += 1
    while idx > 0 and energy < _BUCKET_BOUNDARIES[idx - 1] - margin_down:
        idx -= 1
    return _BUCKET_ORDER[idx]


class SceneCatalog:
    def __init__(self, scenes: list[Scene] | None = None, full: bool = False) -> None:
        """Defaults to `library.CORE_SCENES` (one look per bucket, 2026-09-15
        -- see that list's comment); `full=True` rotates the whole
        `ALL_SCENES` catalog instead; an explicit `scenes` list wins over
        both."""
        if scenes is None:
            scenes = [cls() for cls in (ALL_SCENES if full else CORE_SCENES)]
        instances = scenes
        self._by_bucket: dict[EnergyBucket, list[Scene]] = {b: [] for b in EnergyBucket}
        for scene in instances:
            self._by_bucket[scene.bucket].append(scene)
        self._rr_index: dict[EnergyBucket, int] = {b: 0 for b in EnergyBucket}

    def bucket(self, bucket: EnergyBucket) -> list[Scene]:
        return self._by_bucket[bucket]

    def find(self, name: str) -> Scene | None:
        """The catalog's scene with this exact name (the track plan names looks)."""
        for scenes in self._by_bucket.values():
            for s in scenes:
                if s.name == name:
                    return s
        return None

    def names_by_bucket(self) -> dict[str, list[str]]:
        """{bucket value: [scene names]} -- the pool rekordbox/planner.py picks from."""
        return {b.value: [s.name for s in scenes] for b, scenes in self._by_bucket.items()}

    def eligible(self, scene: Scene, rig: Rig | None) -> bool:
        return rig is None or bool(self._capability_filter([scene], rig))

    def default(self) -> Scene:
        """A calm starting scene."""
        for bucket in (EnergyBucket.BREAK, EnergyBucket.BUILD, EnergyBucket.DROP, EnergyBucket.PEAK, EnergyBucket.OUTRO):
            if self._by_bucket[bucket]:
                return self._by_bucket[bucket][0]
        raise ValueError("catalog has no scenes")

    def pick(self, bucket: EnergyBucket, rig: Rig | None = None, exclude: Scene | None = None) -> Scene:
        """Round-robin within ``bucket``; avoid returning ``exclude`` if possible.

        Falls back to any non-excluded scene in any bucket if the requested bucket
        is empty, so selection never fails.

        ``rig`` (Bible-integration Phase 1, optional/default None): when given,
        applies LIGHTING_BIBLE.md §5's `requires{}`/`min_atmosphere_density`
        capability gate before round-robining, walking one `degrade_to` hop on
        failure (LOOK-01; recursive-degrade beyond one hop is NOT specified
        anywhere in the Bible -- see this project's plan doc's Open Question
        #3 for the deliberately conservative default applied here: one hop
        only, then fall back to round-robin among whatever's actually
        eligible, never hard-fail). Omitting ``rig`` (the default) skips this
        gate entirely -- existing callers that don't pass a rig get
        byte-identical behavior to before this parameter existed.

        ``exclude`` (Bible-integration Phase 4, LOOK-06's `conflicts[]`):
        besides the existing "avoid repeating the current scene" behavior,
        any candidate whose name appears in ``exclude``'s `conflicts` (or
        vice versa) is also excluded from selection outright -- Open
        Question #4's approved default, since the Bible declares
        `conflicts[]` pairs but never states an arbitration rule: a
        conflicting look is simply never selected while the conflicting one
        is active; the current look just keeps running until its own next
        natural rotation point (no forced pre-emption). If literally every
        candidate conflicts with ``exclude``, the conflict filter is skipped
        entirely for that call (never hard-fail selection).
        """
        scenes = self._by_bucket[bucket]
        if not scenes:
            scenes = [s for b in EnergyBucket for s in self._by_bucket[b]]
        if rig is not None:
            scenes = self._capability_filter(scenes, rig)
        if exclude is not None:
            non_conflicting = [s for s in scenes if s is exclude or not self._conflicts(s, exclude)]
            if non_conflicting:
                scenes = non_conflicting
        n = len(scenes)
        for _ in range(n):
            idx = self._rr_index[bucket] % n
            self._rr_index[bucket] = idx + 1
            candidate = scenes[idx]
            if candidate is not exclude:
                return candidate
        return scenes[0]  # only one scene available; reuse it

    @staticmethod
    def _eligible(scene: Scene, rig: Rig) -> bool:
        return scene.requires <= rig.capabilities() and rig.atm >= scene.min_atmosphere_density

    @staticmethod
    def _conflicts(a: Scene, b: Scene) -> bool:
        """Bidirectional: the Bible declares `conflicts[]` one-sidedly per
        look (e.g. `drop_blackout_hit` names its conflicts, the build looks
        it conflicts with don't necessarily name it back) -- checked both
        ways so a look doesn't need to be declared on both sides to work."""
        return a.name in b.conflicts or b.name in a.conflicts

    def _capability_filter(self, scenes: list[Scene], rig: Rig) -> list[Scene]:
        """Replace each ineligible scene with its one-hop `degrade_to` target
        (if that target is itself eligible), then drop anything still
        ineligible. Never returns an empty list while ANY scene anywhere in
        the catalog is eligible (falls back to the full flattened catalog,
        same "selection never fails" guarantee `pick()` already makes)."""
        result: list[Scene] = []
        for scene in scenes:
            if self._eligible(scene, rig):
                result.append(scene)
                continue
            if scene.degrade_to is not None:
                degraded = self._instance_of(scene.degrade_to)
                if degraded is not None and self._eligible(degraded, rig):
                    result.append(degraded)
        if result:
            return result
        # Nothing in this bucket is eligible even after degrading -- fall back
        # to any eligible scene anywhere, same spirit as the empty-bucket case
        # above (never fail to select something).
        everything = [s for b in EnergyBucket for s in self._by_bucket[b]]
        eligible_anywhere = [s for s in everything if self._eligible(s, rig)]
        return eligible_anywhere or scenes  # scenes as an absolute last resort (old behavior)

    def _instance_of(self, scene_type: type[Scene]) -> Scene | None:
        """Find the catalog's own instance of a `degrade_to` target type, so
        degrading reuses the same instance (and its round-robin state)
        rather than constructing a bare, un-registered one."""
        for b in EnergyBucket:
            for s in self._by_bucket[b]:
                if type(s) is scene_type:
                    return s
        return None
