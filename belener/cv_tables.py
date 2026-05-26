"""OpenCV table isolation for engineering drawings (local, no cloud).

Inspired by engineering-drawing-extractor (MIT) and cad-extract (partition/merged cells).
https://github.com/Bakkopi/engineering-drawing-extractor
https://github.com/ricklove/cad-extract
"""

from __future__ import annotations

import logging
import time
from typing import Any

import fitz
import numpy as np

from belener.config import cv_cells_enabled

log = logging.getLogger("belener.cv_tables")

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def cv_available() -> bool:
    return cv2 is not None and np is not None


def _page_to_gray(doc: fitz.Document, page_index: int, dpi: int) -> tuple[np.ndarray, float]:
    if cv2 is None:
        raise RuntimeError("opencv not installed")
    page = doc[page_index]
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n >= 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img.reshape(pix.height, pix.width)
    return gray, scale


def _table_line_mask(gray: np.ndarray) -> np.ndarray:
    nrow, ncol = gray.shape
    _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(2, ncol // 100), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(2, nrow // 100)))
    horiz = cv2.dilate(cv2.erode(bin_img, hk, iterations=3), hk, iterations=3)
    vert = cv2.dilate(cv2.erode(bin_img, vk, iterations=3), vk, iterations=3)
    lines = cv2.bitwise_or(horiz, vert)
    rect_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.dilate(lines, rect_k, iterations=2)


def _overlaps_stamp(rect: fitz.Rect, stamp_rect: fitz.Rect | None) -> bool:
    if stamp_rect is None or rect.is_empty:
        return False
    inter = rect & stamp_rect
    if inter.is_empty:
        return False
    return inter.get_area() / max(rect.get_area(), 1.0) > 0.2


def _find_table_blocks(
    gray: np.ndarray,
    page_rect: fitz.Rect,
    scale: float,
    *,
    stamp_rect: fitz.Rect | None = None,
) -> list[fitz.Rect]:
    h, w = gray.shape
    line_mask = _table_line_mask(gray)
    x_cut = int(w * 0.30)
    roi = line_mask[:, x_cut:]
    inv = cv2.bitwise_not(roi)
    inv = cv2.dilate(inv, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=2)
    contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    blocks: list[tuple[int, fitz.Rect]] = []
    page_h = page_rect.height
    min_area = (w * h) * 0.008
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw * bh < min_area or bh < h * 0.04 or bw < w * 0.08:
            continue
        y_bot = (y + bh) / h
        if y_bot > 0.76 and bh > h * 0.10:
            continue
        px_rect = fitz.Rect(
            x_cut / scale + page_rect.x0,
            y / scale + page_rect.y0,
            (x_cut + bw) / scale + page_rect.x0,
            (y + bh) / scale + page_rect.y0,
        )
        px_rect = px_rect & page_rect
        if _overlaps_stamp(px_rect, stamp_rect):
            continue
        blocks.append((y, px_rect))

    blocks.sort(key=lambda t: t[0])
    merged: list[fitz.Rect] = []
    for _, rect in blocks:
        if not merged:
            merged.append(rect)
            continue
        prev = merged[-1]
        if rect.y0 - prev.y1 < page_h * 0.02:
            merged[-1] = prev | rect
        else:
            merged.append(rect)
    return merged[:8]


def _cell_rects_in_block(
    gray: np.ndarray,
    block_rect: fitz.Rect,
    scale: float,
    page_rect: fitz.Rect,
) -> list[fitz.Rect]:
    """Ячейки таблицы внутри блока (пересечения линий сетки)."""
    x0 = int((block_rect.x0 - page_rect.x0) * scale)
    y0 = int((block_rect.y0 - page_rect.y0) * scale)
    x1 = int((block_rect.x1 - page_rect.x0) * scale)
    y1 = int((block_rect.y1 - page_rect.y0) * scale)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
    if x1 - x0 < 40 or y1 - y0 < 40:
        return []

    patch = gray[y0:y1, x0:x1]
    ph, pw = patch.shape
    _, bin_img = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(2, pw // 40), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(2, ph // 40)))
    horiz = cv2.dilate(cv2.erode(bin_img, hk, iterations=2), hk, iterations=2)
    vert = cv2.dilate(cv2.erode(bin_img, vk, iterations=2), vk, iterations=2)
    grid = cv2.bitwise_or(horiz, vert)
    cells_mask = cv2.bitwise_not(grid)
    cells_mask = cv2.morphologyEx(
        cells_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    )
    contours, _ = cv2.findContours(cells_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_cell = max(12, int(min(pw, ph) * 0.015))
    rects: list[tuple[int, int, fitz.Rect]] = []
    for cnt in contours:
        cx, cy, cw, ch = cv2.boundingRect(cnt)
        if cw < min_cell or ch < min_cell or cw * ch < min_cell * min_cell * 2:
            continue
        pad = 1
        rx0 = (x0 + cx + pad) / scale + page_rect.x0
        ry0 = (y0 + cy + pad) / scale + page_rect.y0
        rx1 = (x0 + cx + cw - pad) / scale + page_rect.x0
        ry1 = (y0 + cy + ch - pad) / scale + page_rect.y0
        rects.append((cy, cx, fitz.Rect(rx0, ry0, rx1, ry1) & block_rect))
    rects.sort(key=lambda t: (t[0], t[1]))
    return [r for _, _, r in rects[:400]]


def _ocr_rect(
    doc: fitz.Document,
    rect: fitz.Rect,
    page_index: int,
    dpi: int,
    *,
    psm: int = 7,
    zone: str = "table",
) -> str:
    from belener.ocr import ocr_region

    if rect.width < 8 or rect.height < 8:
        return ""
    return (ocr_region(doc, page_index, rect, dpi=dpi, zone=zone, psm=psm) or "").strip()


def _ocr_block_whole(doc: fitz.Document, rect: fitz.Rect, page_index: int, dpi: int) -> str:
    return _ocr_rect(doc, rect, page_index, dpi, psm=4, zone="tables_block")


def _cluster_row_indices(centers_y: list[float], tol: float) -> list[list[int]]:
    if not centers_y:
        return []
    order = sorted(range(len(centers_y)), key=lambda i: centers_y[i])
    groups: list[list[int]] = []
    cur = [order[0]]
    base_y = centers_y[order[0]]
    for idx in order[1:]:
        y = centers_y[idx]
        if y - base_y <= tol:
            cur.append(idx)
        else:
            groups.append(cur)
            cur = [idx]
            base_y = y
        base_y = sum(centers_y[i] for i in cur) / len(cur)
    groups.append(cur)
    return groups


def _split_row_into_columns(
    items: list[tuple[float, str]],
    block_width: float,
) -> list[str]:
    """Разбить ячейки строки по X-центрам (колонки ГОСТ-таблицы)."""
    if not items:
        return []
    items = sorted(items, key=lambda t: t[0])
    if len(items) == 1:
        return [items[0][1]]

    xs = [x for x, _ in items]
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    med_gap = sorted(gaps)[len(gaps) // 2] if gaps else block_width * 0.08
    split_gap = max(med_gap * 1.35, block_width * 0.06)

    cols: list[list[str]] = [[items[0][1]]]
    last_x = items[0][0]
    for x, txt in items[1:]:
        if x - last_x > split_gap:
            cols.append([txt])
        else:
            cols[-1].append(txt)
        last_x = x

    return [" ".join(parts).strip() for parts in cols if any(parts)]


def _ocr_block_by_cells(
    doc: fitz.Document,
    gray: np.ndarray,
    rect: fitz.Rect,
    scale: float,
    page_rect: fitz.Rect,
    page_index: int,
    dpi: int,
) -> str:
    cells = _cell_rects_in_block(gray, rect, scale, page_rect)
    if len(cells) < 4:
        return _ocr_block_whole(doc, rect, page_index, dpi)

    cell_data: list[tuple[float, float, str]] = []
    block_w = rect.width
    for cell in cells:
        wide = cell.width > block_w * 0.42
        psm = 6 if wide else 7
        txt = _ocr_rect(doc, cell, page_index, min(dpi, 520), psm=psm, zone="spec_right")
        if not txt:
            continue
        cx = (cell.x0 + cell.x1) / 2
        cy = (cell.y0 + cell.y1) / 2
        cell_data.append((cy, cx, txt))

    if len(cell_data) < 2:
        return _ocr_block_whole(doc, rect, page_index, dpi)

    centers_y = [c[0] for c in cell_data]
    row_tol = max(rect.height * 0.035, 8.0)
    row_groups = _cluster_row_indices(centers_y, row_tol)

    out_rows: list[str] = []
    for group in row_groups:
        row_items = [(cell_data[i][1], cell_data[i][2]) for i in group]
        cols = _split_row_into_columns(row_items, block_w)
        if cols:
            out_rows.append("\t".join(cols))

    if not out_rows:
        return _ocr_block_whole(doc, rect, page_index, dpi)
    return "\n".join(out_rows)


def ocr_table_rect_cells(
    doc: fitz.Document,
    rect: fitz.Rect,
    page_index: int = 0,
    *,
    dpi: int = 480,
) -> str:
    """OCR табличной зоны по ячейкам сетки (tab между колонками) — локально."""
    if not cv_available() or rect.is_empty:
        return ""
    page = doc[page_index]
    page_rect = page.rect
    eff_dpi = min(max(dpi, 360), 560)
    gray, scale = _page_to_gray(doc, page_index, dpi=eff_dpi)
    clipped = rect & page_rect
    if clipped.is_empty:
        return ""
    if cv_cells_enabled():
        text = _ocr_block_by_cells(doc, gray, clipped, scale, page_rect, page_index, eff_dpi)
    else:
        text = _ocr_block_whole(doc, clipped, page_index, eff_dpi)
    return (text or "").strip()


def extract_cv_tables(
    doc: fitz.Document,
    page_index: int = 0,
    *,
    dpi: int = 420,
    stamp_rect: fitz.Rect | None = None,
) -> dict[str, Any]:
    if not cv_available():
        return {"ok": False, "tables": [], "blocks": [], "table_text": ""}

    page = doc[page_index]
    page_rect = page.rect
    t0 = time.monotonic()
    eff_dpi = min(max(dpi, 360), 560)
    gray, scale = _page_to_gray(doc, page_index, dpi=eff_dpi)
    rects = _find_table_blocks(gray, page_rect, scale, stamp_rect=stamp_rect)
    try:
        from belener.blueprint_extract import blueprint_available, blueprint_table_rects

        if blueprint_available():
            bp_rects = blueprint_table_rects(gray, scale, page_rect, stamp_rect=stamp_rect)
            if len(bp_rects) > len(rects):
                rects = bp_rects
    except Exception:
        log.debug("blueprint block hints skipped", exc_info=True)
    log.info("CV table blocks=%s cells_mode=%s (%.1fs)", len(rects), cv_cells_enabled(), time.monotonic() - t0)

    from belener.parse import discover_table_sections

    sections: list[dict[str, Any]] = []
    texts: list[str] = []
    use_cells = cv_cells_enabled()
    for rect in rects:
        if use_cells:
            text = _ocr_block_by_cells(doc, gray, rect, scale, page_rect, page_index, eff_dpi)
        else:
            text = _ocr_block_whole(doc, rect, page_index, eff_dpi)
        if not text or len(text.strip()) < 8:
            continue
        texts.append(text)
        for sec in discover_table_sections(text):
            sec = dict(sec)
            sec["source"] = "cv_tables"
            sec["bbox"] = [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]
            if not sec.get("table_number"):
                sec["table_number"] = f"Таблица {len(sections) + 1}"
            sections.append(sec)

    return {
        "ok": bool(sections or texts),
        "tables": sections,
        "blocks": [
            {"index": i + 1, "bbox": [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]}
            for i, r in enumerate(rects)
        ],
        "table_text": "\n\n".join(texts),
        "pipeline": "belener_cv_tables",
    }
