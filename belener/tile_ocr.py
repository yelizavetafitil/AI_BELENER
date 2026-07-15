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
SUPP_SUB_COLS = 2
SUPP_SUB_ROWS = 2
SUPP_NOTES_ROWS = 2


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
    if cols <= 1 and rows <= 1:
        return [("tile_0_0", fitz.Rect(pr))]
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


def _tile_col(zone: str) -> int:
    return _tile_row_col(zone)[1]


def page_is_wide(page_rect: fitz.Rect) -> bool:
    """Альбом ISO A0–A2 (~1.41), не только сверхширокие листы."""
    pr = page_rect
    if pr.is_empty:
        return False
    return pr.width / max(pr.height, 1.0) >= 1.35


def page_tile_jobs_normative(
    page_rect: fitz.Rect,
    *,
    cols: int = TILE_COLS,
    rows: int = TILE_ROWS,
    overlap_frac: float = 0.12,
) -> list[tuple[str, fitz.Rect]]:
    """Правая половина листа первой: col↓, row↓ (спецификация, ТТ)."""
    jobs = page_tile_jobs(page_rect, cols=cols, rows=rows, overlap_frac=overlap_frac)
    if not page_is_wide(page_rect):
        return jobs
    return sorted(
        jobs,
        key=lambda item: (_tile_row_col(item[0])[1], _tile_row_col(item[0])[0]),
        reverse=True,
    )


def page_supplement_jobs(page_rect: fitz.Rect) -> list[tuple[str, fitz.Rect]]:
    """Одна зона спецификации справа — полное OCR (колонки кодов + наименование)."""
    pr = page_rect
    if not page_is_wide(pr):
        return []
    rect = fitz.Rect(
        pr.x0 + pr.width * 0.46,
        pr.y0 + pr.height * 0.22,
        pr.x1,
        pr.y0 + pr.height * 0.96,
    ) & pr
    if rect.is_empty or rect.width < 80 or rect.height < 80:
        return []
    return [("spec_right", rect)]


def subdivide_rect(
    key: str,
    rect: fitz.Rect,
    *,
    cols: int = SUPP_SUB_COLS,
    rows: int = SUPP_SUB_ROWS,
    overlap_frac: float = 0.08,
) -> list[tuple[str, fitz.Rect]]:
    if rect.is_empty or rect.width < 120 or rect.height < 120:
        return [(key, rect)]
    pr = rect
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
            clip = fitz.Rect(x0, y0, x1, y1) & pr
            if not clip.is_empty and clip.width > 40 and clip.height > 40:
                jobs.append((f"{key}_{row}_{col}", clip))
    return jobs or [(key, rect)]


def page_notes_jobs(page_rect: fitz.Rect, *, rows: int | None = None) -> list[tuple[str, fitz.Rect]]:
    """Нижняя полоса листа: ТТ, общие указания, перечень ТНПА."""
    pr = page_rect
    if pr.is_empty:
        return []
    lower = fitz.Rect(
        pr.x0 + pr.width * 0.02,
        pr.y0 + pr.height * 0.28,
        pr.x0 + pr.width * 0.98,
        pr.y0 + pr.height * 0.97,
    ) & pr
    if lower.is_empty or lower.width < 80 or lower.height < 80:
        return []
    note_rows = rows if rows is not None else SUPP_NOTES_ROWS
    return subdivide_rect("supp_notes", lower, rows=note_rows, overlap_frac=0.14)


def page_all_supplement_jobs(page_rect: fitz.Rect, *, notes_rows: int | None = None) -> list[tuple[str, fitz.Rect]]:
    jobs: list[tuple[str, fitz.Rect]] = []
    jobs.extend(page_supplement_jobs(page_rect))
    jobs.extend(page_notes_jobs(page_rect, rows=notes_rows))
    return jobs


def supplements_for_page_scan(page_rect: fitz.Rect, document_pages: int) -> list[tuple[str, fitz.Rect]]:
    """Меньше доп. зон на больших PDF — укладываемся в бюджет по листам."""
    n = max(1, int(document_pages))
    # Длинный том: полный лист (или грубая сетка) без дублирующих zone OCR.
    if n > 12:
        return []
    jobs = list(page_supplement_jobs(page_rect))
    if n <= 4:
        jobs.extend(page_notes_jobs(page_rect))
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


