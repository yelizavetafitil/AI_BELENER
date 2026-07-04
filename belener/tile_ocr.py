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


def _tile_row_col(zone: str) -> tuple[int, int]:
    parts = (zone or "").split("_")
    if len(parts) >= 3 and parts[0] == "tile":
        try:
            return int(parts[1]), int(parts[2])
        except ValueError:
            pass
    return 0, 0


def page_tile_jobs_normative(
    page_rect: fitz.Rect,
    *,
    cols: int = TILE_COLS,
    rows: int = TILE_ROWS,
    overlap_frac: float = 0.12,
) -> list[tuple[str, fitz.Rect]]:
    """Сетка с приоритетом нижнего ряда и правых колонок (спецификация, ТТ)."""
    jobs = page_tile_jobs(page_rect, cols=cols, rows=rows, overlap_frac=overlap_frac)
    return sorted(jobs, key=lambda item: _tile_row_col(item[0]), reverse=True)


def page_supplement_jobs(page_rect: fitz.Rect) -> list[tuple[str, fitz.Rect]]:
    """Доп. зона на широких листах: спецификация/ТТ справа снизу (мелкий шрифт)."""
    pr = page_rect
    if pr.is_empty or pr.width / max(pr.height, 1.0) < 1.6:
        return []
    rect = fitz.Rect(
        pr.x0 + pr.width * 0.70,
        pr.y0 + pr.height * 0.55,
        pr.x1,
        pr.y1,
    ) & pr
    if rect.is_empty or rect.width < 80 or rect.height < 80:
        return []
    return [("spec_br", rect)]


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


def _ocr_tile_tesseract(img, *, dpi: int, deadline: float, tile_max_sec: float, zone: str = "") -> str:
    from belener.config import tile_ocr_psm_modes
    from belener.ocr import (
        _merge_ocr_passes,
        _tesseract_cli,
        finalize_ocr_text,
        ocr_lang,
    )

    if time.monotonic() >= deadline:
        return ""
    lang = ocr_lang()
    modes = list(tile_ocr_psm_modes())
    if (zone or "").startswith("spec_"):
        modes = list(dict.fromkeys(modes + [3, 11]))
    parts: list[str] = []
    for psm in modes:
        if time.monotonic() >= deadline:
            break
        left = deadline - time.monotonic()
        if left < 0.5:
            break
        tout = min(left, tile_max_sec, 60.0)
        t = _tesseract_cli(img, lang=lang, psm=psm, dpi=dpi, timeout=tout, zone="tile")
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
    tile_max_sec: float,
) -> str:
    from belener.config import tile_text_skip_ocr_min_chars
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

    if layer and len(layer) >= tile_text_skip_ocr_min_chars():
        return "\n\n".join(parts)

    ocr = ""
    img = _render_clip(doc, page_index, rect, dpi=dpi)
    if img is not None:
        ocr = _ocr_tile_tesseract(
            _scale_image_for_ocr(img, TILE_OCR_MAX_SIDE),
            dpi=min(dpi, 280),
            deadline=deadline,
            tile_max_sec=tile_max_sec,
            zone=zone,
        )

    if ocr:
        parts.append(ocr)

    out = "\n\n".join(parts)
    if out:
        log.info("tile %s page=%s %.1fs chars=%s dpi=%s", zone, page_index + 1, time.monotonic() - t0, len(out), dpi)
    return out


def _ocr_supplement_tiles(
    doc: fitz.Document,
    page_index: int,
    *,
    dpi: int,
    deadline: float,
    tile_max_sec: float,
) -> list[str]:
    from belener.config import normative_supplement_budget_sec

    page = doc[page_index]
    out: list[str] = []
    for key, rect in page_supplement_jobs(page.rect):
        left = deadline - time.monotonic()
        if left < 8.0:
            log.warning("tile OCR: supplement skipped page=%s left=%.1fs", page_index + 1, left)
            break
        budget = min(normative_supplement_budget_sec(), tile_max_sec, max(12.0, left - 1.0))
        zdead = min(deadline, time.monotonic() + budget)
        sup_dpi = min(int(dpi * 1.15), 400)
        text = ocr_tile(
            doc,
            page_index,
            rect,
            zone=key,
            dpi=sup_dpi,
            deadline=zdead,
            tile_max_sec=max(12.0, budget - 0.5),
        )
        if text and text not in out:
            out.append(text)
    return out


