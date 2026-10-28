"""Roadmap item 32: the packaging files parse and say what they must (nothing is built nightly)."""
from __future__ import annotations

import ast
import plistlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "livereact.spec"
ISS = ROOT / "packaging" / "windows" / "livereact.iss"
PLIST = ROOT / "packaging" / "macos" / "entitlements.plist"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _assigned_list(tree: ast.Module, name: str) -> list:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return node.value.elts
    raise AssertionError(f"{name} not assigned in the spec")


def test_spec_is_python_and_excludes_gpl_qt_and_opengl():
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    excludes = {e.value for e in _assigned_list(tree, "EXCLUDES")}
    assert {"PyQt5", "PyQt6", "OpenGL", "PyOpenGL", "tkinter"} <= excludes
    assert "PySide6.QtWebEngineCore" in excludes
    for keep in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
        assert keep not in excludes


def test_spec_collects_the_fixture_data_and_presets():
    text = SPEC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    datas = ast.unparse(ast.List(elts=_assigned_list(tree, "DATAS")))
    datas = datas.replace('"', "'")
    assert "'builtin' / '*.json'" in datas and "'livereact/fixtures/builtin'" in datas
    assert "'default_rig.json'" in datas and "'presets.json'" in datas
    assert "ICONS" in text and "livereact/ui/icons" in text
    # every data file the spec names exists in the source tree
    src = ROOT / "src" / "livereact"
    assert list((src / "fixtures" / "builtin").glob("*.json"))
    assert (src / "fixtures" / "default_rig.json").is_file() and (src / "presets" / "presets.json").is_file()


def test_spec_is_onedir_from_the_launcher():
    text = SPEC.read_text(encoding="utf-8")
    assert "exclude_binaries=True" in text and "COLLECT(" in text      # one folder, not one file
    assert '"packaging" / "launch.py"' in text
    launcher = (ROOT / "packaging" / "launch.py").read_text(encoding="utf-8")
    assert "from livereact.__main__ import main" in launcher


def test_inno_setup_installs_the_onedir_build_per_user():
    text = ISS.read_text(encoding="utf-8")
    assert re.search(r"^AppId=\{\{[0-9A-F-]{36}\}", text, re.M)
    assert 'Source: "..\\..\\dist\\LiveReact\\*"' in text and "recursesubdirs" in text
    assert "PrivilegesRequired=lowest" in text and "{autopf}\\LiveReact" in text


def test_entitlements_network_microphone_not_sandboxed():
    ent = plistlib.loads(PLIST.read_bytes())
    assert ent["com.apple.security.app-sandbox"] is False
    assert ent["com.apple.security.network.client"] is True
    assert ent["com.apple.security.network.server"] is True
    assert ent["com.apple.security.device.audio-input"] is True


def test_workflow_builds_three_targets_and_skips_signing_without_secrets():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\t" not in text
    for target in ("windows-x64", "macos-arm64", "macos-x86_64"):
        assert f"target: {target}" in text
    assert "pyinstaller packaging/livereact.spec" in text
    assert "packaging\\windows\\livereact.iss" in text
    assert "--entitlements packaging/macos/entitlements.plist" in text
    # every signing / notarizing step is gated on its secret being configured
    steps = re.split(r"\n      - ", text)
    signing = [s for s in steps if re.search(r"^(name: )?(Sign|Codesign|Notarize)", s)]
    assert len(signing) == 4
    for s in signing:
        assert "HAS_WINDOWS_SIGNING == 'true'" in s or "HAS_APPLE_SIGNING == 'true'" in s
    assert "permissions:\n  contents: read" in text


def test_no_credentials_in_the_packaging_files():
    for f in (SPEC, ISS, PLIST, WORKFLOW):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"sk-ant-|BEGIN (RSA |EC )?PRIVATE KEY|://[^/\s]+:[^@\s]+@", text), f
