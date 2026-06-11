#!/usr/bin/env python3
"""
Экспорт кропов зон из PDF для дообучения / валидации (этап 3).

  python scripts/export_training_crops.py --dir /workspace --out data/training
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/app/data") if Path("/app/data").is_dir() else ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from belener.config import stamp_dpi, table_dpi  # noqa: E402
from belener.discover import discover_sheet_zones  # noqa: E402
from belener.ocr import ocr_region  # noqa: E402
from belener.zones import build_zones  # noqa: E402
from belener.zone_refine import refine_sheet_zones  # noqa: E402

ZONE_KEYS = (
    "spec_right",
    "spec_left",
    "stamp_frame",
    "stamp_block",
    "legend",
    "legend_table",
    "explication",
    "tables_block",
    "sheet_notes",
)


def _safe_stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:120]


def _render_crop(
    doc: fitz.Document, rect: fitz.Rect, dpi: int, zone: str, *, run_ocr: bool
) -> tuple[Image.Image | None, str]:
    if rect is None or rect.is_empty:
        return None, ""
    page = doc[0]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    text = ocr_region(doc, 0, rect, dpi=dpi, zone=zone) if run_ocr else ""
    return img, text


def export_pdf(path: Path, out_root: Path, *, run_ocr: bool) -> list[dict]:
    doc = fitz.open(path)
    stem = _safe_stem(path.stem)
    crop_dir = out_root / "crops" / stem
    crop_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    try:
        page = doc[0]
        if run_ocr:
            zones = discover_sheet_zones(doc, 0, page.rect, fast=True) or build_zones(page.rect)
        else:
            zones = build_zones(page.rect)
        zones = refine_sheet_zones(doc, zones, 0, classify_with_ocr=run_ocr)
        for key in ZONE_KEYS:
            rect = zones.rects.get(key)
            if rect is None:
                continue
            dpi = stamp_dpi() if key.startswith("stamp") else table_dpi()
            img, text = _render_crop(doc, rect, dpi, key, run_ocr=run_ocr)
            rel = f"crops/{stem}/{key}.png"
            out_path = out_root / rel
            if img is not None:
                img.save(out_path, format="PNG", optimize=True)
            rows.append(
                {
                    "file": path.name,
                    "zone": key,
                    "image": rel,
                    "ocr_baseline": (text or "")[:12000],
                    "dpi": dpi,
                }
            )
    finally:
        doc.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT)
    ap.add_argument("--glob", default="*.pdf")
    ap.add_argument("--out", type=Path, default=DATA_ROOT / "training")
    ap.add_argument("--no-ocr", action="store_true", help="Только PNG, без OCR")
    args = ap.parse_args()

    paths = sorted(args.dir.glob(args.glob))
    if not paths:
        print("Нет PDF в", args.dir, file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.jsonl"
    n = 0
    with manifest.open("w", encoding="utf-8") as fh:
        for p in paths:
            try:
                for row in export_pdf(p, args.out, run_ocr=not args.no_ocr):
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    n += 1
                print("ok", p.name)
            except Exception as exc:
                print("skip", p.name, exc, file=sys.stderr)
    print(f"wrote {n} records -> {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