def _ocr_tile_tesseract(
    img, *, dpi: int, deadline: float, tile_max_sec: float, zone: str = "", fast: bool = False
) -> str:
    from belener.config import tile_ocr_psm_modes
    from belener.ocr import (
        _merge_ocr_passes,
        _tesseract_cli,
        finalize_ocr_text,
        ocr_lang,
    )

    now = time.monotonic()
    if now >= deadline:
        return ""
    deadline = min(deadline, now + max(1.0, tile_max_sec))
    lang = ocr_lang()
    if fast:
        modes = (6,)
    else:
        modes = list(tile_ocr_psm_modes())
        if (zone or "").startswith(("spec_", "supp_")):
            modes = list(dict.fromkeys(modes + [11]))
    parts: list[str] = []
    for psm in modes:
        if time.monotonic() >= deadline:
            break
        left = deadline - time.monotonic()
        if left < 0.5:
            break
        cap = 18.0 if fast else 32.0
        tout = min(left, tile_max_sec, cap)
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
    force_ocr: bool = False,
    fast: bool = False,
) -> str:
    from belener.config import normative_force_tile_ocr, tile_text_skip_ocr_min_chars
    from belener.ocr import _render_clip

    if rect is None or rect.is_empty or time.monotonic() >= deadline:
        return ""

    force = force_ocr or normative_force_tile_ocr()
    t0 = time.monotonic()
    page = doc[page_index]
    parts: list[str] = []
    layer = _pdf_text_in_rect(page, rect)
    if layer:
        parts.append(layer)

    if time.monotonic() >= deadline:
        return "\n\n".join(parts)

    skip_min = tile_text_skip_ocr_min_chars()
    if not force and layer and skip_min > 0 and len(layer) >= skip_min:
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
            fast=fast,
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
    supplement_jobs: list[tuple[str, fitz.Rect]] | None = None,
    dpi: int,
    deadline: float,
    tile_max_sec: float,
    force_ocr: bool = False,
    quality: bool = True,
    word_sink: list | None = None,
    zone_sink: list | None = None,
) -> list[str]:
    from belener.config import normative_supplement_budget_sec, normative_wide_right_dpi_boost
    from belener.ocr import tesseract_words_from_rect

    page = doc[page_index]
    out: list[str] = []
    jobs = supplement_jobs if supplement_jobs is not None else page_all_supplement_jobs(page.rect)
    if not jobs:
        return out
    per_zone = min(22.0, max(14.0, normative_supplement_budget_sec()))
    boost = normative_wide_right_dpi_boost() if quality else 1.0
    for key, rect in jobs:
        left = deadline - time.monotonic()
        if left < 5.0:
            log.warning("tile OCR: supplement skipped page=%s left=%.1fs", page_index + 1, left)
            break
        is_notes = (key or "").startswith("supp_notes")
        zone_quality = quality and left >= 14.0 and not is_notes
        notes_cap = min(16.0, per_zone)
        budget = min(notes_cap if is_notes else per_zone, max(10.0 if is_notes else 12.0, left - 1.0))
        zdead = min(deadline, time.monotonic() + budget)
        sup_dpi = min(int(dpi * (1.0 if is_notes else boost)), 320 if is_notes else 400)
        text = ocr_tile(
            doc,
            page_index,
            rect,
            zone=key,
            dpi=sup_dpi,
            deadline=zdead,
            tile_max_sec=max(10.0 if is_notes else 12.0, budget - 0.5),
            force_ocr=force_ocr,
            fast=not zone_quality or is_notes,
        )
        if text and text not in out:
            out.append(text)
        if zone_sink is not None and text:
            zone_sink.append((key, fitz.Rect(rect), text))
        if word_sink is not None and text and left >= 8.0:
            wleft = min(12.0, max(6.0, left - 2.0))
            words = tesseract_words_from_rect(
                doc, page_index, rect, dpi=sup_dpi, timeout=wleft,
            )
            if words:
                word_sink.extend(words)
    return out


def _split_grid_jobs(
    jobs: list[tuple[str, fitz.Rect]], cols: int
) -> tuple[list[tuple[str, fitz.Rect]], list[tuple[str, fitz.Rect]]]:
    right: list[tuple[str, fitz.Rect]] = []
    left: list[tuple[str, fitz.Rect]] = []
    mid = cols // 2
    for item in jobs:
        if _tile_col(item[0]) >= mid:
            right.append(item)
        else:
            left.append(item)
    return right, left


