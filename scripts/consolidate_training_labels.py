#!/usr/bin/env python3
"""Перенести labels на stem, совпадающий с именем папки в crops/."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "training"

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


def _parse(name: str) -> tuple[str, str] | None:
    stem_zone = Path(name).stem
    for suf in ZONE_SUFFIXES:
        if stem_zone.endswith(suf):
            return stem_zone[: -len(suf)], suf[1:]
    return None


def _resolve(stem: str, crop_by_name: dict[str, Path]) -> str | None:
    if stem in crop_by_name:
        return stem
    for alt in (
        stem.replace("_л4", "_л.4").replace("__л2", "__л.2").replace("_л5", "_л.5"),
        re.sub(r"(\d)л(\d)", r"\1л.\2", stem),
    ):
        if alt in crop_by_name:
            return alt
    for crop_name in crop_by_name:
        if crop_name.replace(".", "") == stem.replace(".", ""):
            return crop_name
    return None


def main() -> int:
    labels_dir = DATA / "labels"
    crops = DATA / "crops"
    crop_by_name = {d.name: d for d in crops.iterdir() if d.is_dir()}
    moved = 0
    for p in list(labels_dir.glob("*.txt")):
        parsed = _parse(p.name)
        if not parsed:
            continue
        stem, zone = parsed
        crop_stem = _resolve(stem, crop_by_name)
        if not crop_stem or crop_stem == stem:
            continue
        dst = labels_dir / f"{crop_stem}_{zone}.txt"
        text = p.read_text(encoding="utf-8", errors="replace")
        if dst.is_file():
            existing = dst.read_text(encoding="utf-8", errors="replace")
            if len(text.strip()) > len(existing.strip()):
                dst.write_text(text, encoding="utf-8")
        else:
            dst.write_text(text, encoding="utf-8")
        p.unlink(missing_ok=True)
        moved += 1
        print(f"{p.name} -> {dst.name}")
    print(f"перенесено/объединено: {moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
