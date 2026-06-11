#!/usr/bin/env python3
"""
Восстановить YOLO labels из геометрии build_zones (по размеру PNG страницы).

  python scripts/regenerate_yolo_labels_geometry.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.yolo_labels import format_yolo_box, refine_yolo_boxes
from belener.zones import build_zones

# Порядок важен: не брать legend (поле схемы) и мелкий spec_right сверху
_EXPORT_KEYS: tuple[tuple[str, int], ...] = (
    ("stamp_frame", 1),
    ("legend_table", 2),
    ("spec_left", 0),
    ("tables_block", 0),
    ("spec_right", 0),
)


def _rect_to_yolo(rect: fitz.Rect, page: fitz.Rect) -> tuple[float, float, float, float]:
    w, h = page.width, page.height
    cx = ((rect.x0 + rect.x1) / 2 - page.x0) / w
    cy = ((rect.y0 + rect.y1) / 2 - page.y0) / h
    bw = (rect.x1 - rect.x0) / w
    bh = (rect.y1 - rect.y0) / h
    return (
        max(0.0, min(1.0, cx)),
        max(0.0, min(1.0, cy)),
        max(0.01, min(1.0, bw)),
        max(0.01, min(1.0, bh)),
    )


def labels_for_image(img_path: Path) -> list[str]:
    with Image.open(img_path) as im:
        w, h = im.size
    page = fitz.Rect(0, 0, float(w), float(h))
    zones = build_zones(page)
    pr_h = page.height
    boxes: list[tuple[int, float, float, float, float]] = []
    seen_spec: set[str] = set()
    seen_cls: set[int] = set()
    has_tables_block = False
    for key, cls in _EXPORT_KEYS:
        rect = zones.rects.get(key)
        if rect is None or rect.is_empty:
            continue
        cy = (rect.y0 + rect.y1) / 2
        bh = (rect.y1 - rect.y0) / pr_h
        if key == "spec_right" and (has_tables_block or cy < page.y0 + pr_h * 0.25):
            continue
        if cls == 2 and cy > page.y0 + pr_h * 0.34 and bh > 0.20:
            continue
        if cls in seen_cls and cls != 0:
            continue
        if cls == 0 and key in seen_spec:
            continue
        cx, cy_n, bw, bh_n = _rect_to_yolo(rect, page)
        boxes.append((cls, cx, cy_n, bw, bh_n))
        if cls == 0:
            seen_spec.add(key)
            if key == "tables_block":
                has_tables_block = True
        else:
            seen_cls.add(cls)

    refined = refine_yolo_boxes(boxes)
    return [format_yolo_box(*b) for b in refined]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "training" / "yolo_zones")
    args = ap.parse_args()
    n = 0
    for split in ("train", "val"):
        img_dir = args.data / "images" / split
        lbl_dir = args.data / "labels" / split
        if not img_dir.is_dir():
            continue
        lbl_dir.mkdir(parents=True, exist_ok=True)
        for img_path in sorted(img_dir.glob("*.png")):
            lines = labels_for_image(img_path)
            if not lines:
                continue
            (lbl_dir / f"{img_path.stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            n += 1
            print(f"{split}/{img_path.stem}: {len(lines)} boxes")
    print(f"regenerated {n} label files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
