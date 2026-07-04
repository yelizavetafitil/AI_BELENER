"""Tile OCR: sequential processing."""

from unittest.mock import patch

import fitz

from belener.tile_ocr import extract_page_tiles, page_tile_jobs, page_tile_jobs_normative


def test_extract_page_tiles_all_jobs():
    doc = fitz.open()
    doc.new_page(width=1200, height=800)
    calls: list[str] = []

    def fake_ocr(doc, page_index, rect, *, zone, dpi, deadline, tile_max_sec, **kwargs):
        calls.append(zone)
        return f"GOST 10704-91 {zone}"

    with patch("belener.tile_ocr.ocr_tile", side_effect=fake_ocr):
        sources, done, expected = extract_page_tiles(
            doc, 0, dpi=320, deadline=__import__("time").monotonic() + 120,
            tile_max_sec=20, overlap_frac=0.12,
        )
    doc.close()
    assert expected == 8
    assert done == 8
    assert len(sources) == 8


def test_normative_tile_order_bottom_right_first():
    doc = fitz.open()
    doc.new_page(width=2384, height=842)
    jobs = page_tile_jobs_normative(doc[0].rect)
    names = [name for name, _ in jobs]
    assert names[0] == "tile_1_3"
    assert names[1] == "tile_0_3"
    assert names[-1] == "tile_0_0"
    doc.close()


def test_supplement_runs_after_right_tiles_on_wide_page():
    doc = fitz.open()
    doc.new_page(width=2384, height=842)
    order: list[str] = []

    def fake_ocr(doc, page_index, rect, *, zone, dpi, deadline, tile_max_sec, **kwargs):
        order.append(zone)
        return f"text {zone}"

    with patch("belener.tile_ocr.ocr_tile", side_effect=fake_ocr):
        sources, done, expected = extract_page_tiles(
            doc, 0, dpi=320, deadline=__import__("time").monotonic() + 120,
            tile_max_sec=20, overlap_frac=0.12,
        )
    doc.close()
    assert expected == 9
    assert done == 9
    assert order[0] == "tile_1_3"
    assert order[4] == "spec_right"
    assert order[-1] == "tile_0_0"
