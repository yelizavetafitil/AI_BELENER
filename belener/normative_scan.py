"""Быстрый OCR центрального поля — только для поиска ГОСТ/СТП/ТУ (не в таблицы)."""

from __future__ import annotations

import logging

import fitz

from belener.zones import SheetZones

log = logging.getLogger("belener.normative_scan")


def normative_scan_rect(page_rect: fitz.Rect, zones: SheetZones) -> fitz.Rect:
    """Поле схемы без штампа и правой колонки таблиц."""
    pr = page_rect
    if pr.is_empty:
        return fitz.Rect()
    x0 = pr.x0 + pr.width * 0.02
    y0 = pr.y0 + pr.height * 0.05
    x1 = pr.x1 - pr.width * 0.20
    y1 = pr.y1 - pr.height * 0.30
    for key in ("spec_right", "spec_left", "tables_block", "right_column"):
        rect = zones.rects.get(key)
        if rect is None or rect.is_empty:
            continue
        if rect.x0 > x0 + pr.width * 0.15 and rect.x0 < x1:
            x1 = min(x1, rect.x0 - 4)
    stamp = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block")
    if stamp is not None and not stamp.is_empty:
        y1 = min(y1, stamp.y0 - 4)
    out = fitz.Rect(x0, y0, x1, y1) & pr
    if out.width < pr.width * 0.25 or out.height < pr.height * 0.20:
        return fitz.Rect()
    return out


def ocr_normative_scan(
    doc: fitz.Document,
    page_index: int,
    page_rect: fitz.Rect,
    zones: SheetZones,
    *,
    force: bool = False,
) -> str:
    from belener.config import normative_scan_dpi, normative_scan_enabled
    from belener.ocr import ocr_region

    if not force and not normative_scan_enabled():
        return ""
    rect = normative_scan_rect(page_rect, zones)
    if rect.is_empty:
        return ""
    dpi = normative_scan_dpi()
    try:
        text = ocr_region(doc, page_index, rect, dpi=dpi, zone="body", psm=6)
    except Exception:
        log.debug("normative_scan OCR failed", exc_info=True)
        return ""
    t = (text or "").strip()
    if t:
        log.info("normative_scan %s chars dpi=%s", len(t), dpi)
    return t
