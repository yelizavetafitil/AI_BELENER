#!/usr/bin/env python3
"""Удалить дубликаты labels: stamp_block при stamp_frame, несовпадающие stem с crops/."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("/app/data/training") if Path("/app/data/training").is_dir() else ROOT / "data" / "training"

ZONE_SUFFIXES = (
    "_spec_right",
    "_stamp_frame",
    "_stamp_block",
    "_spec_left",
    "_legend",
    "_explication",
    "_tables_block",
    "_sheet_notes",
    "_legend_table",
)


def _parse_label_name(name: str) -> tuple[str, str] | None:
    stem_zone = Path(name).stem
    for suf in ZONE_SUFFIXES:
        if stem_zone.endswith(suf):
            return stem_zone[: -len(suf)], suf[1:]
    return None


def _crop_stems(crops: Path) -> dict[str, Path]:
    return {d.name: d for d in crops.iterdir() if d.is_dir()}


def _resolve_crop_stem(stem: str, crop_by_name: dict[str, Path]) -> str | None:
    if stem in crop_by_name:
        return stem
    alt = stem.replace("_л4", "_л.4").replace("__л2", "__л.2").replace("_л5", "_л.5")
    if alt in crop_by_name:
        return alt
    alt2 = re.sub(r"(\d)л(\d)", r"\1л.\2", stem)
    if alt2 in crop_by_name:
        return alt2
    for crop_name in crop_by_name:
        if crop_name.replace(".", "") == stem.replace(".", ""):
            return crop_name
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training", type=Path, default=DATA)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    labels_dir = args.training / "labels"
    crops = args.training / "crops"
    if not labels_dir.is_dir():
        print("Нет", labels_dir, file=sys.stderr)
        return 1

    crop_by_name = _crop_stems(crops)
    to_delete: list[Path] = []

    # stamp_block если есть stamp_frame
    for p in labels_dir.glob("*_stamp_block.txt"):
        sf = labels_dir / f"{p.stem.replace('_stamp_block', '_stamp_frame')}.txt"
        if sf.is_file():
            to_delete.append(p)

    # дубликат stem: л4 vs л.4 — оставить тот, что совпадает с crops
    by_crop_zone: dict[tuple[str, str], list[Path]] = {}
    for p in labels_dir.glob("*.txt"):
        parsed = _parse_label_name(p.name)
        if not parsed:
            continue
        stem, zone = parsed
        crop_stem = _resolve_crop_stem(stem, crop_by_name)
        if not crop_stem:
            continue
        by_crop_zone.setdefault((crop_stem, zone), []).append(p)

    for key, paths in by_crop_zone.items():
        if len(paths) <= 1:
            continue
        crop_stem, _ = key
        def score(path: Path) -> int:
            parsed = _parse_label_name(path.name)
            if not parsed:
                return 0
            s, _ = parsed
            return 10 if s == crop_stem else 1

        paths.sort(key=score, reverse=True)
        to_delete.extend(paths[1:])

    # labels без папки crops (после нормализации)
    for p in labels_dir.glob("*.txt"):
        if p in to_delete:
            continue
        parsed = _parse_label_name(p.name)
        if not parsed:
            continue
        stem, zone = parsed
        crop_stem = _resolve_crop_stem(stem, crop_by_name)
        if not crop_stem:
            to_delete.append(p)
            continue
        png = crops / crop_stem / f"{zone}.png"
        if zone == "stamp_block":
            png = crops / crop_stem / "stamp_frame.png"
        if not png.is_file():
            to_delete.append(p)

    unique = []
    seen: set[Path] = set()
    for p in to_delete:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    for p in sorted(unique):
        print("удалить:", p.name)
        if not args.dry_run:
            p.unlink(missing_ok=True)

    print(f"\n{'бы удалено' if args.dry_run else 'удалено'}: {len(unique)} файлов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
