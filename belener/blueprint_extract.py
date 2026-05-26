"""Извлечение таблиц и ячеек штампа по алгоритму engineering-drawing-extractor (MIT).

Идеи cad-extract: разделение соседних таблиц, объединённые ячейки, OCR по ячейкам.
https://github.com/Bakkopi/engineering-drawing-extractor
https://github.com/ricklove/cad-extract
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import fitz
import numpy as np

from belener.config import ocr_lang
from belener.zones import SheetZones

log = logging.getLogger("belener.blueprint")

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

# Метки полей штампа (ГОСТ + англ. чертежи) — без привязки к организации/проекту
_STAMP_LABELS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"обознач|шифр|drawing\s*no|drawing\s*number", re.I), "Обозначение / шифр"),
    (re.compile(r"разраб\.?|drawn\s*by|^drawn\b", re.I), "Разраб."),
    (re.compile(r"н\.?\s*контр|checked\s*by", re.I), "Н.контр."),
    (re.compile(r"нач\.?\s*отд|утв\.?|approved", re.I), "Нач. отд."),
    (re.compile(r"главн|глав\.?\s*спец", re.I), "Гл. спец."),
    (re.compile(r"лист\b|sheet\b|page\b", re.I), "Лист"),
    (re.compile(r"масштаб|scale", re.I), "Масштаб"),
    (re.compile(r"формат|format", re.I), "Формат"),
    (re.compile(r"стадия|status", re.I), "Стадия"),
    (re.compile(r"копиров", re.I), "Копировал"),
    (re.compile(r"изм\.?", re.I), "Изм."),
    (re.compile(r"подп\.?", re.I), "Подп."),
    (re.compile(r"дата\b|date\b", re.I), "Дата"),
    (re.compile(r"организ|предприят|company|contractor", re.I), "Организация"),
    (re.compile(r"наименован|title|drawing\s*title", re.I), "Наименование"),
]


def blueprint_available() -> bool:
    return cv2 is not None


def _page_gray(doc: fitz.Document, page_index: int, dpi: int) -> tuple[np.ndarray, float, fitz.Rect]:
    page = doc[page_index]
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n >= 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img.reshape(pix.height, pix.width)
    return gray, scale, page.rect


def _combined_line_mask(bin_img: np.ndarray) -> np.ndarray:
    """Горизонтальные + вертикальные линии (как mainExtractionOCR.py)."""
    nrow, ncol = bin_img.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, ncol // 150), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(1, nrow // 150)))
    vert = cv2.dilate(cv2.erode(bin_img, hk, iterations=5), hk, iterations=5)
    horiz = cv2.dilate(cv2.erode(bin_img, vk, iterations=5), vk, iterations=5)
    return cv2.bitwise_or(vert, horiz)


def separate_tables_from_drawing(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Маски: table_lines (белый фон, чёрные линии таблиц) и drawing (поле схемы)."""
    nrow, ncol = gray.shape
    _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    combined = _combined_line_mask(bin_img)
    rect_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    drawing_mask = cv2.erode(combined, rect_k, iterations=2)
    drawing_mask = cv2.dilate(drawing_mask, rect_k, iterations=50)
    table_lines = drawing_mask + np.bitwise_not(combined)
    table_lines = np.clip(table_lines, 0, 255).astype(np.uint8)

    table_dil = cv2.dilate(np.bitwise_not(table_lines), rect_k, iterations=5)
    contours, _ = cv2.findContours(table_dil, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    table_bgr = cv2.cvtColor(table_lines, cv2.COLOR_GRAY2BGR)
    for cnt in sorted(contours, key=cv2.contourArea):
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 30 or h < 30:
            cv2.drawContours(table_bgr, [cnt], -1, (255, 255, 255), thickness=-1)

    table_only = cv2.cvtColor(table_bgr, cv2.COLOR_BGR2GRAY)
    _, table_only = cv2.threshold(table_only, 150, 255, cv2.THRESH_BINARY)

    table_mask = cv2.dilate(np.bitwise_not(table_bgr[:, :, 0]), rect_k, iterations=5)
    drawing = np.bitwise_not(bin_img) + table_mask
    drawing = np.where(drawing >= 5, 255, 0).astype(np.uint8)
    tables = np.bitwise_not(bin_img) + np.bitwise_not(table_mask)
    tables = np.where(tables >= 5, 255, 0).astype(np.uint8)
    return table_only, drawing


def _remove_lines_for_ocr(cell: np.ndarray) -> np.ndarray:
    """Убрать линии сетки в ячейке перед OCR (drawingNum.py)."""
    if cv2 is None:
        return cell
    copy = cell.copy()
    _, thresh = cv2.threshold(cell, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, cell.shape[1] // 4), 1))
    v_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, cell.shape[0] // 6)))
    for kernel in (h_k, v_k):
        opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=2)
        cnts, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts or []:
            cv2.drawContours(copy, [c], -1, 255, 5)
    return copy


def _ocr_cell_psm6(cell: np.ndarray, lang: str) -> str:
    import pytesseract

    cleaned = _remove_lines_for_ocr(cell)
    cfg = f"--psm 6 -l {lang}"
    try:
        return (pytesseract.image_to_string(cleaned, config=cfg) or "").strip()
    except Exception:
        return ""


def _expand_label_value(cell_img: np.ndarray, label: str, lang: str) -> list[str]:
    """Если в ячейке только метка — читаем область ниже (reanalyze titles)."""
    lines = [ln.strip() for ln in _ocr_cell_psm6(cell_img, lang).splitlines() if ln.strip()]
    if len("".join(lines)) > len(label) + 4:
        return lines
    h, w = cell_img.shape[:2]
    y2 = min(h + int(h * 1.2), cell_img.shape[0] + 80) if h < 400 else h
    pad = cell_img
    if y2 > h:
        extra = np.full((y2 - h, w), 255, dtype=np.uint8)
        pad = np.vstack([cell_img, extra])
    return [ln.strip() for ln in _ocr_cell_psm6(pad, lang).splitlines() if ln.strip()]


def _match_stamp_label(text: str) -> str | None:
    blob = text.replace("\n", " ")
    for pat, field in _STAMP_LABELS:
        if pat.search(blob):
            return field
    return None


def _value_after_label(lines: list[str], label_pat: re.Pattern[str]) -> str:
    for i, ln in enumerate(lines):
        if label_pat.search(ln):
            for j in range(i + 1, min(i + 4, len(lines))):
                v = lines[j].strip()
                if v and not _match_stamp_label(v):
                    return v
    return ""


def _find_table_contours(table_only: np.ndarray) -> list[tuple[int, int, int, int]]:
    padded = cv2.copyMakeBorder(table_only, 5, 5, 5, 5, cv2.BORDER_CONSTANT, value=0)
    rect_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dil = cv2.dilate(np.bitwise_not(padded), rect_k, iterations=1)
    contours, _ = cv2.findContours(dil, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    nrow, ncol = table_only.shape
    max_area = (nrow * ncol) * 0.45
    cells: list[tuple[int, int, int, int]] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 5 <= x and 5 <= y:
            x, y = x - 5, y - 5
        area = w * h
        if area < 800 or area > max_area or h < 12 or w < 20:
            continue
        cells.append((x, y, w, h))
    return cells


def _cluster_table_blocks(
    cells: list[tuple[int, int, int, int]],
    shape: tuple[int, int],
    *,
    min_x_frac: float = 0.22,
) -> list[tuple[int, int, int, int]]:
    """Группы ячеек → прямоугольники таблиц (cad-extract: partition tables)."""
    if not cells:
        return []
    h, w = shape
    x_min = int(w * min_x_frac)
    filtered = [(x, y, bw, bh) for x, y, bw, bh in cells if x >= x_min or (bw * bh) > (w * h * 0.02)]
    if not filtered:
        filtered = cells
    filtered.sort(key=lambda t: (t[1], t[0]))
    groups: list[list[tuple[int, int, int, int]]] = []
    for cell in filtered:
        cx, cy = cell[0] + cell[2] // 2, cell[1] + cell[3] // 2
        placed = False
        for grp in groups:
            gx0 = min(c[0] for c in grp)
            gy0 = min(c[1] for c in grp)
            gx1 = max(c[0] + c[2] for c in grp)
            gy1 = max(c[1] + c[3] for c in grp)
            gap = max(25, int(min(w, h) * 0.02))
            if gx0 - gap <= cx <= gx1 + gap and gy0 - gap <= cy <= gy1 + gap:
                grp.append(cell)
                placed = True
                break
        if not placed:
            groups.append([cell])
    blocks: list[tuple[int, int, int, int]] = []
    min_cells = 6
    for grp in groups:
        if len(grp) < min_cells:
            continue
        x0 = min(c[0] for c in grp)
        y0 = min(c[1] for c in grp)
        x1 = max(c[0] + c[2] for c in grp)
        y1 = max(c[1] + c[3] for c in grp)
        if (x1 - x0) * (y1 - y0) < w * h * 0.004:
            continue
        if y1 > h * 0.78 and (y1 - y0) > h * 0.12:
            continue
        blocks.append((x0, y0, x1 - x0, y1 - y0))
    blocks.sort(key=lambda b: (b[1], b[0]))
    return blocks[:10]


def _cells_to_tsv(
    gray: np.ndarray,
    block: tuple[int, int, int, int],
    lang: str,
) -> str:
    x, y, w, h = block
    patch = gray[y : y + h, x : x + w]
    cell_boxes = _find_table_contours(
        cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        if patch.size
        else patch
    )
    if len(cell_boxes) < 4:
        return _ocr_cell_psm6(patch, lang)

    rows: list[list[str]] = []
    current: list[str] = []
    last_cy = -1
    tol = max(8, h * 0.04)
    for cx, cy, cw, ch in sorted(cell_boxes, key=lambda t: (t[1], t[0])):
        cell = patch[cy : cy + ch, cx : cx + cw]
        txt = _ocr_cell_psm6(cell, lang)
        if not txt:
            continue
        mid_y = cy + ch // 2
        if last_cy >= 0 and mid_y - last_cy > tol and current:
            rows.append(current)
            current = []
        current.append(txt.replace("\n", " "))
        last_cy = mid_y
    if current:
        rows.append(current)
    return "\n".join("\t".join(r) for r in rows)


def _px_rect(
    x: int, y: int, w: int, h: int, scale: float, page_rect: fitz.Rect
) -> fitz.Rect:
    return fitz.Rect(x / scale, y / scale, (x + w) / scale, (y + h) / scale) & page_rect


def _overlaps_stamp(rect: fitz.Rect, stamp: fitz.Rect | None) -> bool:
    if stamp is None or rect.is_empty:
        return False
    inter = rect & stamp
    return not inter.is_empty and inter.get_area() / max(rect.get_area(), 1.0) > 0.25


def extract_stamp_from_blueprint(
    doc: fitz.Document,
    page_index: int,
    stamp_rect: fitz.Rect,
    gray: np.ndarray,
    scale: float,
    page_rect: fitz.Rect,
    *,
    dpi: int = 480,
) -> dict[str, Any]:
    """Ячейки штампа: сетка ГОСТ + метки (engineering-drawing-extractor)."""
    from belener.parse import parse_stamp
    from belener.stamp_grid import ocr_stamp_grid, stamp_grid_available

    if stamp_grid_available():
        grid_text = ocr_stamp_grid(doc, stamp_rect, page_index, dpi=dpi)
        if grid_text:
            parsed = parse_stamp(grid_text)
            if parsed.get("kv") or parsed.get("signatures"):
                return parsed

    lang = ocr_lang()
    x0 = max(0, int((stamp_rect.x0 - page_rect.x0) * scale))
    y0 = max(0, int((stamp_rect.y0 - page_rect.y0) * scale))
    x1 = min(gray.shape[1], int((stamp_rect.x1 - page_rect.x0) * scale))
    y1 = min(gray.shape[0], int((stamp_rect.y1 - page_rect.y0) * scale))
    if x1 - x0 < 40 or y1 - y0 < 40:
        return {"kv": [], "signatures": [], "raw_lines": []}

    patch = gray[y0:y1, x0:x1]
    _, table_only = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    table_only = cv2.bitwise_not(table_only)
    cells = _find_table_contours(table_only)
    ph, pw = patch.shape
    max_cell = (ph // 3) * (pw // 3)
    kv: list[dict[str, str]] = []
    sigs: list[dict[str, str]] = []
    raw: list[str] = []

    for cx, cy, cw, ch in cells:
        if cw * ch > max_cell or ch > 400:
            continue
        cell = patch[cy : cy + ch, cx : cx + cw]
        lines = _expand_label_value(cell, "", lang)
        if not lines:
            continue
        blob = " ".join(lines)
        raw.append(blob)
        field = _match_stamp_label(blob)
        if not field:
            continue
        if field in ("Разраб.", "Н.контр.", "Нач. отд.", "Гл. спец.", "Копировал"):
            val = ""
            for ln in lines:
                if not _match_stamp_label(ln) and len(ln) >= 3:
                    val = ln
                    break
            if val:
                sigs.append({"role": field, "name": val.split()[0][:40], "date": "—", "sign": "—"})
        else:
            val = ""
            for ln in lines:
                if not _match_stamp_label(ln) and len(ln) >= 2:
                    val = ln
                    break
            if val:
                kv.append({"field": field, "value": val[:200]})

    if kv or sigs:
        return {"kv": kv, "signatures": sigs, "raw_lines": raw}
    if raw:
        return parse_stamp("\n".join(raw))
    return {"kv": [], "signatures": [], "raw_lines": []}


def blueprint_table_rects(
    gray: np.ndarray,
    scale: float,
    page_rect: fitz.Rect,
    *,
    stamp_rect: fitz.Rect | None = None,
) -> list[fitz.Rect]:
    """Прямоугольники таблиц после отделения от поля чертежа."""
    table_only, _ = separate_tables_from_drawing(gray)
    cells = _find_table_contours(table_only)
    blocks = _cluster_table_blocks(cells, gray.shape)
    out: list[fitz.Rect] = []
    for x, y, w, h in blocks:
        rect = _px_rect(x, y, w, h, scale, page_rect)
        if not _overlaps_stamp(rect, stamp_rect):
            out.append(rect)
    return out


def extract_blueprint_page(
    doc: fitz.Document,
    page_index: int = 0,
    *,
    zones: SheetZones | None = None,
    dpi: int = 440,
) -> dict[str, Any]:
    if not blueprint_available():
        return {"ok": False, "tables": [], "table_text": "", "stamp": None}

    t0 = time.monotonic()
    lang = ocr_lang()
    eff_dpi = max(360, min(dpi, 520))
    gray, scale, page_rect = _page_gray(doc, page_index, eff_dpi)
    stamp_rect = None
    if zones:
        stamp_rect = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block")

    table_only, _drawing = separate_tables_from_drawing(gray)
    cells = _find_table_contours(table_only)
    blocks_px = _cluster_table_blocks(cells, gray.shape)

    from belener.parse import discover_table_sections

    sections: list[dict[str, Any]] = []
    texts: list[str] = []
    for bx, by, bw, bh in blocks_px:
        rect = _px_rect(bx, by, bw, bh, scale, page_rect)
        if _overlaps_stamp(rect, stamp_rect):
            continue
        tsv = _cells_to_tsv(gray, (bx, by, bw, bh), lang)
        if len(tsv.strip()) < 10:
            continue
        texts.append(tsv)
        for sec in discover_table_sections(tsv):
            sec = dict(sec)
            sec["source"] = "blueprint_extract"
            sec["bbox"] = [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]
            if not sec.get("table_number"):
                sec["table_number"] = f"Таблица {len(sections) + 1}"
            sections.append(sec)

    log.info(
        "blueprint blocks=%s tables=%s (%.1fs)",
        len(blocks_px),
        len(sections),
        time.monotonic() - t0,
    )
    return {
        "ok": bool(sections or texts),
        "tables": sections,
        "table_text": "\n\n".join(texts),
        "blocks": len(blocks_px),
        "pipeline": "belener_blueprint_extract",
    }
