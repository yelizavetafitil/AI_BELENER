"""Извлечение PDF-чертежей САПР: зонный OCR + vision → структурированный отчёт."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from belener.drawing import analyze_pdf_document

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
        log.info("drawing pipeline %s", filename)
        drawing = analyze_pdf_document(doc, filename, pdf_path=source_path)
        log.info("drawing done %.1fs ok=%s", time.monotonic() - t0, drawing.get("ok"))

        if not drawing.get("ok"):
            return drawing

        return {
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": drawing.get("pipeline") or "belener_hybrid",
            "filename": filename,
            "page_count": doc.page_count,
            "pages": [],
            "total_chars": 0,
            "drawing": drawing,
            "vision_model": drawing.get("vision_model"),
            "warnings": drawing.get("warnings") or [],
        }
    finally:
        doc.close()


def extract_pdf_path(path: str, filename: str | None = None) -> dict[str, Any]:
    p = Path(path)
    path_str = str(p.resolve())
    return extract_pdf_bytes(p.read_bytes(), filename or p.name, source_path=path_str)
