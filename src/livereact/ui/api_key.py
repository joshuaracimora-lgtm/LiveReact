"""Anthropic API key settings (roadmap item 38): store, replace or remove the manual reader's key.

The key goes to the OS keychain only (manual_reader/client.py ``store_api_key``); the dialog
shows whether one is saved, never the key itself, and clears the entry box after saving.
"""

from __future__ import annotations

import os

from PySide6 import QtWidgets

from ..fixtures.manual_reader.client import KEYRING_SERVICE, KEYRING_USER, store_api_key


def _keyring(module=None):
    if module is not None:
        return module
    import keyring  # noqa: PLC0415
    return keyring


class ApiKeyDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None, keyring_module=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Anthropic API key")
        self._kr = keyring_module
        v = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel("The manual reader uses your own Anthropic API key. It is kept in the "
                                "system keychain, never in a file.")
        info.setWordWrap(True)
        v.addWidget(info)
        self.l_status = QtWidgets.QLabel("")
        self.l_status.setWordWrap(True)
        v.addWidget(self.l_status)
        # 2026-11-18: with no usable keychain the dialog said only "No key saved." and Save failed
        # with a class name; the manual reader also reads ANTHROPIC_API_KEY, so say that instead
        self.l_keychain = QtWidgets.QLabel("")
        self.l_keychain.setWordWrap(True)
        self.l_keychain.setStyleSheet("color: #d9a400;")
        v.addWidget(self.l_keychain)
        self.e_key = QtWidgets.QLineEdit()
        self.e_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.e_key.setPlaceholderText("Paste a new key to save or replace it")
        v.addWidget(self.e_key)
        row = QtWidgets.QHBoxLayout()
        self.b_save = QtWidgets.QPushButton("Save key")
        self.b_remove = QtWidgets.QPushButton("Remove key")
        self.b_close = QtWidgets.QPushButton("Close")
        row.addWidget(self.b_save)
        row.addWidget(self.b_remove)
        row.addStretch(1)
        row.addWidget(self.b_close)
        v.addLayout(row)
        self.b_save.clicked.connect(self.save)
        self.b_remove.clicked.connect(self.remove)
        self.b_close.clicked.connect(self.accept)
        self.e_key.textChanged.connect(lambda _t: self.b_save.setEnabled(bool(self.e_key.text().strip())))
        self.b_save.setEnabled(False)
        self.refresh()

    def has_key(self) -> bool:
        return self._read_keychain() is True

    def _read_keychain(self) -> bool | None:
        """True / False = a key is / is not saved; None = the keychain cannot be reached."""
        try:
            return bool(_keyring(self._kr).get_password(KEYRING_SERVICE, KEYRING_USER))
        except Exception:  # noqa: BLE001 - no keychain backend, or keyring not installed
            return None

    def refresh(self) -> None:
        state = self._read_keychain()
        saved = state is True
        self.l_status.setText("A key is saved." if saved else "No key saved.")
        self.b_remove.setEnabled(saved)
        reachable = state is not None
        self.e_key.setEnabled(reachable)
        if reachable:
            self.l_keychain.setText("")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            self.l_keychain.setText("The system keychain cannot be reached on this computer, so no key can be "
                                    "saved here. The manual reader is using the key in the ANTHROPIC_API_KEY "
                                    "environment variable.")
        else:
            self.l_keychain.setText("The system keychain cannot be reached on this computer, so no key can be "
                                    "saved here. Set the ANTHROPIC_API_KEY environment variable to your key "
                                    "and restart LiveReact; the manual reader reads it from there.")
        self.l_keychain.setVisible(not reachable)

    def save(self) -> None:
        key = self.e_key.text().strip()
        if not key:
            return
        try:
            store_api_key(key, keyring_module=_keyring(self._kr))
        except Exception as exc:  # noqa: BLE001 - say why without echoing the key
            self.l_status.setText(f"Could not save the key: {self._why(exc)}")
            return
        self.e_key.clear()
        self.refresh()

    def remove(self) -> None:
        try:
            _keyring(self._kr).delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception as exc:  # noqa: BLE001
            self.l_status.setText(f"Could not remove the key: {self._why(exc)}")
            return
        self.refresh()

    def _why(self, exc: Exception) -> str:
        """Plain words for a keychain failure; never the exception text (it could hold the key)."""
        if self._read_keychain() is None or isinstance(exc, ImportError) or type(exc).__name__ == "NoKeyringError":
            return ("the system keychain cannot be reached on this computer. Set the ANTHROPIC_API_KEY "
                    "environment variable instead.")
        return f"the system keychain refused it ({type(exc).__name__}). Try again, or set ANTHROPIC_API_KEY."
