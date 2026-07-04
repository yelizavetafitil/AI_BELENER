"""Многостраничные PDF: бюджет, сетка тайлов, отчёт."""

from belener.config import (
    gost_check_total_budget_sec,
    normative_ocr_budget_sec,
    tile_grid_for_page_count,
)
from belener.normative_extract import normative_refs_to_markdown
from belener.normative_refs import extract_normative_refs, merge_normative_refs_from_sources


def test_budget_scales_with_page_count():
    assert gost_check_total_budget_sec(1) < gost_check_total_budget_sec(12)
    assert normative_ocr_budget_sec(12) > normative_ocr_budget_sec(1)


def test_tile_grid_shrinks_for_many_pages():
    assert tile_grid_for_page_count(1) == (4, 2)
    assert tile_grid_for_page_count(3) == (3, 2)
    assert tile_grid_for_page_count(12) == (2, 2)


def test_markdown_shows_page_progress():
    md = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": "ГОСТ 481-80"}],
        filename="multi.pdf",
        page_count=12,
        pages_processed=8,
        budget_exhausted=True,
        check_date=None,
    )
    assert "Листов в файле:** 12" in md
    assert "обработано:** 8" in md
    assert "не все листы" in md


def test_material_prefix_not_preferred_in_merge():
    out = merge_normative_refs_from_sources(
        "20-В ГОСТ 2590-2006",
        "ГОСТ 2590-2006",
        "ГОСТ 2590-2006",
    )
    gost = [r for r in out if r["kind"] == "ГОСТ" and "2590" in r["ref"]]
    assert gost
    assert gost[0]["ref"].startswith("ГОСТ")
