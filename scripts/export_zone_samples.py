#!/usr/bin/env python3
"""
Экспорт пар «зона OCR → эталон из текстового слоя PDF» для дообучения/валидации.

Использование (в Docker или локально с PYTHONPATH=корень проекта):
  python scripts/export_zone_samples.py --dir /workspace --out samples.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from belener.discover import discover_sheet_zones  # noqa: E402
from belener.zones import build_zones  # noqa: E402


def _text_in_rect(page: fitz.Page, rect: fitz.Rect | None) -> str:
    if rect is None:
        return ""
    return (page.get_text("text", clip=rect) or "").strip()


def _export_pdf(path: Path) -> list[dict]:
    doc = fitz.open(path)
    try:
        page = doc[0]
        zones = discover_sheet_zones(page) or build_zones(page.rect)
        samples: list[dict] = []
        for key, rect in (zones.rects or {}).items():
            if key.startswith("stamp"):
                continue
            gt = _text_in_rect(page, rect).strip()
            if len(gt) < 20:
                continue
            samples.append(
                {
                    "file": path.name,
                    "zone": key,
                    "ground_truth": gt[:8000],
                }
            )
        return samples
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT)
    ap.add_argument("--glob", default="*.pdf")
    ap.add_argument("--out", type=Path, default=ROOT / "zone_samples.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    paths = sorted(args.dir.glob(args.glob))
    if args.limit:
        paths = paths[: args.limit]

    n = 0
    with args.out.open("w", encoding="utf-8") as fh:
        for p in paths:
            try:
                for row in _export_pdf(p):
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    n += 1
            except Exception as exc:
                print(f"skip {p.name}: {exc}", file=sys.stderr)
    print(f"wrote {n} samples -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
