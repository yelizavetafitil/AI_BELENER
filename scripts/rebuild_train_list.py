#!/usr/bin/env python3
"""Пересобрать manifest.jsonl и paddle_rec/train_list.txt из labels/*.txt (без перезаписи OCR)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/app/data") if Path("/app/data").is_dir() else ROOT / "data"

ZONE_SUFFIXES = (
    ("_spec_right", "spec_right"),
    ("_stamp_frame", "stamp_frame"),
    ("_stamp_block", "stamp_frame"),
    ("_spec_left", "spec_left"),
)


def _crop_stems(crops: Path) -> dict[str, Path]:
    return {d.name: d for d in crops.iterdir() if d.is_dir()}


def _resolve_crop_stem(stem: str, crop_by_name: dict[str, Path]) -> str | None:
    import re

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


def _paddle_escape(label: str) -> str:
    return label.replace("\t", " ").replace("\n", "\\n").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training", type=Path, default=DATA_ROOT / "training")
    args = ap.parse_args()

    crops = args.training / "crops"
    labels_dir = args.training / "labels"
    if not labels_dir.is_dir():
        print("Нет", labels_dir, file=sys.stderr)
        return 1

    manifest_rows: list[dict] = []
    paddle_lines: list[str] = []
    crop_by_name = _crop_stems(crops)
    seen_paddle: set[tuple[str, str]] = set()

    for label_path in sorted(labels_dir.glob("*.txt")):
        stem_zone = label_path.stem
        stem, zone = None, None
        for suf, zone_key in ZONE_SUFFIXES:
            if stem_zone.endswith(suf):
                stem, zone = stem_zone[: -len(suf)], zone_key
                break
        if not stem or not zone:
            continue
        crop_stem = _resolve_crop_stem(stem, crop_by_name)
        if not crop_stem:
            continue
        png = crops / crop_stem / f"{zone}.png"
        if not png.is_file():
            continue
        text = label_path.read_text(encoding="utf-8", errors="replace")
        manifest_rows.append(
            {
                "file": crop_stem,
                "zone": zone,
                "image": f"crops/{crop_stem}/{zone}.png",
                "ocr_baseline": text[:12000],
                "label_file": f"labels/{label_path.name}",
                "chars": len(text),
                "source": "golden_manual",
            }
        )
        if len(text.strip()) >= 3:
            key = (crop_stem, zone)
            if key in seen_paddle:
                continue
            seen_paddle.add(key)
            try:
                rel = png.relative_to(args.training)
            except ValueError:
                rel = png
            paddle_lines.append(f"{rel.as_posix()}\t{_paddle_escape(text)}")

    manifest = args.training / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for row in manifest_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    paddle_dir = args.training / "paddle_rec"
    paddle_dir.mkdir(parents=True, exist_ok=True)
    train_list = paddle_dir / "train_list.txt"
    train_list.write_text("\n".join(paddle_lines) + ("\n" if paddle_lines else ""), encoding="utf-8")

    print(f"manifest: {len(manifest_rows)} записей -> {manifest}")
    print(f"train_list: {len(paddle_lines)} строк -> {train_list}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
