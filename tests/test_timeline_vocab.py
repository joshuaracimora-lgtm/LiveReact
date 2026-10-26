"""Every timeline on this machine uses the marker vocabulary (sim/timeline.py).

The loader only warns on an unknown kind / class / key so a typo can't take down a night's evaluation;
this test is where a typo fails loudly instead. Josh's timelines are private (not in the public repo):
on a machine without them the test is skipped.
"""
import glob
import json
from pathlib import Path

import pytest

from livereact.sim.timeline import DROP_CLASSES, MARKER_KINDS

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted(glob.glob(str(ROOT / "tracks" / "*.timeline.json")))


@pytest.mark.skipif(not FILES, reason="no private timelines on this machine")
def test_every_local_timeline_uses_the_marker_vocabulary():
    bad = []
    for f in FILES:
        for m in json.loads(Path(f).read_text(encoding="utf-8")).get("markers", []):
            name = Path(f).name
            if m.get("kind") not in MARKER_KINDS:
                bad.append(f"{name} @ {m.get('t')}: kind {m.get('kind')!r}")
            if set(m) - {"t", "kind", "note", "end", "class"}:
                bad.append(f"{name} @ {m.get('t')}: keys {sorted(set(m))}")
            if m.get("class") and (m.get("kind") != "drop" or m["class"] not in DROP_CLASSES):
                bad.append(f"{name} @ {m.get('t')}: class {m['class']!r}")
    assert not bad, "\n".join(bad)
