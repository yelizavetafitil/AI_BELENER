#!/usr/bin/env python3
"""
Экспорт YOLO-датасета зон с полных страниц PDF (псевдо-разметка из discover + refine).

Классы: 0=spec_table, 1=stamp, 2=legend

  python scripts/export_yolo_dataset.py --pdf-dir scan --out data/training/yolo_zones
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from belener.discover import discover_sheet_zones  # noqa: E402
from belener.zone_refine import refine_sheet_zones  # noqa: E402
from belener.yolo_zones import YOLO_CLASS_NAMES  # noqa: E402
from belener.zones import build_zones  # noqa: E402

# zone_key -> class_id
_ZONE_CLASS: dict[str, int] = {
    "spec_right": 0,
    "spec_left": 0,
    "stamp_frame": 1,
    "stamp_block": 1,
    "legend": 2,
    "legend_table": 2,
}


def _safe_stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:100]


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


def export_pdf(path: Path, images_dir: Path, labels_dir: Path, dpi: int) -> list[str]:
    doc = fitz.open(path)
    stem = _safe_stem(path.stem)
    lines: list[str] = []
    try:
        page = doc[0]
        pr = page.rect
        zones = discover_sheet_zones(doc, 0, pr, fast=True) or build_zones(pr)
        zones = refine_sheet_zones(doc, zones, 0, classify_with_ocr=False)
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img_path = images_dir / f"{stem}.png"
        Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(img_path, optimize=True)
        seen_cls: set[int] = set()
        seen_spec: set[str] = set()
        label_lines: list[str] = []
        pr_h = pr.height
        for key, rect in zones.rects.items():
            if key not in _ZONE_CLASS or rect.is_empty:
                continue
            cls = _ZONE_CLASS[key]
            cy = (rect.y0 + rect.y1) / 2
            bh = (rect.y1 - rect.y0) / pr_h
            # legend_table вместо spec в верхней полосе
            if cls == 0 and key in ("spec_right", "spec_left") and cy < pr.y0 + pr_h * 0.30 and bh < 0.28:
                cls = 2
            # не экспортировать legend на поле схемы
            if cls == 2 and cy > pr.y0 + pr_h * 0.34 and bh > 0.22 and cy < pr.y0 + pr_h * 0.72:
                continue
            if cls in seen_cls and cls != 0:
                continue
            if cls == 0 and key in seen_spec:
                continue
            y = _rect_to_yolo(rect, pr)
            label_lines.append(f"{cls} {y[0]:.6f} {y[1]:.6f} {y[2]:.6f} {y[3]:.6f}")
            if cls == 0:
                seen_spec.add(key)
            else:
                seen_cls.add(cls)
        if label_lines:
            from belener.yolo_labels import format_yolo_box, parse_yolo_line, refine_yolo_boxes

            parsed = [p for ln in label_lines if (p := parse_yolo_line(ln))]
            label_lines = [format_yolo_box(*b) for b in refine_yolo_boxes(parsed)]
        if label_lines:
            (labels_dir / f"{stem}.txt").write_text("\n".join(label_lines) + "\n", encoding="utf-8")
            lines.append(str(img_path.name))
    finally:
        doc.close()
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", type=Path, default=ROOT)
    ap.add_argument("--glob", default="*.pdf")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "training" / "yolo_zones")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--val-ratio", type=float, default=0.15)
    args = ap.parse_args()

    pdf_dir = args.pdf_dir if args.pdf_dir.is_absolute() else (ROOT / args.pdf_dir)
    pdfs = sorted(pdf_dir.glob(args.glob))
    if not pdfs:
        print("Нет PDF в", pdf_dir, file=sys.stderr)
        return 1

    ds = args.out
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (ds / sub).mkdir(parents=True, exist_ok=True)

    names = list(pdfs)
    random.seed(42)
    random.shuffle(names)
    n_val = max(1, int(len(names) * args.val_ratio))
    val_set = set(names[:n_val])

    for path in names:
        split = "val" if path in val_set else "train"
        export_pdf(path, ds / "images" / split, ds / "labels" / split, args.dpi)

    yaml_path = ds / "dataset.yaml"
    yaml_path.write_text(
        f"""# Belener zone detector (pseudo-labels from geometry; refine manually in CVAT)
path: {ds.resolve().as_posix()}
train: images/train
val: images/val
names:
"""
        + "\n".join(f"  {i}: {n}" for i, n in enumerate(YOLO_CLASS_NAMES))
        + "\n",
        encoding="utf-8",
    )
    print(f"YOLO dataset: {len(pdfs)} PDF -> {yaml_path}")
    print("  python scripts/train_yolo_zones.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
