"""Чтение основной надписи (рамки ГОСТ): сетка ячеек → parse."""

from __future__ import annotations

import logging
from typing import Any

import fitz

from belener.config import accuracy_mode, stamp_grid_enabled, stamp_dpi
from belener.ocr import ocr_region, ocr_stamp_frame
from belener.parse import (
    _signature_has_content,
    merge_signatures,
    merge_stamp,
    normalize_stamp_output,
    parse_stamp,
)

log = logging.getLogger("belener.stamp_read")


def _good_sig_count(stamp: dict[str, Any]) -> int:
    return sum(
        1 for s in stamp.get("signatures") or [] if isinstance(s, dict) and _signature_has_content(s)
    )


def read_stamp_frame(
    doc: fitz.Document,
    rect: fitz.Rect,
    *,
    dpi: int,
    page_index: int = 0,
    grid_rect: fitz.Rect | None = None,
) -> dict[str, Any]:
    eff_dpi = max(dpi, stamp_dpi()) if accuracy_mode() else dpi

    from belener.paddle_ocr import paddle_ocr_enabled, paddle_zone_match

    is_paddle = paddle_ocr_enabled() and paddle_zone_match("stamp_frame")

    grid_clip = grid_rect if grid_rect is not None and not grid_rect.is_empty else rect
    if (
        grid_clip is not None
        and not grid_clip.is_empty
        and rect is not None
        and not rect.is_empty
        and grid_clip.height > rect.height * 1.6
    ):
        grid_clip = rect

    parts: list[dict[str, Any]] = []

    if stamp_grid_enabled():
        try:
            from belener.stamp_grid import ocr_stamp_grid, stamp_grid_available

            if stamp_grid_available():
                grid_text = ocr_stamp_grid(
                    doc, grid_clip, dpi=min(eff_dpi, 600), page_index=page_index
                )
                if grid_text:
                    parts.append(parse_stamp(grid_text))
        except Exception:
            log.exception("stamp grid OCR failed")

    text = ocr_region(doc, page_index, rect, dpi=min(eff_dpi, 480), zone="stamp_frame", psm=6)
    if text:
        parts.append(parse_stamp(text))

    if _good_sig_count(_merge_stamp_parts(parts)) < 3:
        hi = ocr_stamp_frame(doc, page_index, rect, dpi=min(eff_dpi, 520))
        if hi and len(hi.strip()) > len((text or "").strip()):
            parts.append(parse_stamp(hi))
        elif not is_paddle:
            block = ocr_region(doc, page_index, rect, dpi=min(eff_dpi, 420), zone="stamp_frame", psm=6)
            if block and len(block.strip()) > len((text or "").strip()):
                parts.append(parse_stamp(block))

    stamp = _merge_stamp_parts(parts)
    if stamp.get("kv") or stamp.get("signatures"):
        return stamp
    return parse_stamp(text) if text else parse_stamp("")


def _merge_stamp_parts(parts: list[dict[str, Any]]) -> dict[str, Any]:
    if not parts:
        return parse_stamp("")
    stamp = dict(parts[0])
    for other in parts[1:]:
        stamp = merge_stamp(stamp, other)
    sigs: list[dict[str, str]] = []
    for part in parts:
        sigs = merge_signatures(sigs, part.get("signatures"))
    if sigs:
        stamp["signatures"] = sigs
    return stamp


def enrich_stamp_from_table_text(
    stamp: dict[str, Any],
    *texts: str,
) -> dict[str, Any]:
    """Подписи и поля рамки часто попадают в OCR зон таблиц."""
    extra = "\n\n".join(t.strip() for t in texts if (t or "").strip())
    if not extra or not stamp:
        return stamp
    parsed = parse_stamp(extra)
    out = dict(stamp)
    if parsed.get("signatures"):
        out["signatures"] = merge_signatures(out.get("signatures"), parsed.get("signatures"))
    if parsed.get("kv") or parsed.get("titles"):
        out = merge_stamp(out, parsed, extra_texts=(extra,))
    return out


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
