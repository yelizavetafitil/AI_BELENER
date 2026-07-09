"""Сканы PDF: детекция и постраничный OCR."""

from __future__ import annotations

import logging
import re
import time

import fitz

log = logging.getLogger("belener.scanned")

from belener.config import (
    drawing_aspect_min,
    drawing_page_min_pt,
    scan_as_drawing,
    scan_dpi,
)
from belener.ocr import ocr_region, tesseract_available


def _avg_text_per_page(doc: fitz.Document) -> float:
    total = sum(len(doc[i].get_text().strip()) for i in range(doc.page_count))
    return total / max(doc.page_count, 1)


_PDF_WATERMARK = re.compile(
    r"(?i)pdf\s*factory|pdffactory|пробной\s+версией|trial\s+version|created\s+with",
)
_NORM_MARKER = re.compile(
    r"(?i)(?:гост|gost|ост|ост|стп|stp|ткп|tkp|снип|snip|ту|tu|стб|stb|рд|rd)",
)


def page_text_layer_usable(doc: fitz.Document, page_index: int = 0) -> bool:
    """Текстовый слой годится для подсветки — не водяной знак pdfFactory и не пустышка."""
    if page_index < 0 or page_index >= doc.page_count:
        return False
    words = doc[page_index].get_text("words") or []
    if not words:
        return False
    blob = " ".join(str(w[4]) for w in words).strip()
    if _PDF_WATERMARK.search(blob):
        return False
    if len(blob) < 120 and len(words) < 25:
        return False
    tokens = [str(w[4]).casefold() for w in words]
    if any(_NORM_MARKER.search(t) for t in tokens):
        return True
    digit_rich = sum(1 for t in tokens if re.search(r"\d{3,}", t))
    return len(words) >= 40 and digit_rich >= 3


def is_scanned_document(doc: fitz.Document) -> bool:
    if doc.page_count <= 0:
        return True
    return not any(page_text_layer_usable(doc, i) for i in range(doc.page_count))


def is_scanned_pdf(path: str) -> bool:
    doc = fitz.open(path)
    try:
        return is_scanned_document(doc)
    finally:
        doc.close()


def _page_aspect(page_rect: fitz.Rect) -> float:
    return page_rect.width / max(page_rect.height, 1.0)


def is_engineering_scan_document(doc: fitz.Document) -> bool:
    """Скан без текстового слоя, похожий на инженерный лист."""
    if doc.page_count <= 0 or not is_scanned_document(doc):
        return False
    if not scan_as_drawing():
        return False
    r = doc[0].rect
    aspect = _page_aspect(r)
    wide = aspect >= drawing_aspect_min()
    large = max(r.width, r.height) >= drawing_page_min_pt()
    return wide or large


def should_scan_use_drawing_pipeline(path: str) -> bool:
    """Скан инженерного листа → зонный OCR/vision."""
    doc = fitz.open(path)
    try:
        return is_engineering_scan_document(doc)
    finally:
        doc.close()


def ocr_pdf_pages(path: str, *, dpi: int | None = None) -> list[str]:
    """Постраничный OCR всего листа (Tesseract)."""
    if not tesseract_available():
        return []
    eff_dpi = dpi if dpi is not None else scan_dpi()
    doc = fitz.open(path)
    try:
        pages: list[str] = []
        t0 = time.monotonic()
        log.info("scan OCR start pages=%s dpi=%s", doc.page_count, eff_dpi)
        for i in range(doc.page_count):
            tp = time.monotonic()
            clip = doc[i].rect
            text = ocr_region(doc, i, clip, dpi=eff_dpi, zone="full_page", psm=3)
            pages.append(text.strip())
            log.info("scan OCR page %s/%s in %.1fs", i + 1, doc.page_count, time.monotonic() - tp)
        log.info("scan OCR done in %.1fs", time.monotonic() - t0)
        return pages
    finally:
        doc.close()
