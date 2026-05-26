"""Поиск зон таблиц и штампа по якорям OCR (универсально для разных листов)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz

from belener.anchors import (
    has_explication_anchor,
    has_legend_anchor,
    has_specification_anchor,
    stamp_score,
)
from belener.parse import parse_specification
from belener.config import discover_zones_fast, stamp_frac, table_search_dpi
from belener.ocr import ocr_region
from belener.parse import parse_explication, parse_legend
from belener.zones import SheetZones, build_zones


@dataclass
class _Cand:
    name: str
    rect: fitz.Rect
    text: str = ""
    expl: int = 0
    leg: int = 0
    spec: int = 0
    stamp: int = 0


def _clip(rect: fitz.Rect, page: fitz.Rect) -> fitz.Rect:
    return fitz.Rect(
        max(page.x0, rect.x0),
        max(page.y0, rect.y0),
        min(page.x1, rect.x1),
        min(page.y1, rect.y1),
    )


def _table_candidates(page: fitz.Rect) -> list[tuple[str, fitz.Rect]]:
    r = page
    sf = stamp_frac()
    stamp_y = r.y1 - r.height * sf
    margin = r.height * 0.03
    y_top = r.y0 + margin
    y_above_stamp = max(y_top + r.height * 0.12, stamp_y - margin)

    rw = r.width * 0.46
    rh = max(r.height * 0.35, y_above_stamp - y_top)
    mid_x = r.x0 + r.width * 0.5

    cands: list[tuple[str, fitz.Rect]] = [
        ("right_column", fitz.Rect(r.x1 - rw, y_top, r.x1, y_above_stamp)),
        ("right_narrow", fitz.Rect(r.x1 - r.width * 0.38, y_top, r.x1, y_above_stamp)),
        ("right_wide", fitz.Rect(r.x1 - r.width * 0.55, y_top, r.x1, y_above_stamp)),
        ("left_column", fitz.Rect(r.x0, y_top, r.x0 + rw, y_above_stamp)),
        ("center_block", fitz.Rect(mid_x - rw * 0.55, y_top, mid_x + rw * 0.55, y_above_stamp)),
        (
            "bottom_above_stamp",
            fitz.Rect(r.x0 + r.width * 0.25, stamp_y - rh * 0.85, r.x1, stamp_y),
        ),
        ("upper_right", fitz.Rect(r.x1 - rw, y_top, r.x1, y_top + rh)),
    ]
    out: list[tuple[str, fitz.Rect]] = []
    for name, rect in cands:
        cr = _clip(rect, r)
        if cr.width >= r.width * 0.15 and cr.height >= r.height * 0.12:
            out.append((name, cr))
    return out


def _stamp_candidates(page: fitz.Rect) -> list[tuple[str, fitz.Rect]]:
    r = page
    sf = stamp_frac()
    placements: list[tuple[str, fitz.Rect]] = []
    for bw_frac, bh_frac in (
        (0.52, 0.40),
        (0.45, 0.35),
        (0.58, 0.45),
        (0.40, 0.32),
    ):
        bw = r.width * bw_frac
        bh = r.height * bh_frac
        placements.append(("stamp_br", fitz.Rect(r.x1 - bw, r.y1 - bh, r.x1, r.y1)))
        placements.append(("stamp_bl", fitz.Rect(r.x0, r.y1 - bh, r.x0 + bw, r.y1)))
    fh = min(r.height * sf, r.height * 0.48)
    placements.append(("stamp_bottom", fitz.Rect(r.x0, r.y1 - fh, r.x1, r.y1)))
    out: list[tuple[str, fitz.Rect]] = []
    seen: set[str] = set()
    for name, rect in placements:
        cr = _clip(rect, r)
        key = f"{int(cr.x0)}:{int(cr.y0)}:{int(cr.x1)}:{int(cr.y1)}"
        if key in seen:
            continue
        seen.add(key)
        if cr.width >= r.width * 0.2 and cr.height >= r.height * 0.12:
            out.append((name, cr))
    return out


def _spec_score(text: str) -> int:
    t = text or ""
    score = 0
    if re.search(r"перечень\s+аппаратур", t, re.I):
        score += 5
    if re.search(r"продолжен\w*\s+таблиц", t, re.I):
        score += 4
    if has_specification_anchor(t):
        score += 2
    if len(parse_specification(t)) >= 2:
        score += 3
    return score


def _score_candidate(text: str) -> tuple[int, int, int, int]:
    expl = (2 if has_explication_anchor(text) else 0) + len(parse_explication(text))
    leg = (2 if has_legend_anchor(text) else 0) + len(parse_legend(text))
    spec = _spec_score(text)
    st = stamp_score(text)
    return expl, leg, spec, st


def discover_sheet_zones(
    doc: fitz.Document,
    page_index: int,
    page_rect: fitz.Rect,
    *,
    fast: bool = False,
) -> SheetZones:
    """Геометрия по умолчанию + опциональное уточнение зон по якорям OCR."""
    base = build_zones(page_rect)
    dpi = table_search_dpi()
    use_fast = fast or discover_zones_fast()

    table_cands: list[_Cand] = []
    zone_jobs: list[tuple[str, fitz.Rect]] = []
    if use_fast and base.wide:
        for key in ("spec_right", "spec_left", "legend_table"):
            rect = base.rects.get(key)
            if rect is not None:
                zone_jobs.append((key, rect))
    elif use_fast:
        rect = base.rects.get("right_column")
        if rect is not None:
            zone_jobs.append(("right_column", rect))
    else:
        zone_jobs = _table_candidates(page_rect)

    for name, rect in zone_jobs:
        text = (ocr_region(doc, page_index, rect, dpi=dpi, zone=name) or "").strip()
        expl, leg, spec, _ = _score_candidate(text)
        if (
            expl
            or leg
            or spec
            or has_explication_anchor(text)
            or has_legend_anchor(text)
            or has_specification_anchor(text)
        ):
            table_cands.append(_Cand(name, rect, text, expl, leg, spec))

    stamp_cands: list[_Cand] = []
    if use_fast:
        rect = base.rects.get("stamp_frame") or base.rects.get("stamp_block")
        if rect is not None:
            text = (ocr_region(doc, page_index, rect, dpi=dpi, zone="stamp_frame") or "").strip()
            expl, leg, _, st = _score_candidate(text)
            if st > 0:
                stamp_cands.append(_Cand("stamp_frame", rect, text, expl, leg, 0, st))
    else:
        for name, rect in _stamp_candidates(page_rect):
            text = (ocr_region(doc, page_index, rect, dpi=dpi, zone="stamp_frame") or "").strip()
            expl, leg, _, st = _score_candidate(text)
            if st > 0:
                stamp_cands.append(_Cand(name, rect, text, expl, leg, 0, st))

    rects = dict(base.rects)

    if table_cands:
        spec_cands = [c for c in table_cands if c.spec >= 2]
        for c in spec_cands:
            if c.name in ("upper_right", "right_column", "right_wide", "right_narrow", "spec_right"):
                cur = rects.get("spec_right")
                rects["spec_right"] = (cur | c.rect) if cur else c.rect
            if c.name in ("left_column", "center_block", "spec_left"):
                cur = rects.get("spec_left")
                rects["spec_left"] = (cur | c.rect) if cur else c.rect
            if c.name in ("left_column", "bottom_above_stamp", "legend_table") and (
                c.leg >= 1 or has_legend_anchor(c.text)
            ):
                cur = rects.get("legend_table")
                rects["legend_table"] = (cur | c.rect) if cur else c.rect

        combined = max(table_cands, key=lambda c: c.expl + c.leg + c.spec)
        if combined.expl + combined.leg >= 2 and not base.rects.get("spec_right"):
            rects["tables_block"] = combined.rect
            rects["right_column"] = combined.rect

        expl_best = max(table_cands, key=lambda c: c.expl)
        if expl_best.expl >= 1:
            rects["explication"] = expl_best.rect

        leg_best = max(table_cands, key=lambda c: c.leg)
        if leg_best.leg >= 1:
            rects["legend"] = leg_best.rect
            if base.rects.get("legend_table"):
                rects["legend_table"] = leg_best.rect

    if stamp_cands:
        if base.wide:
            stamp_cands = [c for c in stamp_cands if c.rect.x0 >= page_rect.x0 + page_rect.width * 0.42]
        best = max(stamp_cands, key=lambda c: (c.stamp, c.rect.width * c.rect.height))
        default_sf = base.rects.get("stamp_frame")
        if best.stamp >= 3 and default_sf is not None:
            if best.rect.width * best.rect.height >= default_sf.width * default_sf.height * 0.75:
                rects["stamp_frame"] = best.rect
                rects["stamp_block"] = best.rect

    # Не сужать штамп относительно геометрии по умолчанию
    default_sf = base.rects.get("stamp_frame")
    cur_sf = rects.get("stamp_frame")
    if default_sf and cur_sf and cur_sf.width * cur_sf.height < default_sf.width * default_sf.height * 0.8:
        rects["stamp_frame"] = default_sf
        rects["stamp_block"] = default_sf
    elif default_sf and "stamp_frame" not in rects:
        rects["stamp_frame"] = default_sf
        rects["stamp_block"] = default_sf

    return SheetZones(rects=rects, wide=base.wide)
