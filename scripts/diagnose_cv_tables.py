#!/usr/bin/env python3
"""Проверка OpenCV-выделения таблиц на PDF (локально, без сети)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz

from belener.cv_tables import cv_available, extract_cv_tables


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/diagnose_cv_tables.py <file.pdf>")
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print("File not found:", path)
        return 1
    if not cv_available():
        print("OpenCV not installed (pip install opencv-python-headless numpy)")
        return 1
    doc = fitz.open(path)
    out = extract_cv_tables(doc, 0)
    print("blocks:", len(out.get("blocks") or []))
    for b in out.get("blocks") or []:
        print(" ", b)
    for i, sec in enumerate(out.get("tables") or [], 1):
        rows = sec.get("rows") or []
        print(f"section {i}: {sec.get('table_number')} kind={sec.get('kind')} rows={len(rows)}")
        if rows:
            print("  first row:", rows[0])
    text = (out.get("table_text") or "").strip()
    print("OCR chars:", len(text))
    if text:
        print("--- preview ---")
        print(text[:1200])
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
