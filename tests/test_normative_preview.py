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
