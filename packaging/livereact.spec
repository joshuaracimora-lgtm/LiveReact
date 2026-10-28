# PyInstaller spec for LiveReact (docs/APP_ROADMAP.md item 32): one folder (--onedir), so Qt's
# plugins and the fixture data sit beside the executable and start-up is fast.
#
#     pyinstaller packaging/livereact.spec --noconfirm
#
# PyQt5 / PyOpenGL are excluded: python-prodj-link imports them for its own GUI, and PyQt5 is GPL,
# so it must never ship. Unused Qt modules are excluded to keep the bundle small.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is set by PyInstaller
SRC = ROOT / "src" / "livereact"
ICONS = SRC / "ui" / "icons"

DATAS = [
    (str(SRC / "fixtures" / "builtin" / "*.json"), "livereact/fixtures/builtin"),
    (str(SRC / "fixtures" / "default_rig.json"), "livereact/fixtures"),
    (str(SRC / "presets" / "presets.json"), "livereact/presets"),
]
if ICONS.is_dir():
    DATAS.append((str(ICONS / "*"), "livereact/ui/icons"))

EXCLUDES = [
    # GPL / unused GUI stacks pulled in by python-prodj-link
    "PyQt5", "PyQt6", "OpenGL", "OpenGL_accelerate", "PyOpenGL",
    "tkinter",
    # Qt modules LiveReact does not use
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DExtras", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
]

HIDDEN = [
    # keyring picks its backend at run time
    "keyring.backends.Windows", "keyring.backends.macOS",
]

ICON_WIN = ICONS / "livereact.ico"
ICON_MAC = ICONS / "livereact.icns"

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=DATAS,
    hiddenimports=HIDDEN,
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveReact",
    console=False,
    icon=str(ICON_WIN if sys.platform == "win32" else ICON_MAC) if ICONS.is_dir() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LiveReact")  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="LiveReact.app",
        icon=str(ICON_MAC) if ICON_MAC.is_file() else None,
        bundle_identifier="org.livereact.app",  # placeholder: Josh picks the real one (item 34)
        info_plist={
            "NSMicrophoneUsageDescription": "LiveReact listens to the DJ mix to drive the lights.",
            "NSLocalNetworkUsageDescription": "LiveReact talks to DJ players (Pro DJ Link) and Art-Net / sACN nodes.",
            "NSHighResolutionCapable": True,
        },
    )