def _ocr_job_list(
    doc: fitz.Document,
    page_index: int,
    jobs: list[tuple[str, fitz.Rect]],
    *,
    sources: list[str],
    attempted: int,
    remaining_total: int,
    dpi: int,
    deadline: float,
    tile_max_sec: float,
    force_ocr: bool,
    high_quality: bool,
    word_sink: list | None = None,
    zone_sink: list | None = None,
) -> tuple[int, int]:
    from belener.config import normative_wide_right_dpi_boost
    from belener.ocr import tesseract_words_from_rect

    boost = normative_wide_right_dpi_boost() if high_quality else 1.0
    done = attempted
    left_count = remaining_total
    for key, rect in jobs:
        left = deadline - time.monotonic()
        if left < 2:
            log.warning("tile OCR: budget stop page=%s at %s", page_index + 1, done)
            break
        done += 1
        left_count = max(1, remaining_total - (done - attempted))
        tight = left / left_count < 22.0
        use_hq = high_quality and not tight
        use_fast = tight or (not use_hq and left / left_count < 30.0)
        mult = 1.2 if use_hq else 1.0
        per_tile = max(10.0, min(38.0 if use_hq else 26.0, (left - 2.0) / left_count * mult))
        zdead = min(deadline, time.monotonic() + min(tile_max_sec * mult, per_tile))
        tdpi = min(int(dpi * boost), 400) if use_hq else dpi
        text = ocr_tile(
            doc,
            page_index,
            rect,
            zone=key,
            dpi=tdpi,
            deadline=zdead,
            tile_max_sec=per_tile,
            force_ocr=force_ocr,
            fast=use_fast,
        )
        if text and text not in sources:
            sources.append(text)
        if zone_sink is not None and text:
            zone_sink.append((key, fitz.Rect(rect), text))
        if word_sink is not None and text and left >= 8.0:
            wleft = min(12.0, max(6.0, left - 2.0))
            words = tesseract_words_from_rect(
                doc, page_index, rect, dpi=tdpi, timeout=wleft,
            )
            if words:
                word_sink.extend(words)
    return done, left_count


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
    force_ocr: bool = False,
    document_pages: int = 1,
    word_sink: list | None = None,
    zone_sink: list | None = None,
) -> tuple[list[str], int, int]:
    page = doc[page_index]
    wide = page_is_wide(page.rect)
    supplements = supplements_for_page_scan(page.rect, document_pages)
    overlap_use = max(overlap_frac, 0.18) if wide else overlap_frac
    jobs = (
        page_tile_jobs_normative(page.rect, cols=cols, rows=rows, overlap_frac=overlap_use)
        if wide
        else sorted(
            page_tile_jobs(page.rect, cols=cols, rows=rows, overlap_frac=overlap_frac),
            key=lambda item: (_tile_row_col(item[0])[0], _tile_row_col(item[0])[1]),
            reverse=True,
        )
    )
    expected = len(jobs) + len(supplements)
    sources: list[str] = []
    attempted = 0
    multi_page = int(document_pages) > 4
    long_doc = int(document_pages) > 12
    # На длинных томах не гоняем «качественный» multi-PSM — иначе не успеваем все листы.
    sup_quality = not multi_page and not long_doc
    page_hq = not long_doc

    if wide:
        right_jobs, left_jobs = _split_grid_jobs(jobs, cols)
        log.info(
            "tile OCR wide page=%s right_tiles=%s spec=%s left_tiles=%s",
            page_index + 1,
            len(right_jobs),
            len(supplements),
            len(left_jobs),
        )
        if supplements and deadline - time.monotonic() >= 8:
            for text in _ocr_supplement_tiles(
                doc, page_index, supplement_jobs=supplements, dpi=dpi, deadline=deadline,
                tile_max_sec=tile_max_sec, force_ocr=force_ocr,
                quality=sup_quality, word_sink=word_sink, zone_sink=zone_sink,
            ):
                attempted += 1
                if text not in sources:
                    sources.append(text)
        attempted, _ = _ocr_job_list(
            doc, page_index, right_jobs,
            sources=sources, attempted=attempted, remaining_total=expected,
            dpi=dpi, deadline=deadline, tile_max_sec=tile_max_sec,
            force_ocr=force_ocr, high_quality=page_hq and not multi_page,
            word_sink=word_sink, zone_sink=zone_sink,
        )
        attempted, _ = _ocr_job_list(
            doc, page_index, left_jobs,
            sources=sources, attempted=attempted, remaining_total=expected - attempted,
            dpi=dpi, deadline=deadline, tile_max_sec=tile_max_sec,
            force_ocr=force_ocr, high_quality=False,
            word_sink=word_sink, zone_sink=zone_sink,
        )
    else:
        if supplements and deadline - time.monotonic() >= 8:
            for text in _ocr_supplement_tiles(
                doc, page_index, supplement_jobs=supplements, dpi=dpi, deadline=deadline,
                tile_max_sec=tile_max_sec, force_ocr=force_ocr,
                quality=sup_quality, word_sink=word_sink, zone_sink=zone_sink,
            ):
                attempted += 1
                if text not in sources:
                    sources.append(text)
        bottom_row = rows - 1
        hq_jobs = [j for j in jobs if _tile_row_col(j[0])[0] == bottom_row]
        other_jobs = [j for j in jobs if _tile_row_col(j[0])[0] != bottom_row]
        if hq_jobs:
            attempted, _ = _ocr_job_list(
                doc, page_index, hq_jobs,
                sources=sources, attempted=attempted, remaining_total=expected - attempted,
                dpi=dpi, deadline=deadline, tile_max_sec=tile_max_sec,
                force_ocr=force_ocr, high_quality=page_hq,
                word_sink=word_sink, zone_sink=zone_sink,
            )
        if other_jobs:
            attempted, _ = _ocr_job_list(
                doc, page_index, other_jobs,
                sources=sources, attempted=attempted, remaining_total=expected - attempted,
                dpi=dpi, deadline=deadline, tile_max_sec=tile_max_sec,
                force_ocr=force_ocr, high_quality=False,
                word_sink=word_sink, zone_sink=zone_sink,
            )

    return sources, attempted, expected


