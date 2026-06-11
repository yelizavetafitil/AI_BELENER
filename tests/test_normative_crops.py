import fitz

from belener.normative_crops import (
    TILE_COLS,
    TILE_ROWS,
    _finalize_refs,
    page_tile_jobs,
)
from belener.normative_refs import extract_normative_refs


def test_page_tile_jobs_cover_full_sheet():
    doc = fitz.open()
    doc.new_page(width=1200, height=800)
    jobs = page_tile_jobs(doc[0].rect)
    assert len(jobs) == TILE_COLS * TILE_ROWS
    names = {k for k, _ in jobs}
    assert names == {f"tile_{r}_{c}" for r in range(TILE_ROWS) for c in range(TILE_COLS)}
    doc.close()


def test_tile_overlap_expands_interior():
    doc = fitz.open()
    doc.new_page(width=900, height=600)
    r = doc[0].rect
    plain = page_tile_jobs(r, overlap_frac=0.0)[1][1]
    overl = page_tile_jobs(r, overlap_frac=0.12)[1][1]
    assert overl.width > plain.width
    assert overl.height > plain.height
    doc.close()


def test_extract_stacked_double_gost():
    text = "76х3,0 ГОСТ 10704-91\n---\nВ-Ст3пс ГОСТ 10705-80"
    refs = extract_normative_refs(text)
    nums = {r["ref"] for r in refs if r["kind"] == "ГОСТ"}
    assert any("10704-91" in r for r in nums)
    assert any("10705-80" in r for r in nums)


def test_extract_double_gost_same_line():
    text = "76х3,0 ГОСТ 10704-91 В-Ст3пс ГОСТ 10705-80"
    refs = extract_normative_refs(text)
    assert len([r for r in refs if r["kind"] == "ГОСТ"]) >= 2


def test_finalize_refs_no_duplicates():
    a = "ОСТ 34 10 700-97 таблица"
    b = "ОСТ 34.10.700-97 ещё раз"
    refs = _finalize_refs([a, b])
    ost700 = [r for r in refs if "700-97" in r["ref"].replace(" ", "")]
    assert len(ost700) == 1


def test_finalize_refs_dedupes_years():
    table = "ГОСТ 11371-78 Шайба"
    ocr = "ГОСТ 11371-78 ГОСТ 11371-71"
    refs = _finalize_refs([table, ocr])
    gost = [r["ref"] for r in refs if r["kind"] == "ГОСТ"]
    assert any("11371-78" in r for r in gost)
    assert not any("11371-71" in r for r in gost)


def test_extract_from_tile_like_text():
    table = (
        "ГОСТ 10704-91\n"
        "В-Ст3пс ГОСТ 10705-80\n"
        "ГОСТ 33259-2015 ГОСТ 11371-78\n"
        "10 (ТУ 6-21-51-90).\n"
        "ГОСТ 9.402-2004 ГОСТ 7313-75"
    )
    refs = extract_normative_refs(table)
    assert any("10705-80" in r["ref"] for r in refs)
    assert any(r["kind"] == "ТУ" for r in refs)
    assert any("9.402-2004" in r["ref"] for r in refs)
