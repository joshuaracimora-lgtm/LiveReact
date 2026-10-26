"""Move the PRIVATE, git-ignored files between the Mac and the desktop in one zip.

The repo is public; Josh's song data stays out of it: ear timelines, look notes, the
review file, the drive-mode baseline and the nights' reports. They travel by copy:

    python tools/private_bundle.py --pack  LiveReact-private.zip      # on the machine that has the latest
    python tools/private_bundle.py --unpack LiveReact-private.zip     # on the other one (overwrites)
    python tools/private_bundle.py --list                             # what would be packed

Contents (all gitignored):
    tracks/*.timeline.json   tracks/*.look.json   tracks/MARKERS_TO_REVIEW.md
    results/baseline/evaluation.json
    results/agent/<date>/{NIGHT,REPORT,SWEEP}.md and results/search/*/{best_params.json,summary.md}
Audio and the USB export are NOT included (copy those separately; they are large).
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    "tracks/*.timeline.json",
    "tracks/*.look.json",
    "tracks/MARKERS_TO_REVIEW.md",
    "results/baseline/evaluation.json",
    "results/agent/*/NIGHT.md",
    "results/agent/*/REPORT.md",
    "results/agent/*/SWEEP.md",
    "results/search/*/best_params.json",
    "results/search/*/summary.md",
)


def files() -> list[Path]:
    out: list[Path] = []
    for pat in PATTERNS:
        out += sorted(p for p in ROOT.glob(pat) if p.is_file())
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pack", metavar="ZIP")
    g.add_argument("--unpack", metavar="ZIP")
    g.add_argument("--list", action="store_true")
    a = p.parse_args()
    if a.list or a.pack:
        fs = files()
        for f in fs:
            print(f.relative_to(ROOT).as_posix())
        print(f"{len(fs)} files")
        if a.pack:
            with zipfile.ZipFile(a.pack, "w", zipfile.ZIP_DEFLATED) as z:
                for f in fs:
                    z.write(f, f.relative_to(ROOT).as_posix())
            print(f"-> {a.pack}")
        return 0
    with zipfile.ZipFile(a.unpack) as z:
        n = 0
        for name in z.namelist():
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                print(f"skipping suspicious path {name}", file=sys.stderr)
                continue
            dst = ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(z.read(name))
            n += 1
    print(f"{n} files written under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