def merge_page_text(tile_chunks: list[str]) -> str:
    return "\n\n".join(t for t in tile_chunks if str(t or "").strip())


def collect_page_preview_words(
    doc: fitz.Document,
    page_index: int,
    *,
    page_count: int = 1,
    deadline: float | None = None,
) -> list:
    """Слова с bbox для подсветки на сканах (последовательный Tesseract по тайлам)."""
    from belener.config import tile_grid_for_page_count, tile_ocr_dpi_for_pages
    from belener.ocr import tesseract_words_from_rect

    page = doc[page_index]
    cols, rows = tile_grid_for_page_count(page_count)
    dpi = tile_ocr_dpi_for_pages(page_count)
    tile_timeout = 18.0 if page_count <= 1 else 10.0
    if page_is_wide(page.rect):
        jobs = page_tile_jobs_normative(page.rect, cols=cols, rows=rows)
    else:
        jobs = page_tile_jobs(page.rect, cols=cols, rows=rows)
    jobs.extend(supplements_for_page_scan(page.rect, page_count))

    ocr_words: list = []
    for _zone, rect in jobs:
        if deadline is not None and time.monotonic() >= deadline:
            break
        left = 18.0 if deadline is None else max(4.0, deadline - time.monotonic())
        words = tesseract_words_from_rect(
            doc,
            page_index,
            rect,
            dpi=dpi,
            timeout=min(tile_timeout, left),
        )
        if words:
            ocr_words.extend(words)
    return ocr_words


def _page_has_usable_text(doc: fitz.Document, page_index: int) -> bool:
    from belener.scanned import page_text_layer_usable

    return page_text_layer_usable(doc, page_index)


