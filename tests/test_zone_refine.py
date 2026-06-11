from __future__ import annotations

import fitz

from belener.zone_refine import _classify_block, _dedupe_rects, _iou
from belener.zones import build_zones


def test_iou_overlap():
    a = fitz.Rect(0, 0, 100, 100)
    b = fitz.Rect(50, 50, 150, 150)
    assert _iou(a, b) > 0.1


def test_dedupe_keeps_larger():
    a = fitz.Rect(0, 0, 200, 200)
    b = fitz.Rect(10, 10, 190, 190)
    out = _dedupe_rects([a, b])
    assert len(out) == 1


def test_classify_spec_anchor():
    page = fitz.Rect(0, 0, 1000, 700)
    rect = fitz.Rect(600, 100, 980, 500)
    key = _classify_block("Перечень аппаратуры\nПоз. Обозначение", rect, page)
    assert key == "spec_right"


def test_build_zones_wide_has_spec():
    z = build_zones(fitz.Rect(0, 0, 1200, 600))
    assert z.wide
    assert "spec_right" in z.rects
