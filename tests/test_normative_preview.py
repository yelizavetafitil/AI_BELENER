import fitz

from belener.normative_extract import (
    _all_word_spans_for_ref,
    _find_pinpoint_rects,
    _highlight_on_page,
    _pinpoint_rects_for_span,
)


def test_pinpoint_rects_are_word_sized_not_line_wide():
    words = [
        (72.0, 88.0, 84.0, 103.0, "ГОСТ", 0, 0, 0),
        (87.0, 88.0, 146.0, 103.0, "33259-2015", 0, 0, 1),
        (72.0, 120.0, 400.0, 135.0, "длинный", 0, 0, 2),
        (410.0, 120.0, 500.0, 135.0, "текст", 0, 0, 3),
    ]

    rects = _find_pinpoint_rects(words, "ГОСТ 33259-2015")
    assert rects, "expected pinpoint rects"
    assert len(rects) == 2
    for r in rects:
        assert r.width < 120, f"rect too wide for a single token: {r.width}"
    assert sum(r.width for r in rects) < 200


def test_preview_uses_rect_annot_not_line_highlight():
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    words = [
        (72.0, 100.0, 110.0, 115.0, "ГОСТ", 0, 0, 0),
        (115.0, 100.0, 180.0, 115.0, "10704-91", 0, 0, 1),
    ]
    refs = [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}]
    hits, marks = _highlight_on_page(page, refs, words=words)
    assert hits == 1
    assert marks == 2
    kinds = {a.type[0] for a in page.annots() or []}
    assert kinds == {fitz.PDF_ANNOT_SQUARE}
    doc.close()


def test_gost_span_excludes_description():
    words = [
        (60.0, 200.0, 85.0, 215.0, "ГОСТ", 0, 0, 0),
        (90.0, 200.0, 150.0, 215.0, "17375-2001", 0, 0, 1),
        (155.0, 200.0, 220.0, 215.0, "Отвод", 0, 0, 2),
        (225.0, 200.0, 300.0, 215.0, "90°", 0, 0, 3),
    ]
    rects = _find_pinpoint_rects(words, "ГОСТ 17375-2001")
    assert rects
    assert all(r.x1 <= 155 for r in rects)


def test_ost_row_skips_position_number():
    words = [
        (40.0, 200.0, 55.0, 215.0, "10", 0, 0, 0),
        (60.0, 200.0, 85.0, 215.0, "ОСТ", 0, 0, 1),
        (90.0, 200.0, 170.0, 215.0, "34-10-615-93", 0, 0, 2),
    ]
    spans = _all_word_spans_for_ref(words, "ОСТ 34-10-615-93")
    assert spans == [(1, 2)]
    rects = _pinpoint_rects_for_span(words, spans[0][0], spans[0][1])
    assert len(rects) == 2
    assert all(r.x0 >= 60 for r in rects)