def extract_document_tiles(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    max_pages: int | None = None,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    """OCR документа по тайлам в рамках общего бюджета времени."""
    from belener.config import (
        normative_force_tile_ocr,
        ocr_budget_for_gost_check,
        tile_grid_for_page_count,
        tile_ocr_dpi_for_pages,
        tile_ocr_max_pages,
        tile_ocr_overlap_frac,
    )

    force_ocr = normative_force_tile_ocr()

    t0 = time.monotonic()
    total_pages = doc.page_count
    cap = tile_ocr_max_pages() if max_pages is None else max_pages
    pages_to_scan = min(total_pages, cap) if cap and cap > 0 else total_pages

    budget = ocr_budget_for_gost_check(pipeline_deadline=pipeline_deadline, page_count=pages_to_scan)
    if doc is not None:
        from belener.config import normative_ocr_budget_sec

        budget = min(budget, normative_ocr_budget_sec(pages_to_scan, doc=doc))
    deadline = t0 + budget
    if pipeline_deadline is not None:
        deadline = min(deadline, pipeline_deadline)
    dpi = tile_ocr_dpi_for_pages(pages_to_scan)
    overlap = tile_ocr_overlap_frac()
    cols, rows = tile_grid_for_page_count(pages_to_scan)
    if pages_to_scan == 1 and doc.page_count >= 1:
        tiles_per_page = len(page_tile_jobs(doc[0].rect, cols=cols, rows=rows)) + len(
            page_all_supplement_jobs(doc[0].rect)
        )
    else:
        tiles_per_page = max(1, cols * rows)
    # Ровный лимит на лист: иначе первые титульные съедают бюджет и ГОСТ на хвосте теряются.
    full_page = cols * rows <= 1
    min_pass = 14.0 if full_page else (6.0 if pages_to_scan > 12 else 5.0)
    per_page_cap = max(14.0 if pages_to_scan > 12 else 8.0, (budget - 6.0) / max(1, pages_to_scan))
    if pages_to_scan > 12:
        per_page_cap = max(per_page_cap, min_pass + 6.0)
    tile_max = max(8.0, min(60.0 if pages_to_scan <= 1 else 26.0, per_page_cap / max(1, tiles_per_page)))
    if pages_to_scan > 12 and full_page:
        tile_max = max(tile_max, min(24.0, per_page_cap - 1.0))

    page_tiles: list[list[str]] = []
    page_preview_words: list[list] = []
    page_tile_zones: list[list] = []
    all_sources: list[str] = []
    pages_processed = 0
    tiles_expected = 0
    tiles_done = 0
    budget_exhausted = False

    for i in range(pages_to_scan):
        left_total = deadline - time.monotonic()
        pages_left = pages_to_scan - i
        # Резерв на каждый оставшийся лист — не оставляем хвост без OCR.
        reserve_rest = max(0, pages_left - 1) * min_pass
        available = left_total - reserve_rest
        if available < min_pass * 0.55 and pages_left > 1:
            # Жёстко: всё же даём минимальный проход текущему листу.
            available = max(min_pass * 0.7, left_total / max(1, pages_left))
        if left_total < 2.5:
            budget_exhausted = True
            log.warning("tile OCR: budget stop before page=%s/%s", i + 1, pages_to_scan)
            break
        page_rect = doc[i].rect
        page_jobs = page_tile_jobs(page_rect, cols=cols, rows=rows, overlap_frac=overlap)
        page_supps = supplements_for_page_scan(page_rect, pages_to_scan)
        tiles_expected += len(page_jobs) + len(page_supps)
        page_share = left_total / max(1, pages_left)
        this_budget = max(min_pass * 0.7, min(per_page_cap + 3.0, page_share, max(available, min_pass * 0.7)))
        page_deadline = min(deadline, time.monotonic() + this_budget)
        use_preview_words = pages_to_scan <= 12
        pw: list = []
        zs: list = []
        chunks, page_done, page_expected = extract_page_tiles(
            doc,
            i,
            dpi=dpi,
            deadline=page_deadline,
            tile_max_sec=min(tile_max, max(8.0, this_budget - 0.5)),
            overlap_frac=overlap,
            cols=cols,
            rows=rows,
            force_ocr=force_ocr,
            document_pages=pages_to_scan,
            word_sink=pw if use_preview_words else None,
            zone_sink=zs if use_preview_words else None,
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
        text_words = doc[i].get_text("words") or []
        combined: list = []
        if _page_has_usable_text(doc, i):
            combined.extend(text_words)
        if pw:
            combined.extend(pw)
        if combined:
            page_preview_words.append(combined)
            page_tile_zones.append(zs)
            if pw and not _page_has_usable_text(doc, i):
                log.info("tile OCR preview words page=%s count=%s zones=%s", i + 1, len(pw), len(zs))
        elif use_preview_words and time.monotonic() < deadline + 30:
            pw2 = collect_page_preview_words(
                doc,
                i,
                page_count=pages_to_scan,
                deadline=time.monotonic() + min(45.0, max(15.0, deadline - time.monotonic() + 25)),
            )
            page_preview_words.append(pw2)
            page_tile_zones.append(zs)
        else:
            page_preview_words.append([])
            page_tile_zones.append([])
        if deadline - time.monotonic() < 3:
            budget_exhausted = True
            if i + 1 < pages_to_scan:
                log.warning("tile OCR: budget low after page=%s/%s", i + 1, pages_to_scan)
            break

    page_texts = [merge_page_text(chunks) for chunks in page_tiles]
    elapsed = time.monotonic() - t0
    if pages_to_scan == 1 and pages_processed == 1 and tiles_done >= tiles_expected and tiles_expected > 0:
        budget_exhausted = False
    log.info(
        "tile OCR done %.1fs pages=%s/%s tiles=%s/%s chars=%s grid=%sx%s dpi=%s budget=%.0fs (%s)",
        elapsed,
        pages_processed,
        total_pages,
        tiles_done,
        tiles_expected,
        sum(len(t) for t in page_texts),
        cols,
        rows,
        dpi,
        budget,
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
        "page_preview_words": page_preview_words,
        "page_tile_zones": page_tile_zones,
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
