#!/usr/bin/env python3
"""
Строки из golden labels → горизонтальные полосы кропа (PaddleOCR rec fine-tune).

  python scripts/rebuild_train_list.py
  python scripts/export_paddle_line_dataset.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = 250_000_000

ROOT = Path(__file__).resolve().parents[1]
MAX_ZONE_PIXELS = 25_000_000
DATA = Path("/app/data/training") if Path("/app/data/training").is_dir() else ROOT / "data" / "training"

ZONE_SUFFIXES = (
    ("_spec_right", "spec_right"),
    ("_stamp_frame", "stamp_frame"),
    ("_stamp_block", "stamp_frame"),
    ("_spec_left", "spec_left"),
)

_RE_GARBAGE = re.compile(
    r"(ОВО\s+О\s+ПО|<<\s*<<|см\.\s*лиусм|строутельстбо|БЕЛНИПИЗНЕРГОПРОМ)",
    re.I,
)
_STAMP_MARKERS = re.compile(
    r"разраб|пров\.|н\.контр|лист|руп|стадия|изм\.|гип|утв",
    re.I,
)


def _crop_stems(crops: Path) -> dict[str, Path]:
    return {d.name: d for d in crops.iterdir() if d.is_dir()}


def _resolve_crop_stem(stem: str, crop_by_name: dict[str, Path]) -> str | None:
    if stem in crop_by_name:
        return stem
    for alt in (
        stem.replace("_л4", "_л.4").replace("__л2", "__л.2").replace("_л5", "_л.5"),
    ):
        if alt in crop_by_name:
            return alt
    for crop_name in crop_by_name:
        if crop_name.replace(".", "") == stem.replace(".", ""):
            return crop_name
    return None


def _label_ok(text: str, zone: str) -> bool:
    t = (text or "").strip()
    if len(t) < 4:
        return False
    if _RE_GARBAGE.search(t):
        return False
    letters = re.findall(r"[А-Яа-яЁёA-Za-z0-9]", t)
    if not letters:
        return False
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    if cyr / len(letters) < 0.35:
        return False
    if zone.startswith("stamp") and len(t) > 400:
        if len(_STAMP_MARKERS.findall(t)) < 2:
            return False
    return True


def _lines_from_label(text: str) -> list[str]:
    rows: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        if len(ln) < 2:
            continue
        rows.append(ln)
    return rows


def _strip_image(img: Image.Image, y0: int, y1: int, pad: int = 2) -> Image.Image:
    h = img.height
    y0 = max(0, y0 - pad)
    y1 = min(h, y1 + pad)
    if y1 <= y0:
        y1 = min(h, y0 + 8)
    return img.crop((0, y0, img.width, y1))


def export_lines(training: Path, out_dir: Path, *, min_strip_h: int = 10) -> int:
    crops = training / "crops"
    labels_dir = training / "labels"
    out_dir.mkdir(parents=True, exist_ok=True)
    crop_by_name = _crop_stems(crops)
    lines_out: list[str] = []
    n_strips = 0

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
        if not _label_ok(text, zone):
            print(f"skip bad label: {label_path.name}")
            continue
        label_lines = _lines_from_label(text)
        if not label_lines:
            continue
        try:
            img = Image.open(png).convert("RGB")
        except OSError:
            continue
        if img.width * img.height > MAX_ZONE_PIXELS:
            scale = (MAX_ZONE_PIXELS / (img.width * img.height)) ** 0.5
            nw = max(1, int(img.width * scale))
            nh = max(1, int(img.height * scale))
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        n = len(label_lines)
        h = img.height
        for i, label in enumerate(label_lines):
            y0 = int(i * h / n)
            y1 = int((i + 1) * h / n)
            if y1 - y0 < min_strip_h:
                y1 = min(h, y0 + min_strip_h)
            strip = _strip_image(img, y0, y1)
            if strip.width < 8 or strip.height < 6:
                continue
            dst = out_dir / f"{crop_stem}_{zone}_{i:03d}.png"
            strip.save(dst, optimize=True)
            esc = label.replace("\t", " ").replace("\n", " ")
            try:
                rel = dst.relative_to(training)
            except ValueError:
                rel = dst
            lines_out.append(f"{rel.as_posix()}\t{esc}")
            n_strips += 1

    list_path = training / "paddle_rec" / "line_train_list.txt"
    list_path.write_text("\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8")
    print(f"line strips: {n_strips} -> {list_path}")
    return n_strips


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training", type=Path, default=DATA)
    ap.add_argument("--out", type=Path, default=None, help="каталог PNG полос (default paddle_rec/lines)")
    args = ap.parse_args()
    out = args.out or (args.training / "paddle_rec" / "lines")
    n = export_lines(args.training, out)
    if n < 20:
        print(f"Мало строк ({n}). Проверьте labels/crops.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
