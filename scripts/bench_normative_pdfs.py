#!/usr/bin/env python3
"""Проверка извлечения нормативов из PDF (локально или в Docker)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.normative_extract import extract_normatives_pdf_path


def run_one(path: Path) -> dict:
    t0 = time.monotonic()
    r = extract_normatives_pdf_path(str(path), path.name)
    wall = time.monotonic() - t0
    refs = r.get("normative_refs") or []
    return {
        "file": path.name,
        "ok": r.get("ok"),
        "pages_total": r.get("page_count"),
        "pages_processed": r.get("pages_processed"),
        "tiles_done": r.get("tiles_done"),
        "tiles_expected": r.get("tiles_expected"),
        "budget_exhausted": r.get("budget_exhausted"),
        "elapsed_ocr": round(float(r.get("elapsed_sec") or 0), 1),
        "elapsed_wall": round(wall, 1),
        "refs_count": len(refs),
        "refs": [{"kind": x.get("kind"), "ref": x.get("ref")} for x in refs],
        "source_chars": r.get("source_text_chars"),
    }


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else []
    if not paths:
        print("Usage: python scripts/bench_normative_pdfs.py scan/file1.pdf ...")
        return 2
    results = []
    failed = 0
    for p in paths:
        if not p.is_file():
            alt = ROOT / p
            if alt.is_file():
                p = alt
            else:
                print(f"MISSING {p}")
                failed += 1
                continue
        print(f"=== {p.name} ===", flush=True)
        try:
            out = run_one(p)
            results.append(out)
            full = out["pages_processed"] == out["pages_total"] and not out["budget_exhausted"]
            tiles_ok = out["tiles_done"] == out["tiles_expected"] if out["tiles_expected"] else True
            status = "OK" if full and tiles_ok and out["refs_count"] > 0 else "WARN"
            if not full or not tiles_ok:
                failed += 1
            print(
                f"{status} pages {out['pages_processed']}/{out['pages_total']} "
                f"tiles {out['tiles_done']}/{out['tiles_expected']} "
                f"refs={out['refs_count']} ocr={out['elapsed_ocr']}s wall={out['elapsed_wall']}s "
                f"budget_exhausted={out['budget_exhausted']}",
                flush=True,
            )
            for row in out["refs"]:
                print(f"  {row['kind']} | {row['ref']}")
        except Exception as e:
            print(f"ERROR {p.name}: {e}", flush=True)
            failed += 1
    print("\n--- JSON ---")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
