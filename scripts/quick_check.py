#!/usr/bin/env python3
"""Quick single-file check (extraction + yellow marks)."""
from __future__ import annotations

import os
import re
import sys

import fitz

from belener.config import normative_ocr_budget_sec
from belener.normative_crops import extract_normatives_document_crops
from belener.normative_extract import generate_pdf_preview_pages_with_highlights


def main() -> int:
    needles = sys.argv[1:] or ["1721-1"]
    scan = "/app/scan"
    matches = []
    for n in sorted(os.listdir(scan)):
        if not n.lower().endswith(".pdf"):
            continue
        nl = n.lower()
        if not all(part.lower() in nl for part in needles):
            continue
        if any(re.search(p, n, re.I) for p in (r"л\.?\s*4\b", r"\bl\.?\s*4\b", r"\.4\b")) and "1" in needles and "4" not in needles:
            continue
        if re.search(r"л\.?\s*1\b", n, re.I) or (len(needles) == 1):
            matches.append(os.path.join(scan, n))
    if not matches:
        for n in sorted(os.listdir(scan)):
            if not n.lower().endswith(".pdf"):
                continue
            nl = n.lower()
            if all(part.lower() in nl for part in needles):
                matches.append(os.path.join(scan, n))
    if not matches:
        print("no match for", needles)
        return 1
    plain = [m for m in matches if not os.path.basename(m).startswith("(")]
    if plain:
        matches = plain
    path = matches[0]
    print("ocr_budget", normative_ocr_budget_sec(1))
    doc = fitz.open(path)
    try:
        r = extract_normatives_document_crops(doc, os.path.basename(path))
    finally:
        doc.close()
    refs = r.get("normative_refs") or []
    print(os.path.basename(path))
    print("refs", len(refs), [x.get("ref") for x in refs])
    print("tiles", r.get("tiles_done"), "/", r.get("tiles_expected"), "exhausted", r.get("budget_exhausted"))
    previews = generate_pdf_preview_pages_with_highlights(
        path, refs, page_normative_refs=r.get("page_normative_refs")
    )
    print("marks", sum(int(p.get("marks") or 0) for p in previews))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
