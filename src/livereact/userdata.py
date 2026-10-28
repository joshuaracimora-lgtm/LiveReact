"""Where a user's files live: the per-user data folder.

An installed app keeps settings, rigs, fixture definitions, manuals, named events,
recordings and logs in the OS's per-user folder (``%LOCALAPPDATA%\\LiveReact`` on Windows,
``~/Library/Application Support/LiveReact`` on macOS). A source checkout keeps today's
working-folder paths so nothing moves under Josh's feet. ``LIVEREACT_HOME`` overrides both
(tests, portable installs). Not wired into the app yet (docs/APP_ROADMAP.md item 3).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "LiveReact"
HOME_ENV = "LIVEREACT_HOME"
_PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Paths:
    root: Path
    settings: Path
    config: Path
    midi_map: Path
    rigs: Path
    fixtures: Path
    manuals: Path
    events: Path
    recordings: Path
    logs: Path


_DIRS = ("rigs", "fixtures", "manuals", "events", "recordings", "logs")


def dev_mode(argv: list[str] | None = None) -> bool:
    """A source checkout (``pyproject.toml`` two levels above the package, i.e. ``src/livereact``)
    or an explicit ``--dev`` on the command line."""
    if "--dev" in (sys.argv if argv is None else argv):
        return True
    return (_PACKAGE_DIR.parent.parent / "pyproject.toml").is_file()


def paths(dev: bool | None = None, create: bool = True) -> Paths:
    """The data folder layout, creating its directories (never its files) on first use.
    ``create=False`` for read-only lookups, so merely opening a window makes no folders."""
    env = os.environ.get(HOME_ENV)
    if env:
        root = Path(env)
    elif dev_mode() if dev is None else dev:
        root = Path.cwd()
    else:
        root = Path(user_data_dir(APP_NAME, appauthor=False, roaming=False))
    p = Paths(root=root, settings=root / "settings.json", config=root / "config.json",
              midi_map=root / "midi_map.json", **{d: root / d for d in _DIRS})
    if create:
        for d in _DIRS:
            getattr(p, d).mkdir(parents=True, exist_ok=True)
    return p
