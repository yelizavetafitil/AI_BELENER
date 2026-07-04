"""Эталон: спецификация + ТТ (анонимный OCR-текст, не привязан к файлу)."""

from belener.normative_refs import extract_normative_refs, merge_normative_refs_from_sources

SPEC_TABLE_TT_TEXT = """
75 ОСТ 34-10-425-90
3-13 ОСТ 34 10.756-97
ГОСТ 481-80
ГОСТ 7798-70
ГОСТ 5915-70
01 ОСТ 34 10.757-97
ГОСТ 2246-70
ГОСТ 8.586.2-2005
СТП 34.17.101 (РД 34.17.101)
СТП 34.39.201 (РД 34.39.201)
"""

EXPECTED_NORMATIVES = [
    ("ОСТ", "34-10-425-90"),
    ("ОСТ", "34 10.756-97"),
    ("ГОСТ", "481-80"),
    ("ГОСТ", "7798-70"),
    ("ГОСТ", "5915-70"),
    ("ОСТ", "34 10.757-97"),
    ("ГОСТ", "2246-70"),
    ("ГОСТ", "8.586.2-2005"),
    ("СТП", "34.17.101"),
    ("РД", "34.17.101"),
    ("СТП", "34.39.201"),
    ("РД", "34.39.201"),
]

TILE_SOURCES = [
    "75 ОСТ 34-10-425-90\n3-13 ОСТ 34 10.756-97",
    "ГОСТ 481-80 ГОСТ 7798-70 ГОСТ 5915-70",
    "01 ОСТ 34 10.757-97 ГОСТ 2246-70",
    "ГОСТ 8.586.2-2005 СТП 34.17.101 (РД 34.17.101) СТП 34.39.201 (РД 34.39.201)",
    "ГОСТ 5915-7001 ОСТ 34 10.757-97",
]


def _norm_num(kind: str, ref: str) -> str:
    import re

    if kind == "ОСТ":
        m = re.search(r"(?:ост|oct|ost)\s*(.+)$", ref, re.I)
        return re.sub(r"\D", "", m.group(1)) if m else ""
    if kind == "ГОСТ":
        m = re.search(r"(?:гост|gost)\s*(.+)$", ref, re.I)
        return re.sub(r"\D", "", m.group(1)) if m else ""
    if kind in ("СТП", "РД"):
        m = re.search(r"(?:стп|stp|рд|rd)\s*(.+)$", ref, re.I)
        return re.sub(r"\D", "", m.group(1)) if m else ""
    return ref


def _assert_expected(refs: list[dict[str, str]]) -> None:
    got = {(r["kind"], _norm_num(r["kind"], r["ref"])) for r in refs}
    expected = {(k, _norm_num(k, f"{k} {n}")) for k, n in EXPECTED_NORMATIVES}
    assert got == expected, f"missing={expected - got} extra={got - expected}"


def test_spec_table_and_tt_full_text():
    refs = extract_normative_refs(SPEC_TABLE_TT_TEXT)
    assert len(refs) == 12
    _assert_expected(refs)


def test_spec_multi_tile_merge():
    refs = merge_normative_refs_from_sources(*TILE_SOURCES)
    assert len(refs) == 12
    _assert_expected(refs)


def test_ost_neighbors_both_kept():
    refs = merge_normative_refs_from_sources(
        "ОСТ 34 10.756-97",
        "ОСТ 34 10.757-97",
    )
    nums = {_norm_num("ОСТ", r["ref"]) for r in refs if r["kind"] == "ОСТ"}
    assert "341075697" in nums
    assert "341075797" in nums


def test_glued_year_and_ost_lead():
    text = "ГОСТ 5915-7001 ОСТ 34 10.757-97 ГОСТ 2246-70"
    refs = extract_normative_refs(text)
    kinds = {(r["kind"], _norm_num(r["kind"], r["ref"])) for r in refs}
    assert ("ГОСТ", "591570") in kinds
    assert ("ОСТ", "341075797") in kinds
    assert ("ГОСТ", "224670") in kinds


BNP007_SPEC_BR = """
ГОСТ 5525-88 Патрубок
Лист 5x250x250 ГОСТ 19903-2015
10 ОСТ 34-10-615-93 Опора 219К
СТБ 1544-2005 Бетон С 12/15
ГОСТ 3634-99 Люк Л (А15)-К.1
"""

BNP007_MUST_FROM_SPEC = [
    ("ГОСТ", "5525-88"),
    ("ГОСТ", "19903-2015"),
    ("ОСТ", "34-10-615-93"),
    ("СТБ", "1544-2005"),
    ("ГОСТ", "3634-99"),
]


def test_bnp007_spec_br_zone():
    refs = extract_normative_refs(BNP007_SPEC_BR)
    for kind, num in BNP007_MUST_FROM_SPEC:
        assert any(r["kind"] == kind and num in r["ref"] for r in refs), f"missing {kind} {num}"
