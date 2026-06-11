"""OCR по сетке тайлов: единый путь для нормативов и полного текста листа."""

from __future__ import annotations

import logging
import time
from typing import Any

import fitz

log = logging.getLogger("belener.tile_ocr")

TILE_COLS = 4
TILE_ROWS = 2
TILE_OCR_MAX_SIDE = 2400
TILE_OCR_PSM = (6, 3)
PIPELINE = "tile_ocr"


def page_tile_jobs(
    page_rect: fitz.Rect,
    *,
    cols: int = TILE_COLS,
    rows: int = TILE_ROWS,
    overlap_frac: float = 0.12,
) -> list[tuple[str, fitz.Rect]]:
    """Сетка по листу с наложением, чтобы строки не резались по границе."""
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
        log.info("tile %s page=%s %.1fs chars=%s dpi=%s", zone, page_index + 1, time.monotonic() - t0, len(out), dpi)
    return out


def extract_page_tiles(
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
            log.warning("tile OCR: budget stop page=%s", page_index + 1)
            break
        zdead = min(deadline, time.monotonic() + min(tile_max_sec, left - 1))
        text = ocr_tile(doc, page_index, rect, zone=key, dpi=dpi, deadline=zdead)
        if text and text not in sources:
            sources.append(text)

    return sources


def merge_page_text(tile_chunks: list[str]) -> str:
    return "\n\n".join(t for t in tile_chunks if str(t or "").strip())


def extract_document_tiles(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """OCR документа по тайлам в рамках общего бюджета времени."""
    from belener.config import tile_ocr_dpi, tile_ocr_overlap_frac, tile_ocr_time_budget_sec

    t0 = time.monotonic()
    budget = tile_ocr_time_budget_sec()
    deadline = t0 + budget
    dpi = tile_ocr_dpi()
    overlap = tile_ocr_overlap_frac()
    pages = min(doc.page_count, max_pages if max_pages is not None else 3)
    tiles_per_page = TILE_COLS * TILE_ROWS
    tile_max = max(8.0, min(16.0, (budget - 10) / max(1, pages * tiles_per_page)))

    page_tiles: list[list[str]] = []
    all_sources: list[str] = []

    for i in range(pages):
        if deadline - time.monotonic() < 6:
            break
        chunks = extract_page_tiles(
            doc,
            i,
            dpi=dpi,
            deadline=deadline,
            tile_max_sec=tile_max,
            overlap_frac=overlap,
        )
        page_tiles.append(chunks)
        for s in chunks:
            if s and s not in all_sources:
                all_sources.append(s)

    page_texts = [merge_page_text(chunks) for chunks in page_tiles]
    elapsed = time.monotonic() - t0
    log.info(
        "tile OCR done %.1fs pages=%s tiles=%s chars=%s (%s)",
        elapsed,
        len(page_tiles),
        len(all_sources),
        sum(len(t) for t in page_texts),
        filename,
    )
    return {
        "page_tiles": page_tiles,
        "page_texts": page_texts,
        "all_sources": all_sources,
        "elapsed_sec": elapsed,
        "tiles_count": len(all_sources),
        "dpi": dpi,
        "budget_sec": budget,
    }


def extract_document(
    doc: fitz.Document,
    filename: str = "document.pdf",
) -> dict[str, Any]:
    """Полный текст листа + нормативы из одного tile OCR прохода."""
    from belener.normative_refs import merge_normative_refs_from_sources

    tiles = extract_document_tiles(doc, filename)
    full_text_pages = [
        {"index": i + 1, "text": text}
        for i, text in enumerate(tiles["page_texts"])
        if str(text or "").strip()
    ]
    normative_refs = merge_normative_refs_from_sources(*tiles["all_sources"]) if tiles["all_sources"] else []

    drawing = {
        "ok": True,
        "pipeline": PIPELINE,
        "filename": filename,
        "full_text_pages": full_text_pages,
        "normative_refs": normative_refs,
        "tile_sources": tiles["all_sources"],
    }
    return {
        "ok": True,
        "filename": filename,
        "page_count": doc.page_count,
        "pipeline": PIPELINE,
        "normative_refs": normative_refs,
        "full_text_pages": full_text_pages,
        "page_texts": tiles["all_sources"],
        "source_text_chars": sum(len(t) for t in tiles["page_texts"]),
        "elapsed_sec": tiles["elapsed_sec"],
        "drawing": drawing,
    }
