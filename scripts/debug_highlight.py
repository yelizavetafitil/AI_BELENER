#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

import fitz

from belener.normative_crops import extract_normatives_document_crops
from belener.normative_extract import (
    _find_pinpoint_rects,
    _preview_word_sources,
    generate_pdf_preview_pages_with_highlights,
)


def check(path: str) -> None:
    name = os.path.basename(path)
    doc = fitz.open(path)
    try:
        r = extract_normatives_document_crops(doc, name)
    finally:
        doc.close()
    refs = r.get("normative_refs") or []
    print("===", name)
    print("refs", len(refs), [x.get("ref") for x in refs])
    print("budget_exhausted", r.get("budget_exhausted"), "tiles", r.get("tiles_done"), "/", r.get("tiles_expected"))
    ws = r.get("page_preview_words") or []
    pw = ws[0] if ws else []
    print("preview words cached", len(pw))
    for ref in refs:
        rs = ref.get("ref") or ""
        n = len(_find_pinpoint_rects(pw, rs))
        print("  highlight", rs, n)
    previews = generate_pdf_preview_pages_with_highlights(
        path,
        refs,
        page_normative_refs=r.get("page_normative_refs"),
        page_preview_words=r.get("page_preview_words"),
    )
    marks = sum(int(p.get("marks") or 0) for p in previews)
    print("preview marks", marks)


def main() -> int:
    scan = "/app/scan"
    keys = sys.argv[1:] or ["1118-0", "1721-1"]
    for name in sorted(os.listdir(scan)):
        if not name.lower().endswith(".pdf"):
            continue
        if not any(k in name for k in keys):
            continue
        check(os.path.join(scan, name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
