"""Roadmap item 9: the per-user data folder (userdata.py) and the settings store (settings.py).
Not wired into the app (item 3); these only check the modules."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from livereact import settings as settings_mod
from livereact import userdata
from livereact.settings import Settings

DIRS = ("rigs", "fixtures", "manuals", "events", "recordings", "logs")
FILES = {"settings": "settings.json", "config": "config.json", "midi_map": "midi_map.json"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "lr_home"
    monkeypatch.setenv("LIVEREACT_HOME", str(root))
    return root


def test_livereact_home_layout(home):
    p = userdata.paths()
    assert p.root == home
    for name, fname in FILES.items():
        assert getattr(p, name) == home / fname
    for d in DIRS:
        assert getattr(p, d) == home / d
        assert getattr(p, d).is_dir()          # created on first use


def test_files_are_not_created(home):
    p = userdata.paths()
    assert not p.settings.exists() and not p.config.exists() and not p.midi_map.exists()


def test_livereact_home_wins_over_dev_mode(home):
    assert userdata.paths(dev=True).root == home


def test_platform_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVEREACT_HOME", raising=False)
    seen = {}

    def fake_user_data_dir(appname, appauthor=None, **kw):
        seen.update(appname=appname, appauthor=appauthor, **kw)
        return str(tmp_path / "platform" / appname)

    monkeypatch.setattr(userdata, "user_data_dir", fake_user_data_dir)
    p = userdata.paths(dev=False)
    assert p.root == tmp_path / "platform" / "LiveReact"
    assert seen["appname"] == "LiveReact" and seen["appauthor"] is False
    assert seen.get("roaming", False) is False      # %LOCALAPPDATA% on Windows, not Roaming
    assert p.rigs.is_dir()


def test_real_platformdirs_names_the_app_folder():
    """No folder is created: only the path platformdirs gives for this OS."""
    import platformdirs
    path = Path(platformdirs.user_data_dir("LiveReact", appauthor=False, roaming=False))
    assert path.name == "LiveReact"


def test_dev_mode_uses_the_working_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVEREACT_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    p = userdata.paths(dev=True)
    assert p.root == tmp_path
    assert p.config == tmp_path / "config.json"        # config.DEFAULT_PATH today
    assert p.midi_map == tmp_path / "midi_map.json"    # AppConfig.midi_map_path today
    assert p.recordings == tmp_path / "recordings"     # __main__ --record today
    assert p.rigs == tmp_path / "rigs"


def test_dev_mode_detection(tmp_path, monkeypatch):
    # this test suite runs from a source checkout: pyproject.toml sits beside src/livereact
    assert userdata.dev_mode(argv=[]) is True
    monkeypatch.setattr(userdata, "_PACKAGE_DIR", tmp_path / "site-packages" / "livereact")
    assert userdata.dev_mode(argv=[]) is False
    assert userdata.dev_mode(argv=["livereact", "--dev"]) is True


def test_installed_app_without_override_uses_platform_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVEREACT_HOME", raising=False)
    monkeypatch.setattr(userdata, "_PACKAGE_DIR", tmp_path / "site-packages" / "livereact")
    monkeypatch.setattr(userdata, "user_data_dir", lambda *a, **k: str(tmp_path / "plat"))
    monkeypatch.setattr(userdata.sys, "argv", ["livereact"])
    assert userdata.paths().root == tmp_path / "plat"


# --- settings ---

def test_settings_defaults_when_missing(home):
    s = Settings.load()
    assert s.recent_rigs == [] and s.last_event is None and s.onboarding_done is False
    assert s.path == userdata.paths().settings


def test_settings_round_trip(home):
    s = Settings.load()
    s.add_recent_rig(home / "rigs" / "a.json")
    s.last_event = "Friday club"
    s.onboarding_done = True
    s.save()
    raw = json.loads(userdata.paths().settings.read_text(encoding="utf-8"))
    assert raw["onboarding_done"] is True
    again = Settings.load()
    assert again.recent_rigs == [str(home / "rigs" / "a.json")]
    assert again.last_event == "Friday club" and again.onboarding_done is True


def test_recent_rigs_most_recent_first_no_duplicates_capped(tmp_path):
    s = Settings(path=tmp_path / "s.json")
    for i in range(15):
        s.add_recent_rig(f"r{i}.json")
    s.add_recent_rig("r3.json")
    assert s.recent_rigs[0] == "r3.json"
    assert s.recent_rigs.count("r3.json") == 1
    assert len(s.recent_rigs) == settings_mod.MAX_RECENT_RIGS


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    s = Settings.load(p)
    assert s.recent_rigs == [] and s.onboarding_done is False
    p.write_text(json.dumps({"recent_rigs": "oops", "onboarding_done": "yes"}), encoding="utf-8")
    s = Settings.load(p)
    assert s.recent_rigs == [] and s.onboarding_done is False


def test_unknown_keys_survive_a_save(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"future_key": 3, "onboarding_done": True}), encoding="utf-8")
    s = Settings.load(p)
    s.save()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["future_key"] == 3 and raw["onboarding_done"] is True


def test_save_leaves_no_temp_file(tmp_path):
    p = tmp_path / "sub" / "s.json"
    Settings(path=p).save()
    assert [f.name for f in p.parent.iterdir()] == ["s.json"]