def extract_page_tiles(
    doc: fitz.Document,
    page_index: int,
    *,
    dpi: int,
    deadline: float,
    tile_max_sec: float,
    overlap_frac: float,
    cols: int = TILE_COLS,
    rows: int = TILE_ROWS,
) -> tuple[list[str], int, int]:
    page = doc[page_index]
    supplements = page_supplement_jobs(page.rect)
    jobs = (
        page_tile_jobs_normative(page.rect, cols=cols, rows=rows, overlap_frac=overlap_frac)
        if supplements
        else page_tile_jobs(page.rect, cols=cols, rows=rows, overlap_frac=overlap_frac)
    )
    expected = len(jobs) + len(supplements)
    sources: list[str] = []
    attempted = 0

    for text in _ocr_supplement_tiles(
        doc, page_index, dpi=dpi, deadline=deadline, tile_max_sec=tile_max_sec
    ):
        attempted += 1
        if text not in sources:
            sources.append(text)

    for key, rect in jobs:
        left = deadline - time.monotonic()
        if left < 2:
            log.warning("tile OCR: budget stop page=%s at %s/%s", page_index + 1, attempted, expected)
            break
        attempted += 1
        remaining = expected - attempted + 1
        per_tile = max(15.0, min(60.0, (left - 1.5) / max(1, remaining)))
        zdead = min(deadline, time.monotonic() + min(tile_max_sec, per_tile))
        text = ocr_tile(doc, page_index, rect, zone=key, dpi=dpi, deadline=zdead, tile_max_sec=per_tile)
        if text and text not in sources:
            sources.append(text)

    return sources, attempted, expected


def merge_page_text(tile_chunks: list[str]) -> str:
    return "\n\n".join(t for t in tile_chunks if str(t or "").strip())


def extract_document_tiles(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """OCR документа по тайлам в рамках общего бюджета времени."""
    from belener.config import (
        normative_ocr_budget_sec,
        tile_grid_for_page_count,
        tile_ocr_dpi_for_pages,
        tile_ocr_max_pages,
        tile_ocr_overlap_frac,
    )

    t0 = time.monotonic()
    total_pages = doc.page_count
    cap = tile_ocr_max_pages() if max_pages is None else max_pages
    pages_to_scan = min(total_pages, cap) if cap and cap > 0 else total_pages

    budget = normative_ocr_budget_sec(pages_to_scan)
    deadline = t0 + budget
    dpi = tile_ocr_dpi_for_pages(pages_to_scan)
    overlap = tile_ocr_overlap_frac()
    cols, rows = tile_grid_for_page_count(pages_to_scan)
    tiles_per_page = cols * rows
    tile_max = max(15.0, min(60.0, (budget - 10) / max(1, pages_to_scan * tiles_per_page)))

    page_tiles: list[list[str]] = []
    all_sources: list[str] = []
    pages_processed = 0
    tiles_expected = 0
    tiles_done = 0
    budget_exhausted = False

    for i in range(pages_to_scan):
        if deadline - time.monotonic() < 4:
            budget_exhausted = True
            log.warning("tile OCR: budget stop before page=%s/%s", i + 1, pages_to_scan)
            break
        page_rect = doc[i].rect
        tiles_expected += len(page_tile_jobs(page_rect, cols=cols, rows=rows, overlap_frac=overlap))
        tiles_expected += len(page_supplement_jobs(page_rect))
        chunks, page_done, page_expected = extract_page_tiles(
            doc,
            i,
            dpi=dpi,
            deadline=deadline,
            tile_max_sec=tile_max,
            overlap_frac=overlap,
            cols=cols,
            rows=rows,
        )
        tiles_done += page_done
        pages_processed += 1
        page_tiles.append(chunks)
        for s in chunks:
            if s and s not in all_sources:
                all_sources.append(s)
        if page_done < page_expected:
            budget_exhausted = True
            log.warning("tile OCR: partial page=%s tiles=%s/%s", i + 1, page_done, page_expected)
            break
        if deadline - time.monotonic() < 4:
            budget_exhausted = True
            break

    page_texts = [merge_page_text(chunks) for chunks in page_tiles]
    elapsed = time.monotonic() - t0
    log.info(
        "tile OCR done %.1fs pages=%s/%s tiles=%s/%s chars=%s grid=%sx%s dpi=%s (%s)",
        elapsed,
        pages_processed,
        total_pages,
        tiles_done,
        tiles_expected,
        sum(len(t) for t in page_texts),
        cols,
        rows,
        dpi,
        filename,
    )
    return {
        "page_tiles": page_tiles,
        "page_texts": page_texts,
        "all_sources": all_sources,
        "elapsed_sec": elapsed,
        "tiles_count": len(all_sources),
        "tiles_expected": tiles_expected,
        "tiles_done": tiles_done,
        "dpi": dpi,
        "budget_sec": budget,
        "pages_processed": pages_processed,
        "pages_total": total_pages,
        "pages_planned": pages_to_scan,
        "budget_exhausted": budget_exhausted,
        "tile_cols": cols,
        "tile_rows": rows,
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
