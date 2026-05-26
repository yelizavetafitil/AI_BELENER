"""Геометрические зоны листа САПР/чертежа."""

from __future__ import annotations

from dataclasses import dataclass

import fitz

from belener.config import (
    drawing_aspect_min,
    expl_split_frac,
    notes_column_frac,
    right_column_frac,
    stamp_block_height_frac,
    stamp_block_width_frac,
    stamp_frac,
)


@dataclass
class SheetZones:
    rects: dict[str, fitz.Rect]
    wide: bool


def build_zones(page_rect: fitz.Rect) -> SheetZones:
    r = page_rect
    sf = stamp_frac()
    rects: dict[str, fitz.Rect] = {}

    fh = min(r.height * sf, r.height * 0.48)
    rects["stamp"] = fitz.Rect(r.x0, r.y1 - fh, r.x1, r.y1)
    fh_t = min(r.height * 0.22, r.height * 0.28)
    rects["stamp_tight"] = fitz.Rect(r.x0, r.y1 - fh_t, r.x1, r.y1)

    wide = (r.width / max(r.height, 1.0)) >= drawing_aspect_min()
    bw = min(r.width * stamp_block_width_frac(), r.width * 0.42)
    bh = min(r.height * stamp_block_height_frac(), r.height * 0.28)
    if wide:
        rects["stamp_frame"] = fitz.Rect(r.x1 - bw, r.y1 - bh, r.x1, r.y1)
        rects["stamp_block"] = rects["stamp_frame"]
        rw = r.width * right_column_frac()
        y0 = r.y0 + r.height * 0.04
        y1 = r.y1 - r.height * sf
        if y1 - y0 >= r.height * 0.15:
            split_y = y0 + (y1 - y0) * expl_split_frac()
            x0 = r.x1 - rw
            rects["right_column"] = fitz.Rect(x0, y0, r.x1, y1)
            rects["explication"] = fitz.Rect(x0, y0, r.x1, split_y)
            rects["legend"] = fitz.Rect(x0, split_y, r.x1, y1)
            x_mid = r.x0 + r.width * 0.34
            if x0 - x_mid >= r.width * 0.14:
                rects["tables_block"] = fitz.Rect(x_mid, y0, r.x1, y1)
            y_spec_left = y0 + (y1 - y0) * 0.28
            y_spec_right = y0 + (y1 - y0) * 0.36
            x_left = r.x0 + r.width * 0.36
            x_right = r.x0 + r.width * 0.50
            rects["spec_left"] = fitz.Rect(r.x0, y0, x_left, y_spec_left)
            rects["spec_right"] = fitz.Rect(x_right, y0, r.x1, y_spec_right)
            y_leg = y_spec_left + (y1 - y_spec_left) * 0.08
            y_leg_end = y0 + (y1 - y0) * 0.52
            rects["legend_table"] = fitz.Rect(r.x0, y_leg, x_left, min(y_leg_end, y1 - bh * 0.35))
            body_y1 = r.y1 - bh
            if body_y1 - r.y0 >= r.height * 0.2 and (x0 - r.x0) >= r.width * 0.25:
                rects["body"] = fitz.Rect(r.x0, r.y0, x0, body_y1)
            nw = r.width * notes_column_frac()
            notes_x1 = x0
            notes_x0 = max(r.x0 + r.width * 0.32, notes_x1 - nw)
            if notes_x1 - notes_x0 >= r.width * 0.16 and body_y1 - y0 >= r.height * 0.2:
                rects["sheet_notes"] = fitz.Rect(notes_x0, y0, notes_x1, body_y1)
    else:
        # Портрет / А4: штамп снизу, таблицы часто справа (как на альбомных листах).
        bh = min(bh, r.height * 0.34)
        bw_p = r.width * min(0.55, stamp_block_width_frac() + 0.04)
        rects["stamp_frame"] = fitz.Rect(r.x1 - bw_p, r.y1 - bh, r.x1, r.y1)
        rects["stamp_block"] = rects["stamp_frame"]
        y0 = r.y0 + r.height * 0.035
        y1 = r.y1 - bh
        rw = r.width * right_column_frac()
        x0 = r.x1 - rw
        if y1 - y0 >= r.height * 0.18 and x0 - r.x0 >= r.width * 0.22:
            split_y = y0 + (y1 - y0) * expl_split_frac()
            rects["right_column"] = fitz.Rect(x0, y0, r.x1, y1)
            rects["explication"] = fitz.Rect(x0, y0, r.x1, split_y)
            rects["legend"] = fitz.Rect(x0, split_y, r.x1, y1)
            nw = r.width * notes_column_frac()
            notes_x0 = max(r.x0 + r.width * 0.28, x0 - nw)
            if x0 - notes_x0 >= r.width * 0.14:
                rects["sheet_notes"] = fitz.Rect(notes_x0, y0, x0, y1)
            if x0 - r.x0 >= r.width * 0.25:
                rects["body"] = fitz.Rect(r.x0, y0, x0, y1)
        elif y1 - y0 >= r.height * 0.25:
            rects["body"] = fitz.Rect(r.x0, y0, r.x1, y1)

    return SheetZones(rects=rects, wide=wide)


def stamp_ocr_rect(zones: SheetZones, page_rect: fitz.Rect) -> fitz.Rect:
    """Нижняя полоса основной надписи (без захвата таблиц справа над штампом)."""
    r = page_rect
    base = zones.rects.get("stamp_tight") or zones.rects.get("stamp")
    if base is None or base.is_empty:
        base = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block") or zones.rects.get("stamp")
    if not base or base.is_empty:
        sf = stamp_frac()
        fh = min(r.height * sf, r.height * 0.48)
        base = fitz.Rect(r.x0, r.y1 - fh, r.x1, r.y1)
    pad_x = r.width * 0.025
    pad_y = r.height * 0.055
    out = fitz.Rect(
        max(r.x0, base.x0 - pad_x),
        max(r.y0, base.y0 - pad_y),
        min(r.x1, base.x1),
        min(r.y1, base.y1),
    )
    return out & r
