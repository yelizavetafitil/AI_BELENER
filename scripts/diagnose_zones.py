#!/usr/bin/env python3
"""Диагностика зонного OCR: что читается в каждой зоне листа."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz

from belener.ocr import ocr_region
from belener.zones import build_zones


def diagnose(pdf_path: str) -> None:
    doc = fitz.open(pdf_path)
    page = doc[0]
    zones = build_zones(page.rect)
    print("=== Зоны ===")
    for name, rect in zones.rects.items():
        print(f"  {name}: {rect.width:.0f} x {rect.height:.0f} pt")
    order = ("explication", "legend", "sheet_notes", "right_column", "stamp_frame", "body")
    for name in order:
        rect = zones.rects.get(name)
        if rect is None:
            continue
        text = ocr_region(doc, 0, rect, dpi=300, zone=name)
        print(f"\n=== {name} ({len(text)} симв.) ===")
        print((text or "")[:800])
        if len(text or "") > 800:
            print("…")
    doc.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/diagnose_zones.py path/to/drawing.pdf")
        sys.exit(1)
    diagnose(str(Path(sys.argv[1]).resolve()))