def test_gost_with_dot_in_number():
    words = [
        (72.0, 300.0, 110.0, 315.0, "ГОСТ", 0, 0, 0),
        (115.0, 300.0, 175.0, 315.0, "9.602-2016", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "ГОСТ 9.602-2016")
    assert len(rects) == 2


def test_multiple_occurrences_of_same_ref():
    words = [
        (72.0, 100.0, 110.0, 115.0, "ГОСТ", 0, 0, 0),
        (115.0, 100.0, 180.0, 115.0, "10704-91", 0, 0, 1),
        (72.0, 140.0, 110.0, 155.0, "ГОСТ", 0, 0, 2),
        (115.0, 140.0, 180.0, 155.0, "10704-91", 0, 0, 3),
    ]
    spans = _all_word_spans_for_ref(words, "ГОСТ 10704-91")
    assert len(spans) == 2
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    _, marks = _highlight_on_page(page, [{"ref": "ГОСТ 10704-91"}], words=words)
    assert marks == 4
    doc.close()


def test_ost_with_position_prefix_and_latin_oct():
    words = [
        (40.0, 200.0, 55.0, 215.0, "19", 0, 0, 0),
        (60.0, 200.0, 85.0, 215.0, "OCT", 0, 0, 1),
        (90.0, 200.0, 170.0, 215.0, "108.275.52-80", 0, 0, 2),
    ]
    spans = _all_word_spans_for_ref(words, "ОСТ 108.275.52-80")
    assert spans == [(1, 2)]
    rects = _pinpoint_rects_for_span(words, spans[0][0], spans[0][1])
    assert len(rects) == 2
    assert all(r.x0 >= 60 for r in rects)


def test_gost_with_material_prefix():
    words = [
        (50.0, 220.0, 90.0, 235.0, "12-В", 0, 0, 0),
        (95.0, 220.0, 130.0, 235.0, "ГОСТ", 0, 0, 1),
        (135.0, 220.0, 195.0, 235.0, "2590-2006", 0, 0, 2),
    ]
    spans = _all_word_spans_for_ref(words, "ГОСТ 2590-2006")
    assert spans == [(1, 2)]


def test_gost_span_excludes_length_and_quantity_tokens():
    words = [
        (60.0, 200.0, 85.0, 215.0, "ГОСТ", 0, 0, 0),
        (90.0, 200.0, 150.0, 215.0, "1050-88", 0, 0, 1),
        (155.0, 200.0, 200.0, 215.0, "L-1612", 0, 0, 2),
        (400.0, 200.0, 410.0, 215.0, "1", 0, 0, 3),
    ]
    spans = _all_word_spans_for_ref(words, "ГОСТ 1050-88")
    assert spans == [(0, 1)]
    rects = _pinpoint_rects_for_span(words, spans[0][0], spans[0][1])
    assert len(rects) == 2
    assert all(r.x1 <= 155 for r in rects)


def test_fraction_cell_highlights_each_gost_on_its_line():
    """Дробь в ячейке: числитель и знаменатель — отдельные spans."""
    words = [
        (80.0, 100.0, 120.0, 112.0, "4x40", 0, 0, 0),
        (125.0, 100.0, 150.0, 112.0, "ГОСТ", 0, 0, 1),
        (155.0, 100.0, 210.0, 112.0, "103-2006", 0, 0, 2),
        (80.0, 118.0, 110.0, 130.0, "С235", 0, 0, 3),
        (115.0, 118.0, 140.0, 130.0, "ГОСТ", 0, 0, 4),
        (145.0, 118.0, 210.0, 130.0, "27772-2015", 0, 0, 5),
    ]
    spans103 = _all_word_spans_for_ref(words, "ГОСТ 103-2006")
    spans277 = _all_word_spans_for_ref(words, "ГОСТ 27772-2015")
    assert spans103 == [(1, 2)]
    assert spans277 == [(4, 5)]
    r103 = _pinpoint_rects_for_span(words, 1, 2)
    r277 = _pinpoint_rects_for_span(words, 4, 5)
    assert all(r.y1 <= 115 for r in r103)
    assert all(r.y0 >= 115 for r in r277)


def test_repeated_ost_rows_all_highlighted():
    """Повторяющиеся ОСТ в соседних строках — каждая строка отдельно."""
    words = [
        (40.0, 100.0, 55.0, 115.0, "02", 0, 0, 0),
        (60.0, 100.0, 85.0, 115.0, "OCT", 0, 0, 1),
        (90.0, 100.0, 170.0, 115.0, "108.632.02-80", 0, 0, 2),
        (40.0, 115.0, 55.0, 130.0, "02", 0, 0, 3),
        (60.0, 115.0, 85.0, 130.0, "OCT", 0, 0, 4),
        (90.0, 115.0, 170.0, 130.0, "108.643.01-80", 0, 0, 5),
        (40.0, 130.0, 55.0, 145.0, "02", 0, 0, 6),
        (60.0, 130.0, 85.0, 145.0, "OCT", 0, 0, 7),
        (90.0, 130.0, 170.0, 145.0, "108.632.01-80", 0, 0, 8),
    ]
    refs = [
        {"ref": "ОСТ 108.632.02-80"},
        {"ref": "ОСТ 108.643.01-80"},
        {"ref": "ОСТ 108.632.01-80"},
    ]
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    hits, marks = _highlight_on_page(page, refs, words=words)
    assert hits == 3
    assert marks == 6
    doc.close()


def test_ocr_foct_gost_highlight():
    words = [
        (60.0, 200.0, 90.0, 215.0, "FOCT", 0, 0, 0),
        (95.0, 200.0, 160.0, 215.0, "27772-2015", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "ГОСТ 27772-2015")
    assert len(rects) == 2


def test_ocr_tkn_tkp_highlight():
    words = [
        (60.0, 200.0, 85.0, 215.0, "TKN", 0, 0, 0),
        (90.0, 200.0, 200.0, 215.0, "45-2.01-111-2008", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "ТКП 45-2.01-111-2008")
    assert len(rects) == 2


def test_ocr_ctb_stb_highlight():
    words = [
        (60.0, 200.0, 85.0, 215.0, "CTB", 0, 0, 0),
        (90.0, 200.0, 160.0, 215.0, "2073-2010", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "СТБ 2073-2010")
    assert len(rects) == 2


def test_stp_rd_year_highlight():
    words = [
        (60.0, 200.0, 95.0, 215.0, "(СТП", 0, 0, 0),
        (100.0, 200.0, 220.0, 215.0, "33240.49.101-2018)", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "СТП 33240.49.101-2018")
    assert len(rects) >= 2


def test_repeated_gost_rows_both_highlighted():
    words = [
        (80.0, 100.0, 120.0, 112.0, "ГОСТ", 0, 0, 0),
        (125.0, 100.0, 180.0, 112.0, "103-2006", 0, 0, 1),
        (80.0, 118.0, 120.0, 130.0, "ГОСТ", 0, 0, 2),
        (125.0, 118.0, 180.0, 130.0, "103-2006", 0, 0, 3),
    ]
    spans = _all_word_spans_for_ref(words, "ГОСТ 103-2006")
    assert len(spans) == 2
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    _, marks = _highlight_on_page(page, [{"ref": "ГОСТ 103-2006"}], words=words)
    assert marks == 4
    doc.close()


def test_rd_ref_highlight_target_and_words():
    from belener.normative_refs import _ref_highlight_target

    kind, canon, _ = _ref_highlight_target("РД 34.03.304-87")
    assert kind == "РД"
    assert canon
    words = [
        (60.0, 200.0, 85.0, 215.0, "(RD", 0, 0, 0),
        (90.0, 200.0, 200.0, 215.0, "34.03.304-87)", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "РД 34.03.304-87")
    assert len(rects) >= 2


def test_all_ost_designation_rows_highlighted():
    words = [
        (40.0, 100.0, 55.0, 115.0, "19", 0, 0, 0),
        (60.0, 100.0, 85.0, 115.0, "OCT", 0, 0, 1),
        (90.0, 100.0, 170.0, 115.0, "108.275.52-80", 0, 0, 2),
        (40.0, 115.0, 55.0, 130.0, "02", 0, 0, 3),
        (60.0, 115.0, 85.0, 130.0, "OCT", 0, 0, 4),
        (90.0, 115.0, 170.0, 130.0, "108.632.02-80", 0, 0, 5),
        (40.0, 130.0, 55.0, 145.0, "02", 0, 0, 6),
        (60.0, 130.0, 85.0, 145.0, "OCT", 0, 0, 7),
        (90.0, 130.0, 170.0, 145.0, "108.643.01-80", 0, 0, 8),
    ]
    refs = [
        {"ref": "ОСТ 108.275.52-80"},
        {"ref": "ОСТ 108.632.02-80"},
        {"ref": "ОСТ 108.643.01-80"},
    ]
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    hits, marks = _highlight_on_page(page, refs, words=words)
    assert hits == 3
    assert marks == 6
    doc.close()


def test_rd_pa_ocr_year_mismatch_highlight():
    words = [
        (10.0, 100.0, 30.0, 112.0, "PA", 0, 0, 0),
        (35.0, 100.0, 120.0, 112.0, "34.03.304-87", 0, 0, 1),
    ]
    from belener.normative_extract import _find_pinpoint_rects

    rects = _find_pinpoint_rects(words, "РД 34.03.304-67")
    assert len(rects) >= 2


def test_stp_ctn_comma_number_highlight():
    words = [
        (10.0, 100.0, 35.0, 112.0, "CTN", 0, 0, 0),
        (40.0, 100.0, 150.0, 112.0, "33240.49,101-2018", 0, 0, 1),
    ]
    from belener.normative_extract import _find_pinpoint_rects

    rects = _find_pinpoint_rects(words, "СТП 33240.49.101-2018")
    assert len(rects) >= 2
    import fitz

    from belener.normative_extract import _filter_pinpoint_rects

    page = fitz.Rect(0, 0, 1200, 800)
    big = fitz.Rect(100, 50, 700, 400)
    small = fitz.Rect(200, 100, 320, 118)
    out = _filter_pinpoint_rects([big, small], page)
    assert len(out) == 1
    assert out[0].width < 150


def test_zone_text_mentions_ref():
    from belener.normative_extract import _zone_text_mentions_ref

    assert _zone_text_mentions_ref("асфальт по СТБ 1033-2016", "СТБ 1033-2016")
    assert not _zone_text_mentions_ref("бетон B15", "СТБ 1033-2016")


def test_preview_scan_uses_pil_highlights():
    """На image-only PDF жёлтый рисуем поверх pixmap (PIL), не через annots."""
    import fitz

    from belener.normative_extract import (
        _collect_highlight_rects,
        _render_preview_image_with_highlights,
    )

    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    words = [
        (72.0, 100.0, 110.0, 115.0, "ГОСТ", 0, 0, 0),
        (115.0, 100.0, 180.0, 115.0, "8267-93", 0, 0, 1),
    ]
    refs = [{"ref": "ГОСТ 8267-93"}]
    _, _, rects = _collect_highlight_rects(page, refs, [words])
    img = _render_preview_image_with_highlights(page, rects, dpi=120)
    yellow = sum(
        1
        for px in img.getdata()
        if px[0] > 200 and px[1] > 180 and px[2] < 140
    )
    assert yellow > 20
    doc.close()


def test_album_number_without_kind_not_highlighted():
    """Серия/альбом 1.400-15 без ГОСТ — не подсвечивать как норматив."""
    words = [
        (60.0, 200.0, 120.0, 215.0, "1.400-15", 0, 0, 0),
        (130.0, 200.0, 170.0, 215.0, "ГОСТ", 0, 0, 1),
        (175.0, 200.0, 240.0, 215.0, "27772-2015", 0, 0, 2),
    ]
    spans = _all_word_spans_for_ref(words, "ГОСТ 27772-2015")
    assert spans == [(1, 2)]
    spans_album = _all_word_spans_for_ref(words, "ГОСТ 1400-15")
    assert spans_album == []
    rects = _find_pinpoint_rects(words, "ГОСТ 27772-2015")
    assert all(r.x0 >= 130 for r in rects)
