"""Чтение основной надписи (рамки ГОСТ): сетка ячеек → parse."""

from __future__ import annotations

import logging
from typing import Any

import fitz

from belener.config import accuracy_mode, stamp_grid_enabled, stamp_dpi
from belener.ocr import ocr_region, ocr_stamp_frame
from belener.parse import merge_signatures, merge_stamp, normalize_stamp_output, parse_stamp

log = logging.getLogger("belener.stamp_read")


def read_stamp_frame(
    doc: fitz.Document,
    rect: fitz.Rect,
    *,
    dpi: int,
    page_index: int = 0,
    grid_rect: fitz.Rect | None = None,
) -> dict[str, Any]:
    eff_dpi = max(dpi, stamp_dpi()) if accuracy_mode() else dpi
    text = ocr_region(doc, page_index, rect, dpi=min(eff_dpi, 480), zone="stamp_frame", psm=6)
    stamp = parse_stamp(text) if text else parse_stamp("")
    kv_n = len(stamp.get("kv") or [])
    sig_ok = sum(
        1
        for s in stamp.get("signatures") or []
        if str(s.get("name") or "").strip() not in ("", "—")
    )
    has_cipher = any("шифр" in str(x.get("field") or "").casefold() for x in stamp.get("kv") or [])
    if kv_n >= 3 or (has_cipher and kv_n >= 2) or (kv_n >= 2 and sig_ok >= 2):
        return stamp

    grid_clip = grid_rect if grid_rect is not None and not grid_rect.is_empty else rect
    if stamp_grid_enabled():
        try:
            from belener.stamp_grid import ocr_stamp_grid, stamp_grid_available

            if stamp_grid_available():
                text = ocr_stamp_grid(
                    doc, grid_clip, dpi=min(eff_dpi, 600), page_index=page_index
                )
                if text:
                    stamp = parse_stamp(text)
                    if stamp.get("kv"):
                        return stamp
        except Exception:
            log.exception("stamp grid OCR failed")

    if not text or len(text.strip()) < 40:
        import os

        fast_default = "0" if accuracy_mode() else "1"
        fast = (os.environ.get("PDF_STAMP_FAST") or fast_default).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if fast:
            text = ocr_region(doc, page_index, rect, dpi=min(eff_dpi, 420), zone="stamp_frame", psm=6)
        else:
            text = ocr_stamp_frame(doc, page_index, rect, dpi=eff_dpi)

    return parse_stamp(text) if text else parse_stamp("")


def merge_stamp_sources(
    ocr_stamp: dict[str, Any],
    vision_stamp: dict[str, Any] | None,
    *,
    table_ocr_text: str = "",
) -> dict[str, Any]:
    if not vision_stamp:
        return ocr_stamp
    if not ocr_stamp or not (ocr_stamp.get("kv") or ocr_stamp.get("signatures")):
        return vision_stamp
    from belener.config import report_faithful

    if report_faithful():
        return merge_stamp(ocr_stamp, vision_stamp, extra_texts=(table_ocr_text or "",))
    return merge_stamp(vision_stamp, ocr_stamp, extra_texts=(table_ocr_text or "",))


def finalize_stamp(stamp: dict[str, Any]) -> dict[str, Any]:
    return normalize_stamp_output(stamp)
