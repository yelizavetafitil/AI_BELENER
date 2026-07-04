#!/usr/bin/env python3
"""Smoke: sequential tile OCR на синтетическом скане."""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.normative_crops import extract_normatives_document_crops
from belener.tile_ocr import TILE_COLS, TILE_ROWS, extract_document_tiles, page_tile_jobs


def _font(size: int = 40):
    for name in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> int:
    w, h = 1200, 850
    by = {k: r for k, r in page_tile_jobs(fitz.Rect(0, 0, w, h))}
    font = _font()
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    labels = [
        ("tile_0_1", "GOST 10704-91"),
        ("tile_0_2", "GOST 10705-80"),
        ("tile_0_3", "GOST 9467-75"),
        ("tile_1_0", "OST 34 10.748-97"),
        ("tile_1_1", "STP 34.17.101"),
        ("tile_1_2", "STP 34.39.201"),
    ]
    for key, text in labels:
        r = by[key]
        draw.text((int(r.x0 + 30), int(r.y0 + 30)), text, fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        pdf_path = Path(tmp.name)
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(pdf_path))
    doc.close()

    doc = fitz.open(str(pdf_path))
    try:
        tiles = extract_document_tiles(doc, "smoke.pdf", ocr_budget_sec=180.0)
        print(
            f"tiles {tiles['tiles_done']}/{tiles['tiles_expected']} "
            f"chars={sum(len(t) for t in tiles['page_texts'])} "
            f"elapsed={tiles['elapsed_sec']:.1f}s"
        )
        if tiles["tiles_done"] != TILE_COLS * TILE_ROWS:
            return 1
        if tiles["budget_exhausted"]:
            return 1
        result = extract_normatives_document_crops(doc, "smoke.pdf")
        print(f"normatives: {len(result['normative_refs'])}")
        for r in result["normative_refs"]:
            print(f"  {r['kind']} {r['ref']}")
        if tiles["elapsed_sec"] > 180:
            return 1
        print("OK")
        return 0
    finally:
        doc.close()
        pdf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
