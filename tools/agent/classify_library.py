"""Classify every planned section of every USB track; the Track Classifier's main instrument.

    python tools/agent/classify_library.py [--usb usb_export] [--out results/agent/<date>/tracks]
                                           [--classes section_classes.json] [--limit N]

Writes ``CLASSES.md`` + ``classes.json`` in ``--out``:
- per class: its rule, how many sections across the library it matches, 5 example tracks with mm:ss to
  check by ear, and whether it is approved (and which scene);
- per section kind: how many sections no class matches (the unclassified pool a new class could cover)
  and the feature ranges seen there;
- agreement with Josh's ears on the TUNING tracks only (the holdout stays unseen): each ear-labelled
  drop's class (sim/drop_class.py) against the class of the planned drop section it falls in.

File-only (rekordbox/sections.py): the whole library measures in seconds, cached under
``results/agent/features_cache/``. The report names Josh's tracks, so it stays under ``results/``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def mmss(t: float | None) -> str:
    return "?" if t is None else f"{int(t // 60)}:{t % 60:05.2f}"


def _quantiles(vals: list[float]) -> str:
    if not vals:
        return "-"
    v = sorted(vals)
    q = lambda f: v[min(len(v) - 1, int(f * len(v)))]   # noqa: E731
    return f"{q(0.1):.2f} / {q(0.5):.2f} / {q(0.9):.2f}"


def agreement(usb, book, tdir: Path) -> list[dict]:
    """Ear drops on tuning tracks vs. the class of the planned section each falls in."""
    from livereact.loop.evaluate import ear_timeline_for, is_holdout, labeled_tracks
    from livereact.rekordbox.planner import plan_track
    from livereact.rekordbox.pssi import beat_to_seconds
    from livereact.rekordbox.sections import section_features
    from livereact.sim.drop_class import infer_drop_class
    rows = []
    for p in labeled_tracks(tdir, usb):
        if is_holdout(p):
            continue
        ut = usb.find(p)
        if ut is None:
            continue
        table, grid, wave = usb.load_analysis(ut)
        if table is None:
            continue
        tl = ear_timeline_for(p, usb, tdir)
        plan = plan_track(table, track_id=ut.info.id, bpm=ut.info.bpm, grid=grid, waveform=wave)
        feats = section_features(plan, table, grid=grid, waveform=wave, bpm=ut.info.bpm)
        for m in tl.markers:
            if m.kind != "drop":
                continue
            ear = infer_drop_class(m, tl.bpm, tl.beats_per_bar)
            hit = next((f for f in feats if f.t0 is not None and f.t1 is not None and f.t0 - 0.05 <= m.t < f.t1), None)
            c = book.classify(hit) if hit is not None else None
            rows.append({"track": p.stem, "t": m.t, "ear_class": ear, "planned_kind": hit.kind if hit else "",
                         "planned_class": c.name if c else "", "planned_t0": hit.t0 if hit else None})
    return rows


def build(usb, book, tdir: Path, cache: Path, limit: int | None = None) -> tuple[list[str], dict]:
    from livereact.rekordbox.sections import FEATURE_NAMES, library_features
    lib = library_features(usb, cache, limit=limit)
    by_class: dict[str, list] = {c.name: [] for c in book.classes}
    unmatched: dict[str, list] = {}
    profiles: dict[str, int] = {}
    n_sections = 0
    from livereact.rekordbox.sections import SectionFeatures
    for stem, rec in lib.items():
        profiles[rec["profile"] or "?"] = profiles.get(rec["profile"] or "?", 0) + 1
        for d in rec["sections"]:
            f = SectionFeatures(**d)
            n_sections += 1
            c = book.classify(f)
            (by_class[c.name] if c else unmatched.setdefault(f.kind, [])).append((stem, f))
    agree = agreement(usb, book, tdir)
    L = [f"# Section classes -- {len(lib)} tracks, {n_sections} planned sections", "",
         f"Track frequency profiles (D-23): " + ", ".join(f"{k} {v}" for k, v in sorted(profiles.items())), "",
         "## Classes", ""]
    for c in book.classes:
        hits = by_class[c.name]
        state = f"APPROVED -> {c.scene}" if c.approved else ("proposed scene " + c.scene if c.scene else "no scene yet")
        L.append(f"### {c.name}  ({c.kind}; {len(hits)} sections in {len({s for s, _ in hits})} tracks; {state})")
        L.append(f"- rule: {json.dumps(c.rule) if c.rule is not None else 'not defined yet (matches nothing)'}")
        if c.notes:
            L.append(f"- notes: {c.notes}")
        for stem, f in hits[:5]:
            L.append(f"- e.g. {stem[:60]} @ {mmss(f.t0)} ({f.bars:g} bars, energy {f.energy:.2f}, "
                     f"high {f.high_share:.2f}, low {f.low_share:.2f})")
        L.append("")
    L += ["## Not matched by any class", "", "| kind | sections | " + " | ".join(f"{k} p10/p50/p90" for k in
          ("bars", "energy", "high_share", "low_share", "energy_delta")) + " |",
          "|---|---|" + "---|" * 5]
    for kind, rows in sorted(unmatched.items(), key=lambda kv: -len(kv[1])):
        L.append(f"| {kind} | {len(rows)} | " + " | ".join(_quantiles([f.get(k) for _, f in rows]) for k in
                 ("bars", "energy", "high_share", "low_share", "energy_delta")) + " |")
    L += ["", "## Agreement with Josh's ears (tuning tracks only)", "",
          "| track | ear drop | ear class | planned section | planned class |", "|---|---|---|---|---|"]
    for r in agree:
        L.append(f"| {r['track'][:40]} | {mmss(r['t'])} | {r['ear_class']} | {r['planned_kind'] or '-'} "
                 f"@ {mmss(r['planned_t0'])} | {r['planned_class'] or '-'} |")
    agreed = sum(1 for r in agree if r["planned_class"] and r["planned_class"].endswith(r["ear_class"]))
    L += ["", f"agreement: {agreed}/{len(agree)} ear drops land in a planned section of the same class"]
    data = {"tracks": len(lib), "sections": n_sections, "profiles": profiles,
            "classes": {c.name: len(by_class[c.name]) for c in book.classes},
            "unmatched": {k: len(v) for k, v in unmatched.items()}, "agreement": agree,
            "agreed": agreed, "features": list(FEATURE_NAMES)}
    return L, data


def main(argv: list[str] | None = None) -> int:
    from livereact.loop.evaluate import resolve_usb
    from livereact.rekordbox import section_classes
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--usb", default=None)
    ap.add_argument("--tracks", default=str(ROOT / "tracks"))
    ap.add_argument("--out", default=str(ROOT / "results" / "agent" / dt.date.today().strftime("%Y%m%d") / "tracks"))
    ap.add_argument("--classes", default=None, help="a section_classes.json to try (default: the repo's)")
    ap.add_argument("--cache", default=str(ROOT / "results" / "agent" / "features_cache"))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    usb = resolve_usb(a.usb)
    if usb is None:
        print("no rekordbox export (usb_export/ or $LIVEREACT_USB)")
        return 2
    book = section_classes.load(a.classes) if a.classes else section_classes.load()
    if a.classes and not book.classes:
        print(f"{a.classes}: no valid classes (see the warning above)")
        return 2
    lines, data = build(usb, book, Path(a.tracks), Path(a.cache), a.limit)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "CLASSES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "classes.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines[:3]))
    print(f"agreement: {data['agreed']}/{len(data['agreement'])}; -> {out / 'CLASSES.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
