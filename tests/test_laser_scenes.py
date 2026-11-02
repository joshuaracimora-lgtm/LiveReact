"""engines/laser_scenes.py -- the laser scene library and the channel dictionary behind it (2026-10-29).

Josh: "The goal is for LiveReact to understand the depths of the laser channels, so if I describe a
scene or pattern, it's able to understand and know how to create that pattern."
"""
from __future__ import annotations

import json

import pytest

from livereact.engines import laser_scenes as ls
from livereact.engines.laser_show import DEFAULT_SCENE, LASER_SCENES
from livereact.fixtures.profiles import Laser


def test_sophias_four_verbatim_scenes_are_unchanged_and_first():
    assert list(LASER_SCENES)[:4] == ["Rainbow", "Slow Rainbow", "Rainbow Dots", "Blue Sweep"]
    assert LASER_SCENES["Rainbow"] == (80, 119, 0, 0, 0, 0, 255, 71, 255, 255, 127, 255)
    assert LASER_SCENES["Slow Rainbow"] == (80, 119, 0, 0, 0, 0, 170, 71, 255, 128, 64, 255)
    assert LASER_SCENES["Rainbow Dots"] == (60, 118, 0, 0, 0, 0, 200, 71, 0, 0, 190, 255)
    assert LASER_SCENES["Blue Sweep"] == (60, 118, 0, 0, 0, 0, 240, 71, 0, 0, 190, 51)
    assert DEFAULT_SCENE == "Rainbow" and ls.get_scene("Rainbow").status == "seen"


def test_every_library_scene_passes_the_dictionary_check():
    for s in ls.library():
        errors, _notes = ls.check(s.values)
        assert errors == [], (s.name, errors)
        assert len(s.values) == 12 and 42 <= s.values[0] <= 83
        assert s.color in ls.COLOR_MODES and s.status in ls.STATUSES and s.description


def test_describe_reads_a_scene_back_in_the_manuals_words():
    lines = ls.describe(ls.get_scene("Rainbow").values)
    assert len(lines) == 12
    assert lines[0].startswith("CH1 = 80: mode: manual control") and "[seen]" in lines[0]
    assert "pattern group 4" in lines[1] and "line" in lines[1]
    assert "zoom in and out" in lines[8] and "[manual]" in lines[8]
    assert "colour follows the painting" in lines[11]


def test_check_refuses_what_the_unit_must_never_do():
    errors, _ = ls.check((200,) + (0,) * 11)          # sound-active CH1
    assert any("manual band" in e for e in errors)
    errors, _ = ls.check((60, 118, 0))
    assert errors == ["a scene needs 12 bytes, got 3"]
    errors, _ = ls.check((60, 118, 0, 0, 0, 0, 300, 0, 0, 0, 190, 11))
    assert any("CH7" in e for e in errors)
    _, notes = ls.check(LASER_SCENES["Rainbow"])
    assert any("CH9" in n and "animates" in n for n in notes)   # the manual's CH9 speed bands are flagged


def test_compose_turns_words_into_bytes_through_the_dictionary():
    v = ls.compose()                                     # the defaults: the dotted line sweeping, palette colour
    assert v[:3] == (Laser.MANUAL_PATTERN_MODE, Laser.PATTERN_A1_DOTTED_LINE, 0)
    assert v[10] == Laser.SCAN_DOTS and v[11] == Laser.COLOR_WHITE and 128 <= v[6] <= 255
    v = ls.compose(rotation=("cw", 0.5), x=("pos", 0.5), size=("pulse", 1.0), outline=("solid", 0.3), color="blue")
    assert 128 <= v[3] <= 191 and 0 <= v[6] <= 127 and v[8] == 255 and 0 <= v[10] <= 127 and v[11] == Laser.COLOR_BLUE
    v = ls.compose(shape=(2, 5), painting=("style2", 0.0), color=("rainbow", 0.5), flip_y=("spin", 1.0))
    assert 32 <= v[1] <= 63 and 64 <= v[2] <= 79 and v[9] == 128 and 128 <= v[11] <= 191 and v[5] == 255
    for s in ls.library():                               # everything composed is accepted by the same check
        assert ls.check(s.values)[0] == []


@pytest.mark.parametrize("bad", [
    dict(rotation=("sideways", 0.5)),
    dict(size=("huge", 0.5)),
    dict(outline="fuzzy"),
    dict(color="crimson"),
    dict(shape=(9, 1)),
    dict(painting=("style3", 0.5)),
])
def test_compose_refuses_words_it_does_not_know(bad):
    with pytest.raises(ls.LaserSceneError):
        ls.compose(**bad)


def test_the_file_is_validated_on_load(tmp_path):
    good = json.loads(ls.LIBRARY_PATH.read_text())
    bad = dict(good, scenes=good["scenes"] + [dict(good["scenes"][0], name="Rainbow")])   # duplicate
    p = tmp_path / "x.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ls.LaserSceneError):
        ls.load_library(p)
    bad = dict(good, scenes=[dict(good["scenes"][0], values=[200] + [0] * 11)])             # sound-active CH1
    p.write_text(json.dumps(bad))
    with pytest.raises(ls.LaserSceneError):
        ls.load_library(p)
    with pytest.raises(ls.LaserSceneError):
        ls.get_scene("No Such Scene")
    assert "Rainbow" in ls.catalogue() and "CH12" in ls.catalogue()
