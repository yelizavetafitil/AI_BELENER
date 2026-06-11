"""Нормативы из PDF: весь лист → сетка тайлов → OCR, бюджет ≤150 с."""

from __future__ import annotations

import logging
import time
from typing import Any

import fitz

from belener.normative_refs import (
    extract_normative_refs,
    merge_normative_refs_from_sources,
)

log = logging.getLogger("belener.normative_crops")

TILE_COLS = 4
TILE_ROWS = 2
TILE_OCR_MAX_SIDE = 2400
TILE_OCR_PSM = (6, 3)


def page_tile_jobs(
    page_rect: fitz.Rect,
    *,
    cols: int = TILE_COLS,
    rows: int = TILE_ROWS,
    overlap_frac: float = 0.12,
) -> list[tuple[str, fitz.Rect]]:
    """Сетка по листу с наложением, чтобы строки таблицы/ТТ не резались по границе."""
    pr = page_rect
    if pr.is_empty:
        return []
    step_w = pr.width / cols
    step_h = pr.height / rows
    pad_x = step_w * overlap_frac
    pad_y = step_h * overlap_frac
    jobs: list[tuple[str, fitz.Rect]] = []
    for row in range(rows):
        for col in range(cols):
            x0 = pr.x0 + col * step_w - (pad_x if col > 0 else 0)
            x1 = pr.x0 + (col + 1) * step_w + (pad_x if col < cols - 1 else 0)
            y0 = pr.y0 + row * step_h - (pad_y if row > 0 else 0)
            y1 = pr.y0 + (row + 1) * step_h + (pad_y if row < rows - 1 else 0)
            rect = fitz.Rect(x0, y0, x1, y1) & pr
            if not rect.is_empty and rect.width > 40 and rect.height > 40:
                jobs.append((f"tile_{row}_{col}", rect))
    return jobs


def _scale_image_for_ocr(img, max_side: int):
    from PIL import Image

    w, h = img.size
    if max(w, h) <= max_side:
        return img
    scale = max_side / max(w, h)
    return img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)


def _pdf_text_in_rect(page: fitz.Page, rect: fitz.Rect) -> str:
    if rect is None or rect.is_empty:
        return ""
    try:
        return (page.get_text("text", clip=rect) or "").strip()
    except Exception:
        return ""


def _normative_ref_count(text: str) -> int:
    return len(extract_normative_refs(text or ""))


def _ocr_tile_tesseract(img, *, dpi: int, deadline: float) -> str:
    from belener.ocr import (
        _merge_ocr_passes,
        _tesseract_cli,
        finalize_ocr_text,
        ocr_lang,
    )

    if time.monotonic() >= deadline:
        return ""
    lang = ocr_lang()
    parts: list[str] = []
    for psm in TILE_OCR_PSM:
        if time.monotonic() >= deadline:
            break
        t = _tesseract_cli(img, lang=lang, psm=psm, dpi=dpi)
        if t:
            parts.append(t)
    if not parts:
        return ""
    return finalize_ocr_text(_merge_ocr_passes(parts))


def ocr_tile(
    doc: fitz.Document,
    page_index: int,
    rect: fitz.Rect,
    *,
    zone: str,
    dpi: int,
    deadline: float,
) -> str:
    from belener.ocr import _render_clip

    if rect is None or rect.is_empty or time.monotonic() >= deadline:
        return ""

    t0 = time.monotonic()
    page = doc[page_index]
    parts: list[str] = []
    layer = _pdf_text_in_rect(page, rect)
    if layer:
        parts.append(layer)

    if time.monotonic() >= deadline:
        return "\n\n".join(parts)

    ocr = ""
    img = _render_clip(doc, page_index, rect, dpi=dpi)
    if img is not None:
        ocr = _ocr_tile_tesseract(
            _scale_image_for_ocr(img, TILE_OCR_MAX_SIDE),
            dpi=min(dpi, 280),
            deadline=deadline,
        )

    if ocr:
        parts.append(ocr)

    out = "\n\n".join(parts)
    if out:
        log.info(
            "normative tile %s %.1fs refs=%s dpi=%s",
            zone,
            time.monotonic() - t0,
            _normative_ref_count(out),
            dpi,
        )
    return out


def extract_normatives_page_tiles(
    doc: fitz.Document,
    page_index: int,
    *,
    dpi: int,
    deadline: float,
    tile_max_sec: float,
    overlap_frac: float,
) -> list[str]:
    page = doc[page_index]
    sources: list[str] = []

    for key, rect in page_tile_jobs(page.rect, overlap_frac=overlap_frac):
        left = deadline - time.monotonic()
        if left < 2:
            log.warning("normative tiles: budget stop page=%s", page_index + 1)
            break
        zdead = min(deadline, time.monotonic() + min(tile_max_sec, left - 1))
        text = ocr_tile(doc, page_index, rect, zone=key, dpi=dpi, deadline=zdead)
        if text and text not in sources:
            sources.append(text)

    return sources


def _finalize_refs(all_sources: list[str]) -> list[dict[str, str]]:
    uniq = [s for s in all_sources if str(s or "").strip()]
    if not uniq:
        return []
    return merge_normative_refs_from_sources(*uniq)


def extract_normatives_document_crops(
    doc: fitz.Document,
    filename: str = "document.pdf",
) -> dict[str, Any]:
    from belener.config import (
        normative_table_dpi,
        normative_tile_overlap_frac,
        normative_time_budget_sec,
        normative_vision_enabled,
    )

    t0 = time.monotonic()
    budget = normative_time_budget_sec()
    deadline = t0 + budget
    dpi = normative_table_dpi()
    overlap = normative_tile_overlap_frac()
    max_pages = min(doc.page_count, 3)
    tiles_per_page = TILE_COLS * TILE_ROWS
    tile_max = max(8.0, min(16.0, (budget - 10) / max(1, max_pages * tiles_per_page)))

    all_sources: list[str] = []
    pipeline = "normative_tiles+ocr"

    for i in range(max_pages):
        if deadline - time.monotonic() < 6:
            break
        for s in extract_normatives_page_tiles(
            doc,
            i,
            dpi=dpi,
            deadline=deadline,
            tile_max_sec=tile_max,
            overlap_frac=overlap,
        ):
            if s and s not in all_sources:
                all_sources.append(s)

    refs = _finalize_refs(all_sources)
    elapsed = time.monotonic() - t0

    if len(refs) < 3 and normative_vision_enabled() and (deadline - time.monotonic()) > 40:
        from belener.normative_vision import extract_normatives_document_vision, vision_available

        if vision_available():
            v = extract_normatives_document_vision(doc, filename)
            if v.get("ok") and v.get("normative_refs"):
                v["pipeline"] = "normative_tiles+vision"
                return v

    log.info(
        "normative tiles done %.1fs refs=%s tiles=%s (%s)",
        elapsed,
        len(refs),
        len(all_sources),
        filename,
    )
    return {
        "ok": True,
        "filename": filename,
        "page_count": doc.page_count,
        "pipeline": pipeline,
        "normative_refs": refs,
        "vision_model": None,
        "source_text_chars": sum(len(s) for s in all_sources),
        "page_texts": all_sources,
        "drawing": None,
    }
