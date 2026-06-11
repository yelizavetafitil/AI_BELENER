"""Извлечение PDF: tile OCR (тот же путь, что и для нормативов)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from belener.tile_ocr import extract_document

log = logging.getLogger("belener.extract")


def extract_pdf_bytes(
    data: bytes,
    filename: str = "document.pdf",
    *,
    source_path: str | None = None,
) -> dict[str, Any]:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count <= 0:
            return {"ok": False, "error": "PDF без страниц", "filename": filename}

        t0 = time.monotonic()
        log.info("tile extract %s", filename)
        result = extract_document(doc, filename)
        log.info("tile extract done %.1fs ok=%s", time.monotonic() - t0, result.get("ok"))

        if not result.get("ok"):
            return result

        return {
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": result.get("pipeline") or "tile_ocr",
            "filename": filename,
            "page_count": doc.page_count,
            "pages": result.get("full_text_pages") or [],
            "total_chars": result.get("source_text_chars") or 0,
            "drawing": result.get("drawing"),
            "normative_refs": result.get("normative_refs") or [],
            "warnings": [],
        }
    finally:
        doc.close()


def extract_pdf_path(path: str, filename: str | None = None) -> dict[str, Any]:
    p = Path(path)
    path_str = str(p.resolve())
    return extract_pdf_bytes(p.read_bytes(), filename or p.name, source_path=path_str)
