#!/usr/bin/env python3
"""Debug normative extraction for 1118-0-ГП9 л.1."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from belener.normative_refs import extract_normative_refs, merge_normative_refs_from_sources
from belener.tile_ocr import extract_document_tiles, page_notes_jobs


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app/data/tmp/tmp2nljvh4_.pdf")
    if not path.is_file():
        print("MISSING", path)
        return 2
    doc = fitz.open(str(path))
    print("rect", doc[0].rect, "aspect", doc[0].rect.width / doc[0].rect.height)
    tiles = extract_document_tiles(doc, path.name, max_pages=1)
    print("tiles", tiles["tiles_done"], "/", tiles["tiles_expected"], "chars", sum(len(t) for t in tiles["all_sources"]))
    for i, s in enumerate(tiles["all_sources"]):
        hits = re.findall(r"(?i)(?:стб|stb)\s*[\d\-–— .]{4,20}|2235|2073|\d{4}-\d{4}", s)
        if hits:
            print(f"\n--- source[{i}] len={len(s)} hits={hits[:8]} ---")
            print(s[:1200])
    refs = merge_normative_refs_from_sources(*tiles["all_sources"])
    print("\nREFS:")
    for r in refs:
        print(r)
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
