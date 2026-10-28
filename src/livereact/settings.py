"""A small JSON settings store in the user data folder: recent rigs, the last named event,
whether first-run setup is done. Separate from ``config.json`` (the show's operator knobs).

A missing or corrupt file reads as defaults; keys this version does not know survive a save.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import userdata

MAX_RECENT_RIGS = 10


@dataclass
class Settings:
    path: Path
    recent_rigs: list[str] = field(default_factory=list)
    last_event: str | None = None
    onboarding_done: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        p = Path(path) if path is not None else userdata.paths().settings
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        s = cls(path=p)
        rigs = raw.pop("recent_rigs", [])
        if isinstance(rigs, list):
            s.recent_rigs = [r for r in rigs if isinstance(r, str)][:MAX_RECENT_RIGS]
        ev = raw.pop("last_event", None)
        s.last_event = ev if isinstance(ev, str) else None
        s.onboarding_done = raw.pop("onboarding_done", False) is True
        s.extra = raw
        return s

    def add_recent_rig(self, rig_path: str | Path) -> None:
        """Move (or add) ``rig_path`` to the top of the recent list."""
        r = str(rig_path)
        self.recent_rigs = [r] + [x for x in self.recent_rigs if x != r]
        del self.recent_rigs[MAX_RECENT_RIGS:]

    def save(self) -> None:
        """Write atomically, so a crash mid-save never leaves a half-written file."""
        data = {**self.extra, "recent_rigs": self.recent_rigs, "last_event": self.last_event,
                "onboarding_done": self.onboarding_done}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
