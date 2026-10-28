"""Roadmap item 25: Open Fixture Library search + fetch, with a recorded register and a fake
transport (no network)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from livereact.fixtures import ofl
from livereact.fixtures.ofl import OflService, OflUnavailable, index_from_register, search

DATA = Path(__file__).resolve().parent / "data" / "ofl"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class Recorded:
    """Serves the recorded files for OFL's URLs and counts the calls."""

    def __init__(self, broken: bool = False) -> None:
        self.calls: list[str] = []
        self.broken = broken

    def __call__(self, url: str, timeout: float) -> bytes:
        self.calls.append(url)
        assert timeout > 0
        if self.broken:
            raise OSError("network unreachable")
        if url == ofl.REGISTER_URL:
            return (DATA / "register.json").read_bytes()
        if url == ofl.MANUFACTURERS_URL:
            return (DATA / "manufacturers.json").read_bytes()
        files = {"test-maker/test-par-rgbw": "test-par-rgbw", "test-maker/test-spot-60": "test-spot",
                 "other-co/beam-laser": "test-laser"}
        for key, name in files.items():
            if url == ofl.FIXTURE_URL.format(key=key):
                return (DATA / f"{name}.json").read_bytes()
        raise OSError(f"404 {url}")


def _service(tmp_path, transport=None):
    return OflService(tmp_path / "cache", transport or Recorded())


def test_urls_are_https_constants():
    for url in (ofl.REGISTER_URL, ofl.MANUFACTURERS_URL, ofl.FIXTURE_URL):
        assert url.startswith("https://") and "@" not in url


def test_index_from_register_names_and_redirects():
    import json
    reg = json.loads((DATA / "register.json").read_text(encoding="utf-8"))
    man = json.loads((DATA / "manufacturers.json").read_text(encoding="utf-8"))
    entries = index_from_register(reg, man)
    keys = [e.key for e in entries]
    assert "other-co/old-name" not in keys                      # a redirect, not a fixture
    assert "other-co/wash-bar-12" in keys                       # from the manufacturers lists
    by_key = {e.key: e for e in entries}
    assert by_key["other-co/beam-laser"].manufacturer == "Other Co."
    assert by_key["test-maker/test-spot-60"].name == "Test Spot 60"
    assert by_key["other-co/wash-bar-12"].name == "Wash Bar 12"


def test_search_all_words_then_close_spellings(tmp_path):
    entries = _service(tmp_path).index()
    assert [e.key for e in search(entries, "test spot")] == ["test-maker/test-spot-60"]
    assert [e.key for e in search(entries, "other laser")] == ["other-co/beam-laser"]
    assert [e.key for e in search(entries, "spott")] == ["test-maker/test-spot-60"]   # a close spelling
    assert search(entries, "") == [] and search(entries, "zzzz") == []
    assert len(search(entries, "test", limit=2)) == 2


def test_register_is_cached_in_the_user_folder(tmp_path):
    t = Recorded()
    s = _service(tmp_path, t)
    first = s.index()
    n = len(t.calls)
    assert (tmp_path / "cache" / "register.json").is_file()
    assert s.index() == first and len(t.calls) == n            # served from the cache
    s.index(refresh=True)
    assert len(t.calls) == n + 2


def test_offline_with_no_cache_is_unavailable(tmp_path):
    with pytest.raises(OflUnavailable, match="unavailable"):
        _service(tmp_path, Recorded(broken=True)).index()


def test_offline_refresh_falls_back_to_the_cache(tmp_path):
    _service(tmp_path).index()
    assert len(_service(tmp_path, Recorded(broken=True)).index(refresh=True)) == 4


def test_fetch_caches_and_imports(tmp_path):
    t = Recorded()
    s = _service(tmp_path, t)
    entry = next(e for e in s.index() if e.key == "test-maker/test-spot-60")
    d = s.import_fixture(entry, license="CC-BY-SA-4.0")
    assert d.source.kind == "ofl" and d.source.ofl_key == entry.key and d.manufacturer == "Test Maker"
    assert not d.source.confirmed and d.source.license == "CC-BY-SA-4.0"
    n = len(t.calls)
    s.fetch(entry.key)
    assert len(t.calls) == n


def test_fetch_refuses_odd_keys_and_reports_missing(tmp_path):
    s = _service(tmp_path)
    for bad in ("../x/y", "a/b/c", "A/B", ""):
        with pytest.raises(ValueError):
            s.fetch(bad)
    with pytest.raises(OflUnavailable):
        s.fetch("other-co/wash-bar-12")                         # 404 in the recording


def test_a_garbage_reply_is_unavailable(tmp_path):
    s = _service(tmp_path, lambda url, timeout: b"<html>rate limited</html>")
    with pytest.raises(OflUnavailable):
        s.index()


# --- the wizard page ---

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_search_page_imports_a_fixture(qapp, tmp_path):
    from livereact.ui.ofl_worker import OflSearchPage
    page = OflSearchPage(lambda: _service(tmp_path), threaded=False)
    got = []
    page.imported.connect(got.append)
    page.load()
    assert "4 fixtures" in page.l_status.text()
    page.e_query.setText("test par")
    assert page.results.count() == 1 and page.b_import.isEnabled()
    page.b_import.click()
    assert got and got[0].model == "Test Par RGBW"


def test_search_page_threaded_worker(qapp, tmp_path):
    from PySide6 import QtCore
    from livereact.ui.ofl_worker import OflSearchPage
    page = OflSearchPage(lambda: _service(tmp_path), threaded=True)
    page.load()
    page.wait()
    for _ in range(50):
        QtCore.QCoreApplication.processEvents()
        if page.entries:
            break
    assert len(page.entries) == 4


def test_search_page_offline_says_unavailable(qapp, tmp_path):
    from livereact.ui.ofl_worker import UNAVAILABLE, OflSearchPage
    page = OflSearchPage(lambda: _service(tmp_path, Recorded(broken=True)), threaded=False)
    msgs = []
    page.unavailable.connect(msgs.append)
    page.load()
    assert UNAVAILABLE in page.l_status.text() and msgs
    assert page.results.count() == 0 and not page.b_import.isEnabled()
