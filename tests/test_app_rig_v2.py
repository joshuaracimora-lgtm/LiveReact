"""Roadmap item 14: rig file v2. A slot may name a fixture definition ("fixture": "<id>@<mode>")
and the rig embeds a "definitions" snapshot, so the file is self-contained; v1 rigs load and save
exactly as before."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from livereact.fixtures.definition import SCHEMA
from livereact.fixtures.generic import GenericFixture, GenericLaser, GenericMovingHead
from livereact.fixtures.profiles import ChannelKind, Par
from livereact.fixtures.rig import RigError, _rig_from_dict, load_rig, rig_to_dict, save_rig

ROOT = Path(__file__).resolve().parents[1]
HASHES = json.loads((ROOT / "tests" / "data" / "rig_v1_save_hashes.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def _sha(b: bytes) -> str:
    return hashlib.sha256(_norm(b)).hexdigest()


# --- v1 unchanged ---

def test_every_v1_rig_saves_byte_for_byte_as_before(tmp_path):
    """Expected outputs were saved by the pre-v2 loader (hashes only: rig files name venues)."""
    files = sorted((ROOT / "rigs").glob("*.json")) + [ROOT / "src/livereact/fixtures/default_rig.json"]
    checked = 0
    for f in files:
        expected = HASHES.get(_sha(f.read_bytes()))
        if expected is None:
            continue          # a rig edited since the hashes were taken
        out = tmp_path / "out.json"
        save_rig(load_rig(str(f)), str(out))
        assert _sha(out.read_bytes()) == expected, f.name
        assert "definitions" not in json.loads(out.read_text(encoding="utf-8"))
        checked += 1
    assert checked >= 1
    assert _sha((ROOT / "src/livereact/fixtures/default_rig.json").read_bytes()) in HASHES


# --- v2 ---

def _par_def(fid="acme_par", model="Par"):
    return {"schema": SCHEMA, "id": fid, "manufacturer": "Acme", "model": model, "category": "par",
            "source": {"kind": "user", "confirmed": True},
            "modes": [{"name": "5ch", "channels": [
                {"offset": 0, "function": "dimmer"}, {"offset": 1, "function": "red"},
                {"offset": 2, "function": "green"}, {"offset": 3, "function": "blue"},
                {"offset": 4, "function": "strobe"}]}]}


def _head_def():
    return {"schema": SCHEMA, "id": "acme_head", "manufacturer": "Acme", "model": "Head",
            "category": "moving_head", "source": {"kind": "ofl", "confirmed": True},
            "physical": {"pan_deg": 540, "tilt_deg": 270},
            "modes": [{"name": "3ch", "channels": [
                {"offset": 0, "function": "pan", "default": 128}, {"offset": 1, "function": "tilt", "default": 128},
                {"offset": 2, "function": "dimmer"}]}]}


def _laser_def():
    return {"schema": SCHEMA, "id": "acme_laser", "manufacturer": "Acme", "model": "Laser",
            "category": "laser", "source": {"kind": "manual_ai", "confirmed": False},
            "modes": [{"name": "2ch", "channels": [{"offset": 0, "function": "mode"},
                                                   {"offset": 1, "function": "laser_pattern"}]}]}


def _rig(slots, definitions=None):
    d = {"name": "test", "slots": slots}
    if definitions is not None:
        d["definitions"] = definitions
    return d


def test_v2_slot_builds_a_generic_fixture_from_embedded_definitions():
    rig = _rig_from_dict(_rig(
        [{"slot_id": "p1", "fixture": "acme_par@5ch", "dmx_address": 10, "label": "Par 1", "side": "right"}],
        {"acme_par": _par_def()}))
    fx = rig.fixture("p1")
    assert type(fx) is GenericFixture
    assert (fx.address, fx.label, fx.role, fx.side, fx.channel_count) == (10, "Par 1", "wash", "right", 5)
    assert rig.kind_at(9) == ChannelKind.DIMMER and rig.kind_at(13) == ChannelKind.STROBE
    assert list(rig.boot_universe()[9:14]) == [0, 0, 0, 0, 0]


def test_v2_classes_by_definition():
    rig = _rig_from_dict(_rig([
        {"slot_id": "h", "fixture": "acme_head", "dmx_address": 1},
        {"slot_id": "l", "fixture": "acme_laser@2ch", "dmx_address": 20},
    ], {"acme_head": _head_def(), "acme_laser": _laser_def()}))
    assert type(rig.fixture("h")) is GenericMovingHead and rig.fixture("h").role == "spot"
    las = rig.fixture("l")
    assert type(las) is GenericLaser and las.role == "laser" and not las.ready
    assert rig.kind_at(19) == rig.kind_at(20) == ChannelKind.LASER


def test_unconfirmed_laser_from_a_rig_stays_dark_when_armed():
    rig = _rig_from_dict(_rig([{"slot_id": "l", "fixture": "acme_laser", "dmx_address": 1}],
                              {"acme_laser": _laser_def()}))
    uni = bytearray(512)
    uni[0:2] = bytes([200, 200])
    for fx in rig.fixtures:
        fx.apply_safety(uni, True)
    assert uni[0:2] == bytes(2)


def test_v2_slot_resolves_from_user_folder_and_builtin():
    from livereact.fixtures import library
    from livereact.fixtures.definition import load_definition
    library.save_user_definition(load_definition(_par_def(model="From user folder")))
    rig = _rig_from_dict(_rig([
        {"slot_id": "u", "fixture": "acme_par@5ch", "dmx_address": 1},
        {"slot_id": "b", "fixture": "rgb_par@4ch", "dmx_address": 10},
    ]))
    assert rig.fixture("u").definition.model == "From user folder"
    b = rig.fixture("b")
    assert type(b) is GenericFixture and not isinstance(b, Par)
    assert b.channel_kinds() == Par(1, "x", "wash", "left").channel_kinds()


def test_save_embeds_the_snapshot_and_reloads_with_no_user_files(tmp_path, monkeypatch):
    from livereact.fixtures import library
    from livereact.fixtures.definition import load_definition
    library.save_user_definition(load_definition(_par_def()))
    rig = _rig_from_dict(_rig([
        {"slot_id": "native", "fixture_type": "rgb_par", "dmx_address": 1, "label": "N"},
        {"slot_id": "p1", "fixture": "acme_par@5ch", "dmx_address": 10, "label": "P", "notes": "keep me"},
        {"slot_id": "b", "fixture": "rgb_par@4ch", "dmx_address": 20},
    ]))
    out = tmp_path / "rig.json"
    data = save_rig(rig, str(out))
    slots = {s["slot_id"]: s for s in data["slots"]}
    assert slots["native"]["fixture_type"] == "rgb_par" and "fixture" not in slots["native"]
    assert slots["p1"]["fixture"] == "acme_par@5ch" and "fixture_type" not in slots["p1"]
    assert slots["p1"]["notes"] == "keep me"
    # builtins always ship, so only the user's definition is embedded
    assert set(data["definitions"]) == {"acme_par"}
    assert data["definitions"]["acme_par"]["model"] == "Par"
    # a different machine: empty user folder
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "elsewhere"))
    again = load_rig(str(out))
    assert again.fixture("p1").definition.model == "Par"
    assert rig_to_dict(again) == data


def test_v2_errors_are_rig_errors():
    with pytest.raises(RigError, match="unknown fixture"):
        _rig_from_dict(_rig([{"slot_id": "x", "fixture": "nope@1ch", "dmx_address": 1}]))
    with pytest.raises(RigError):
        _rig_from_dict(_rig([{"slot_id": "x", "fixture": "acme_par@9ch", "dmx_address": 1}],
                            {"acme_par": _par_def()}))
    bad = _par_def()
    bad["modes"][0]["channels"][0]["offset"] = 7
    with pytest.raises(RigError):
        _rig_from_dict(_rig([{"slot_id": "x", "fixture": "acme_par", "dmx_address": 1}], {"acme_par": bad}))
    with pytest.raises(RigError, match="both"):
        _rig_from_dict(_rig([{"slot_id": "x", "fixture": "rgb_par", "fixture_type": "rgb_par",
                              "dmx_address": 1}]))


def test_v2_overlap_is_detected():
    with pytest.raises(RigError, match="overlap"):
        _rig_from_dict(_rig([
            {"slot_id": "a", "fixture_type": "rgb_par", "dmx_address": 1},
            {"slot_id": "b", "fixture": "acme_par", "dmx_address": 3},
        ], {"acme_par": _par_def()}))


def test_home_position_round_trips_for_a_generic_head(tmp_path):
    rig = _rig_from_dict(_rig([{"slot_id": "h", "fixture": "acme_head", "dmx_address": 1,
                                "home_pan": 0.4, "home_tilt": 0.6}], {"acme_head": _head_def()}))
    assert (rig.fixture("h").home_pan, rig.fixture("h").home_tilt) == (0.4, 0.6)
    data = rig_to_dict(rig)
    assert data["slots"][0]["home_pan"] == 0.4
