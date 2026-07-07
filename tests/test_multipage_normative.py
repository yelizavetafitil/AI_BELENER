"""Многостраничные PDF: бюджет, сетка тайлов, отчёт."""

from belener.config import (
    gost_check_budget_human,
    gost_check_total_budget_sec,
    normative_ocr_budget_sec,
    tile_grid_for_page_count,
)
from belener.normative_extract import normative_refs_to_markdown
from belener.normative_refs import extract_normative_refs, merge_normative_refs_from_sources


def test_budget_scales_with_page_count():
    one = gost_check_total_budget_sec(1)
    twelve = gost_check_total_budget_sec(12)
    hundred = gost_check_total_budget_sec(100)
    assert one >= 220.0
    assert twelve > one
    assert twelve == one + 11 * 50.0
    assert hundred > twelve
    assert hundred <= 2400.0
    assert normative_ocr_budget_sec(12) >= normative_ocr_budget_sec(1)
    assert "мин" in gost_check_budget_human(12)


def test_tile_grid_shrinks_for_many_pages():
    assert tile_grid_for_page_count(1) == (4, 2)
    assert tile_grid_for_page_count(3) == (3, 2)
    assert tile_grid_for_page_count(12) == (2, 2)
    assert tile_grid_for_page_count(50) == (2, 1)
    assert tile_grid_for_page_count(100) == (1, 2)


def test_multipage_preview_generates_all_pages():
    import fitz

    from belener.normative_extract import generate_pdf_preview_pages_with_highlights

    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page(width=600, height=400)
        page.insert_text((72, 100), "ГОСТ 10704-91")
    tmp = doc.tobytes()
    doc.close()

    import os
    import tempfile

    path = os.path.join(tempfile.gettempdir(), "belener_preview_test.pdf")
    with open(path, "wb") as f:
        f.write(tmp)

    refs = [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}]
    pages = generate_pdf_preview_pages_with_highlights(path, refs)
    try:
        os.unlink(path)
    except OSError:
        pass
    assert len(pages) == 3
    assert all(p.get("url") for p in pages)
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
