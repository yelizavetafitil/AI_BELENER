#!/usr/bin/env python3
"""Быстрый прогон: зоны + OCR spec/stamp без полного extract (минуты, не часы)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/app/data") if Path("/app/data").is_dir() else ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.config import stamp_block_dpi, table_dpi  # noqa: E402
from belener.discover import discover_sheet_zones  # noqa: E402
from belener.grounding import filter_table_rows_by_ocr, row_grounded_in_ocr  # noqa: E402
from belener.ocr import ocr_region  # noqa: E402
from belener.parse import parse_specification, parse_stamp  # noqa: E402
from belener.stamp_read import read_stamp_frame  # noqa: E402
from belener.zones import build_zones  # noqa: E402
from belener.zone_refine import refine_sheet_zones  # noqa: E402


def _quick_pdf(path: Path) -> dict:
    doc = fitz.open(path)
    try:
        page = doc[0]
        zones = discover_sheet_zones(doc, 0, page.rect, fast=True)
        zones = refine_sheet_zones(doc, zones, 0, classify_with_ocr=False)
        spec_rect = zones.rects.get("spec_right") or zones.rects.get("tables_block")
        stamp_rect = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block")
        spec_text = ""
        if spec_rect is not None:
            spec_text = ocr_region(doc, 0, spec_rect, dpi=min(table_dpi(), 420), zone="spec_right")
        stamp = {}
        if stamp_rect is not None:
            stamp = read_stamp_frame(doc, stamp_rect, dpi=min(stamp_block_dpi(), 480), grid_rect=stamp_rect)
        rows = parse_specification(spec_text or "") if spec_text else []
        blob = spec_text or ""
        grounded = [r for r in rows if row_grounded_in_ocr(r, blob)]
        ungrounded = [r for r in rows if r not in grounded]
        return {
            "file": path.name,
            "spec_ocr_chars": len(spec_text or ""),
            "spec_rows": len(rows),
            "grounded_rows": len(grounded),
            "ungrounded_count": len(ungrounded),
            "faithful_ok": len(ungrounded) == 0 or len(rows) == 0,
            "stamp_kv": len(stamp.get("kv") or []),
            "spec_preview": (spec_text or "")[:400],
            "ungrounded_sample": ungrounded[:3],
        }
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=Path("/workspace/scan"))
    ap.add_argument("--out", type=Path, default=DATA_ROOT / "benchmark" / "quick_scan.json")
    args = ap.parse_args()

    pdfs = sorted(args.dir.glob("*.pdf"))
    if not pdfs:
        print("Нет PDF в", args.dir, file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for i, p in enumerate(pdfs, 1):
        t0 = time.monotonic()
        print(f"[{i}/{len(pdfs)}] {p.name} …", flush=True)
        try:
            row = _quick_pdf(p)
            row["seconds"] = round(time.monotonic() - t0, 1)
        except Exception as exc:
            row = {"file": p.name, "error": str(exc), "seconds": round(time.monotonic() - t0, 1)}
        rows.append(row)
        flag = "OK" if row.get("faithful_ok") else "FAIL"
        if "error" not in row:
            print(
                f"  [{flag}] rows={row.get('spec_rows')} grounded={row.get('grounded_rows')} "
                f"ungrounded={row.get('ungrounded_count')} ocr={row.get('spec_ocr_chars')} "
                f"({row.get('seconds')}s)"
            )
        else:
            print(f"  [ERROR] {row['error']}")

    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    faithful = sum(1 for r in rows if r.get("faithful_ok"))
    print(f"\nИтого: {faithful}/{len(rows)} faithful_ok -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
