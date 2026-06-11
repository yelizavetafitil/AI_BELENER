"""Поячеечный OCR основной надписи (ГОСТ): линии сетки → ячейка → Tesseract."""

from __future__ import annotations

import logging
import time
from typing import Any

import fitz
import numpy as np

log = logging.getLogger("belener.stamp_grid")

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def stamp_grid_available() -> bool:
    return cv2 is not None


def _render_gray(doc: fitz.Document, rect: fitz.Rect, page_index: int, dpi: int) -> tuple[np.ndarray, float]:
    page = doc[page_index]
    scale = dpi / 72.0
    clip = rect & page.rect
    pix = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        clip=clip,
        alpha=False,
    )
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n >= 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img.reshape(pix.height, pix.width)
    return gray, scale


def _line_mask(gray: np.ndarray) -> np.ndarray:
    nrow, ncol = gray.shape
    _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(2, ncol // 50), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(2, nrow // 50)))
    horiz = cv2.dilate(cv2.erode(bin_img, hk, iterations=2), hk, iterations=2)
    vert = cv2.dilate(cv2.erode(bin_img, vk, iterations=2), vk, iterations=2)
    return cv2.bitwise_or(horiz, vert)


def _find_cells(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    h, w = gray.shape
    inv = cv2.bitwise_not(_line_mask(gray))
    inv = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    contours, _ = cv2.findContours(inv, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    cells: list[tuple[int, int, int, int, int]] = []
    min_a = (w * h) * 0.00015
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw * bh < min_a or bw < 8 or bh < 8:
            continue
        if bw > w * 0.98 and bh > h * 0.98:
            continue
        cells.append((y, x, bw, bh, bw * bh))
    cells.sort(key=lambda t: (t[0], t[1]))
    return [(x, y, bw, bh) for y, x, bw, bh, _ in cells]


def _cluster_rows(cells: list[tuple[int, int, int, int]], tol: int) -> list[list[tuple[int, int, int, int]]]:
    if not cells:
        return []
    sorted_cells = sorted(cells, key=lambda c: (c[1] + c[3] // 2, c[0]))
    rows: list[list[tuple[int, int, int, int]]] = []
    current: list[tuple[int, int, int, int]] = []
    row_y = -1
    for cell in sorted_cells:
        cy = cell[1] + cell[3] // 2
        if row_y < 0 or abs(cy - row_y) <= tol:
            current.append(cell)
            row_y = cy if row_y < 0 else (row_y + cy) // 2
        else:
            if current:
                rows.append(sorted(current, key=lambda c: c[0]))
            current = [cell]
            row_y = cy
    if current:
        rows.append(sorted(current, key=lambda c: c[0]))
    return rows


def ocr_stamp_grid(
    doc: fitz.Document,
    rect: fitz.Rect,
    *,
    dpi: int = 480,
    page_index: int = 0,
) -> str:
    """Текст штампа: строки с | между ячейками (для parse_stamp)."""
    if not stamp_grid_available() or rect.width < 20 or rect.height < 20:
        return ""

    from belener.ocr import ocr_region

    t0 = time.monotonic()
    gray, scale = _render_gray(doc, rect, page_index, min(dpi, 520))
    cells = _find_cells(gray)
    if len(cells) < 4:
        log.info("stamp grid: few cells (%s), fallback", len(cells))
        return ""

    tol = max(6, int(gray.shape[0] * 0.018))
    rows = _cluster_rows(cells, tol)
    page = doc[page_index]
    lines: list[str] = []
    sig_lines: list[str] = []
    sig_x_max = rect.x0 + rect.width * 0.48

    from concurrent.futures import ThreadPoolExecutor

    # Собираем все ячейки в плоский список для параллельной обработки
    cell_jobs = []
    for row_idx, row in enumerate(rows):
        for col_idx, (x, y, bw, bh) in enumerate(row):
            pad = 1
            cell_rect = fitz.Rect(
                rect.x0 + (x + pad) / scale,
                rect.y0 + (y + pad) / scale,
                rect.x0 + (x + bw - pad) / scale,
                rect.y0 + (y + bh - pad) / scale,
            )
            cell_rect = cell_rect & page.rect
            if cell_rect.width >= 4 and cell_rect.height >= 4:
                cell_jobs.append((row_idx, col_idx, cell_rect))

    from belener.ocr import _render_clip, finalize_ocr_text
    from belener.paddle_ocr import paddle_batch_size, paddle_ocr_enabled, paddle_zone_match

    eff_dpi = min(dpi, 500)
    rendered_jobs: list[tuple[int, int, fitz.Rect, Any]] = []
    for r_idx, c_idx, crect in cell_jobs:
        img = _render_clip(doc, page_index, crect, eff_dpi)
        if img is not None:
            rendered_jobs.append((r_idx, c_idx, crect, img))

    results: list[tuple[int, int, fitz.Rect, str]] = []
    if paddle_ocr_enabled() and paddle_zone_match("stamp_cell") and rendered_jobs:
        from belener.paddle_ocr import ocr_pil_images_batch

        batch_n = paddle_batch_size()
        imgs = [j[3] for j in rendered_jobs]
        all_texts: list[str] = []
        for i in range(0, len(imgs), batch_n):
            all_texts.extend(ocr_pil_images_batch(imgs[i : i + batch_n], zone="stamp_cell"))
        for (r_idx, c_idx, crect, _), raw in zip(rendered_jobs, all_texts):
            txt = " ".join(finalize_ocr_text(raw or "", spell=True).split())
            results.append((r_idx, c_idx, crect, txt))
    else:

        def _process_cell(job):
            r_idx, c_idx, crect = job
            txt = (ocr_region(doc, page_index, crect, dpi=eff_dpi, zone="stamp_cell", psm=7) or "").strip()
            txt = " ".join(txt.split())
            return (r_idx, c_idx, crect, txt)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_process_cell, cell_jobs))

    # Раскладываем результаты обратно по строкам
    processed_rows = {}
    for r_idx, c_idx, crect, txt in results:
        if r_idx not in processed_rows:
            processed_rows[r_idx] = []
        processed_rows[r_idx].append((c_idx, crect, txt))

    for row_idx in range(len(rows)):
        if row_idx not in processed_rows:
            continue
        row_cells = sorted(processed_rows[row_idx], key=lambda x: x[0])
        parts: list[str] = []
        sig_parts: list[str] = []
        for c_idx, crect, txt in row_cells:
            parts.append(txt)
            if crect.x1 <= sig_x_max + rect.width * 0.05:
                sig_parts.append(txt)
        if parts:
            line = " | ".join(parts)
            if line.replace("|", "").strip():
                lines.append(line)
        if sig_parts and " | ".join(sig_parts).replace("|", "").strip():
            sig_lines.append(" | ".join(sig_parts))

    if not lines:
        return ""

    out_parts: list[str] = []
    if sig_lines:
        out_parts.append("--- stamp_sig ---")
        out_parts.extend(sig_lines)
        out_parts.append("---")
    out_parts.extend(lines)
    text = "\n".join(out_parts)
    log.info("stamp grid cells=%s rows=%s (%.1fs)", len(cells), len(rows), time.monotonic() - t0)
    return text
