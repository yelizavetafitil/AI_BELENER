# -*- coding: utf-8 -*-
"""ГОСТ, СТП, РД, ТУ, ТКП, СНиП и прочие сокращения — извлечение и подсветка."""

import pytest

from belener.normative_extract import _all_word_spans_for_ref, _find_pinpoint_rects
from belener.normative_refs import (
    _normalize_kind_label,
    _ref_highlight_target,
    extract_normative_refs,
    word_looks_like_kind_token,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("GOST", "ГОСТ"),
        ("гост", "ГОСТ"),
        ("SNIP", "СНиП"),
        ("СНиП", "СНиП"),
        ("СНИП", "СНиП"),
        ("STP", "СТП"),
        ("RD", "РД"),
        ("PA", "PA"),
        ("CO", "СО"),
        ("СО", "СО"),
        ("TU", "ТУ"),
        ("TKP", "ТКП"),
        ("STB", "СТБ"),
        ("SP", "СП"),
        ("ISO", "ISO"),
    ],
)
def test_normalize_kind_label(raw, expected):
    assert _normalize_kind_label(raw) == expected


@pytest.mark.parametrize(
    "ref,kind,number",
    [
        ("ГОСТ 10704-91", "ГОСТ", "10704-91"),
        ("ОСТ 34-10-615-93", "ОСТ", "34-10-615-93"),
        ("СТП 33240.49.101-2018", "СТП", "33240.49.101-2018"),
        ("РД 34.03.304-67", "РД", "34.03.304-67"),
        ("ТУ 6-21-51-90", "ТУ", "6-21-51-90"),
        ("ТКП 45-1.03-01-2018", "ТКП", "45-1.03-01-2018"),
        ("СНиП 3.05.06-85", "СНиП", "3.05.06-85"),
        ("СТБ 1033-2016", "СТБ", "1033-2016"),
        ("СП 63.13330.2018", "СП", "63.13330.2018"),
        ("СО 34.35.301-2004", "СО", "34.35.301-2004"),
    ],
)
def test_extract_and_highlight_target(ref, kind, number):
    extracted = extract_normative_refs(f"по {ref} в тексте")
    assert any(r["kind"] == kind and number in r["ref"] for r in extracted), extracted
    got_kind, canon, _ = _ref_highlight_target(ref)
    assert got_kind == kind, ref
    assert canon


@pytest.mark.parametrize(
    "marker,kind",
    [
        ("GOST", "ГОСТ"),
        ("FOCT", "ГОСТ"),
        ("ОСТ", "ОСТ"),
        ("CTN", "СТП"),
        ("PA", "РД"),
        ("ТУ", "ТУ"),
        ("TKN", "ТКП"),
        ("СНиП", "СНиП"),
        ("SNIP", "СНиП"),
        ("СТБ", "СТБ"),
        ("CTB", "СТБ"),
    ],
)
def test_ocr_kind_markers(marker, kind):
    assert word_looks_like_kind_token(marker, kind)


@pytest.mark.parametrize(
    "ref,token",
    [
        ("СНиП 3.05.06-85", "3.05.06-85"),
        ("СТП 33240.49.101-2018", "33240.49.101-2018"),
        ("РД 34.03.304-67", "34.03.304-87"),
        ("ТКП 45-1.03-01-2018", "45-1.03-01-2018"),
        ("ТУ 6-21-51-90", "6-21-51-90"),
    ],
)
def test_bare_number_highlight_without_prefix(ref, token):
    words = [(10.0, 100.0, 120.0, 112.0, token, 0, 0, 0)]
    spans = _all_word_spans_for_ref(words, ref)
    assert spans == [(0, 0)], ref
    assert _find_pinpoint_rects(words, ref)


def test_gost_bare_number_not_highlighted_without_marker():
    words = [(60.0, 200.0, 120.0, 215.0, "10704-91", 0, 0, 0)]
    spans = _all_word_spans_for_ref(words, "ГОСТ 10704-91")
    assert spans == []


def test_stp_with_ctn_marker_highlight():
    words = [
        (10.0, 100.0, 35.0, 112.0, "CTN", 0, 0, 0),
        (40.0, 100.0, 150.0, 112.0, "33240.49.101-2018", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "СТП 33240.49.101-2018")
    assert len(rects) >= 2


def test_rd_with_pa_marker_highlight():
    words = [
        (10.0, 100.0, 30.0, 112.0, "PA", 0, 0, 0),
        (35.0, 100.0, 120.0, 112.0, "34.03.304-87", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "РД 34.03.304-67")
    assert len(rects) >= 2


def test_parenthesized_tu_extract():
    out = extract_normative_refs("10 (ТУ 6-21-51-90).")
    assert any(r["kind"] == "ТУ" and "6-21-51-90" in r["ref"] for r in out)
