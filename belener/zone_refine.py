"""Уточнение зон spec/legend/explication по контурам таблиц (OpenCV).

Геометрия zones.py — стартовая сетка; на сканах таблицы разного размера —
подменяем/расширяем зоны найденными прямоугольниками с линиями сетки.
"""

from __future__ import annotations

import logging
import re

import fitz

from belener.anchors import (
    has_explication_anchor,
    has_legend_anchor,
    has_specification_anchor,
)
from belener.zones import SheetZones

log = logging.getLogger("belener.zone_refine")

_TABLE_KEYS = (
    "spec_right",
    "spec_left",
    "legend",
    "explication",
    "tables_block",
    "right_column",
    "legend_table",
)


def _iou(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = a & b
    if inter.is_empty:
        return 0.0
    ua = a.get_area() + b.get_area() - inter.get_area()
    return inter.get_area() / max(ua, 1.0)


def _dedupe_rects(rects: list[fitz.Rect], *, iou_thresh: float = 0.55) -> list[fitz.Rect]:
    ordered = sorted(rects, key=lambda r: r.get_area(), reverse=True)
    kept: list[fitz.Rect] = []
    for r in ordered:
        if any(_iou(r, k) >= iou_thresh for k in kept):
            continue
        kept.append(r)
    return kept


def detect_cv_table_rects(
    doc: fitz.Document,
    page_index: int,
    *,
    stamp_rect: fitz.Rect | None = None,
    dpi: int | None = None,
) -> list[fitz.Rect]:
    from belener.config import cv_tables_dpi
    from belener.cv_tables import _find_table_blocks, _page_to_gray, cv_available

    if not cv_available():
        return []
    page = doc[page_index]
    eff_dpi = dpi if dpi is not None else cv_tables_dpi()
    eff_dpi = max(360, min(int(eff_dpi), 560))
    gray, scale = _page_to_gray(doc, page_index, eff_dpi)
    rects = list(_find_table_blocks(gray, page.rect, scale, stamp_rect=stamp_rect))
    try:
        from belener.blueprint_extract import blueprint_available, blueprint_table_rects

        if blueprint_available():
            bp = blueprint_table_rects(gray, scale, page.rect, stamp_rect=stamp_rect)
            if len(bp) > len(rects):
                rects = bp
    except Exception:
        log.debug("blueprint_table_rects skipped", exc_info=True)
    return _dedupe_rects([r & page.rect for r in rects if not r.is_empty])


def _classify_block(
    text: str,
    rect: fitz.Rect,
    page_rect: fitz.Rect,
) -> str:
    t = text or ""
    if has_specification_anchor(t) or re.search(
        r"перечень\s+аппаратур|поз\.?\s+обозначен|продолжен\w*\s+таблиц",
        t,
        re.I,
    ):
        return "spec_right" if (rect.x0 + rect.x1) / 2 > (page_rect.x0 + page_rect.x1) / 2 else "spec_left"
    if has_legend_anchor(t):
        return "legend"
    if has_explication_anchor(t):
        return "explication"
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    mid_x = (page_rect.x0 + page_rect.x1) / 2
    top_third = page_rect.y0 + page_rect.height * 0.38
    if cx >= mid_x - page_rect.width * 0.04:
        if cy < top_third and rect.height > page_rect.height * 0.12:
            return "explication"
        if rect.height >= page_rect.height * 0.10:
            return "spec_right"
        return "legend"
    if rect.height >= page_rect.height * 0.08:
        return "spec_left"
    return "legend_table"


def _merge_into(rects: dict[str, fitz.Rect], key: str, new: fitz.Rect, page_rect: fitz.Rect) -> None:
    new = new & page_rect
    if new.is_empty:
        return
    cur = rects.get(key)
    if cur is None or cur.is_empty:
        rects[key] = new
        return
    if new.get_area() > cur.get_area() * 1.12 or _iou(cur, new) > 0.12:
        rects[key] = cur | new
    elif _iou(cur, new) > 0.35:
        rects[key] = cur | new


def _expand_zone_with_blocks(
    rects: dict[str, fitz.Rect],
    key: str,
    blocks: list[fitz.Rect],
    page_rect: fitz.Rect,
    *,
    min_iou: float = 0.08,
) -> None:
    base = rects.get(key)
    if base is None or base.is_empty:
        return
    matched: list[fitz.Rect] = []
    for b in blocks:
        if _iou(base, b) >= min_iou or (base & b).get_area() > 0:
            if (base & b).get_area() > 0 or _iou(base, b) >= min_iou:
                matched.append(b)
        elif (b.x0 <= base.x1 and b.x1 >= base.x0 and b.y0 <= base.y1 and b.y1 >= base.y0):
            matched.append(b)
    if not matched:
        return
    union = base
    for b in matched:
        union = union | b
    pad_x = page_rect.width * 0.008
    pad_y = page_rect.height * 0.008
    union = fitz.Rect(
        max(page_rect.x0, union.x0 - pad_x),
        max(page_rect.y0, union.y0 - pad_y),
        min(page_rect.x1, union.x1 + pad_x),
        min(page_rect.y1, union.y1 + pad_y),
    )
    if union.get_area() >= base.get_area() * 0.95:
        rects[key] = union & page_rect


def refine_sheet_zones(
    doc: fitz.Document,
    zones: SheetZones,
    page_index: int = 0,
    *,
    classify_with_ocr: bool = True,
) -> SheetZones:
    from belener.config import cv_zone_refine_enabled, table_search_dpi
    from belener.ocr import ocr_region

    if not cv_zone_refine_enabled():
        return zones

    page = doc[page_index]
    page_rect = page.rect
    stamp_rect = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block")
    rects = dict(zones.rects)

    # Даже если CV не нашёл блоки (часто на слабых/сканированных листах),
    # `tables_block`/`right_column` обычно содержит всю таблицу,
    # а `spec_right` — это лишь более узкий под-участок.
    # Чтобы таблица не была "обрезана справа", гарантируем базовое расширение.
    tb = rects.get("tables_block")
    sr = rects.get("spec_right")
    rc = rects.get("right_column")
    if tb is not None and sr is not None and not tb.is_empty and not sr.is_empty:
        if sr.width < tb.width * 0.90:
            rects["spec_right"] = (sr | tb) & page_rect
    if rc is not None and sr is not None and not rc.is_empty and not sr.is_empty:
        if sr.width < rc.width * 0.90:
            rects["spec_right"] = (sr | rc) & page_rect

    blocks = detect_cv_table_rects(doc, page_index, stamp_rect=stamp_rect)
    if not blocks:
        return SheetZones(rects=rects, wide=zones.wide)
    assigned: dict[str, list[fitz.Rect]] = {k: [] for k in _TABLE_KEYS}

    dpi_cls = min(table_search_dpi(), 240)
    for block in blocks:
        sample = ""
        if classify_with_ocr:
            try:
                sample = (ocr_region(doc, page_index, block, dpi=dpi_cls, zone="tables_block", psm=6) or "")[
                    :800
                ]
            except Exception:
                sample = ""
        key = _classify_block(sample, block, page_rect)
        assigned.setdefault(key, []).append(block)

    for key, blist in assigned.items():
        if not blist:
            continue
        merged = blist[0]
        for b in blist[1:]:
            if _iou(merged, b) > 0.2 or abs(merged.x0 - b.x0) < page_rect.width * 0.08:
                merged = merged | b
        _merge_into(rects, key, merged, page_rect)

    for key in ("spec_right", "spec_left", "legend", "explication", "tables_block", "right_column"):
        _expand_zone_with_blocks(rects, key, blocks, page_rect)

    # Гарантировать, что справа захватывается "вся таблица" на случай,
    # когда классификация в assigned ошиблась (или при run_ocr=False sample пустой).
    mid_x = page_rect.x0 + page_rect.width * 0.5
    right_blocks = [b for b in blocks if (b.x0 + b.x1) / 2 >= mid_x]
    left_blocks = [b for b in blocks if (b.x0 + b.x1) / 2 < mid_x]

    if right_blocks:
        union = right_blocks[0]
        for b in right_blocks[1:]:
            union = union | b
        cur = rects.get("spec_right")
        rects["spec_right"] = (cur | union) & page_rect if cur is not None else union & page_rect
        rc = rects.get("right_column")
        if rc is not None:
            rects["right_column"] = (rc | union) & page_rect

    if left_blocks:
        union = left_blocks[0]
        for b in left_blocks[1:]:
            union = union | b
        cur = rects.get("spec_left")
        rects["spec_left"] = (cur | union) & page_rect if cur is not None else union & page_rect

    # На практике `spec_right` часто является подмножиной `right_column`.
    # Чтобы таблица не была обрезана при обучающих/диагностических кропах,
    # расширяем её до более широкой правой зоны.
    rc = rects.get("right_column")
    sr = rects.get("spec_right")
    if rc is not None and sr is not None and not rc.is_empty and not sr.is_empty:
        rects["spec_right"] = (sr | rc) & page_rect
    if rc is not None and (sr is None or sr.is_empty):
        rects["spec_right"] = rc & page_rect

    tb = rects.get("tables_block")
    if tb is not None and rects.get("spec_right") is not None and not tb.is_empty:
        rects["spec_right"] = (rects["spec_right"] | tb) & page_rect

    log.info(
        "zone_refine CV blocks=%s spec_right=%s spec_left=%s",
        len(blocks),
        bool(rects.get("spec_right")),
        bool(rects.get("spec_left")),
    )
    return SheetZones(rects=rects, wide=zones.wide)
