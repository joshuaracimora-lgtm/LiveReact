"""Roadmap item 38: the API key settings dialog, against a fake keyring (no real keychain)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeKeyring:
    def __init__(self, stored=None, broken=False):
        self.store = {} if stored is None else {("LiveReact", "anthropic_api_key"): stored}
        self.broken = broken

    def get_password(self, s, u):
        if self.broken:
            raise RuntimeError("no backend")
        return self.store.get((s, u))

    def set_password(self, s, u, k):
        if self.broken:
            raise RuntimeError("no backend")
        self.store[(s, u)] = k

    def delete_password(self, s, u):
        del self.store[(s, u)]


def _dialog(kr):
    from livereact.ui.api_key import ApiKeyDialog
    return ApiKeyDialog(keyring_module=kr)


def test_shows_whether_a_key_is_saved_never_the_key(qapp):
    from PySide6 import QtWidgets
    kr = FakeKeyring("sk-ant-SECRET")
    d = _dialog(kr)
    assert d.l_status.text() == "A key is saved." and d.b_remove.isEnabled()
    shown = " ".join(w.text() for w in d.findChildren(QtWidgets.QLabel))
    assert "SECRET" not in shown and d.e_key.text() == ""
    assert d.e_key.echoMode() == QtWidgets.QLineEdit.EchoMode.Password


def test_save_stores_in_the_keychain_and_clears_the_box(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kr = FakeKeyring()
    d = _dialog(kr)
    assert d.l_status.text() == "No key saved." and not d.b_save.isEnabled() and not d.b_remove.isEnabled()
    d.e_key.setText("  sk-ant-NEW  ")
    d.b_save.click()
    assert kr.store == {("LiveReact", "anthropic_api_key"): "sk-ant-NEW"}
    assert d.e_key.text() == "" and d.l_status.text() == "A key is saved."
    assert list(tmp_path.iterdir()) == []                        # nothing written to disk


def test_remove(qapp):
    kr = FakeKeyring("sk-ant-OLD")
    d = _dialog(kr)
    d.b_remove.click()
    assert kr.store == {} and d.l_status.text() == "No key saved."


def test_a_broken_keychain_is_reported_without_the_key(qapp):
    kr = FakeKeyring(broken=True)
    d = _dialog(kr)
    assert d.l_status.text() == "No key saved."
    d.e_key.setText("sk-ant-SECRET")
    d.b_save.click()
    assert "Could not save" in d.l_status.text() and "SECRET" not in d.l_status.text()
