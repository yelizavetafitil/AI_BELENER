#!/usr/bin/env python3
"""Quick check: normative extraction + yellow preview marks."""
from __future__ import annotations

import os
import sys

import fitz

from belener.normative_crops import extract_normatives_document_crops
from belener.normative_extract import generate_pdf_preview_pages_with_highlights


def main() -> int:
    scan = "/app/scan"
    targets = sys.argv[1:] or [
        "BNP_1721-1",
        "1118-0-",
        "427-1-",
    ]
    paths: list[str] = []
    for name in sorted(os.listdir(scan)):
        if not name.lower().endswith(".pdf"):
            continue
        if any(t in name for t in targets):
            paths.append(os.path.join(scan, name))
    if not paths:
        print("no matching PDFs in", scan)
        return 1

    for path in paths:
        doc = fitz.open(path)
        try:
            r = extract_normatives_document_crops(doc, os.path.basename(path))
        finally:
            doc.close()
        refs = r.get("normative_refs") or []
        page_refs = r.get("page_normative_refs") or []
        previews = generate_pdf_preview_pages_with_highlights(
            path, refs, page_normative_refs=page_refs
        )
        marks = sum(int(p.get("marks") or 0) for p in previews)
        hrefs = sum(int(p.get("refs") or 0) for p in previews)
        print(
            os.path.basename(path),
            f"refs={len(refs)}",
            f"highlighted={hrefs}",
            f"marks={marks}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
