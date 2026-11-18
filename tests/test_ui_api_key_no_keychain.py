"""2026-11-18: the API-key dialog on a computer with no usable keychain.

Before: the dialog said only "No key saved.", invited a paste, and Save answered
"Could not save the key (NoKeyringError)." -- a class name, and no way forward, although the manual
reader also reads ANTHROPIC_API_KEY. Now the dialog says up front that the keychain cannot be reached and
names the environment variable, the error is in words, and a key already in the environment is said.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class NoKeyringError(RuntimeError):
    """Same name and base as keyring.errors.NoKeyringError."""


class NoBackend:
    def get_password(self, s, u):
        raise NoKeyringError("No recommended backend was available.")

    def set_password(self, s, u, k):
        raise NoKeyringError("No recommended backend was available.")

    def delete_password(self, s, u):
        raise NoKeyringError("No recommended backend was available.")


class Working:
    def __init__(self):
        self.store = {}

    def get_password(self, s, u):
        return self.store.get((s, u))

    def set_password(self, s, u, k):
        self.store[(s, u)] = k

    def delete_password(self, s, u):
        del self.store[(s, u)]


def _dialog(kr, monkeypatch, env_key=None):
    if env_key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", env_key)
    from livereact.ui.api_key import ApiKeyDialog
    return ApiKeyDialog(keyring_module=kr)


def test_no_keychain_is_said_before_the_user_pastes_a_key(qapp, monkeypatch):
    d = _dialog(NoBackend(), monkeypatch)
    note = d.l_keychain.text()
    assert not d.l_keychain.isHidden()
    assert "keychain" in note.lower() and "ANTHROPIC_API_KEY" in note, note
    assert not d.e_key.isEnabled()                   # nothing to paste into: Save could only fail


def test_a_failed_save_is_said_in_words_not_a_class_name(qapp, monkeypatch):
    d = _dialog(NoBackend(), monkeypatch)
    d.e_key.setEnabled(True)                         # even if the user gets a key into the box
    d.e_key.setText("sk-ant-SECRET")
    d.b_save.click()
    text = d.l_status.text()
    assert "Could not save" in text and "SECRET" not in text
    assert "NoKeyringError" not in text and "ANTHROPIC_API_KEY" in text, text


def test_a_key_in_the_environment_is_said(qapp, monkeypatch):
    d = _dialog(NoBackend(), monkeypatch, env_key="sk-ant-ENV")
    note = d.l_keychain.text()
    assert "ANTHROPIC_API_KEY" in note and "ENV" not in note.replace("ANTHROPIC_API_KEY", ""), note
    assert "uses" in note.lower() or "using" in note.lower(), note


def test_a_working_keychain_shows_no_such_note(qapp, monkeypatch):
    d = _dialog(Working(), monkeypatch)
    assert d.l_keychain.isHidden()
    assert d.e_key.isEnabled()
    d.e_key.setText("sk-ant-NEW")
    d.b_save.click()
    assert d.l_status.text() == "A key is saved."
