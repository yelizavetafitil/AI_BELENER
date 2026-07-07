import re

from belener.normative_refs import (
    dedupe_normative_year_variants,
    extract_normative_refs,
    merge_normative_refs_from_sources,
    merge_page_supplement,
    prune_unconfirmed_variants,
)


def test_highlight_patterns_strict():
    from belener.normative_refs import highlight_patterns_for_normative_ref

    terms = highlight_patterns_for_normative_ref("В-ступ ГОСТ 10705-80")
    assert all(re.search(r"(?i)гост|gost", t) for t in terms)
    assert not any(t == "10705-80" for t in terms)

    ost = highlight_patterns_for_normative_ref("ОСТ 34-10-615-93")
    assert all("ОСТ" in t or "ост" in t.lower() or "OCT" in t for t in ost)
    assert not any(t == "34-10-615-93" for t in ost)
    assert any("ОСТ34-10-615-93" in t.replace(" ", "") for t in ost)
    assert any(t.startswith("(") for t in ost)
    assert any(t.startswith("OCT ") for t in ost)

    ost108 = highlight_patterns_for_normative_ref("ОСТ 108.275.52-80")
    assert any("OCT 108.275.52-80" in t for t in ost108)


def test_highlight_parenthesized_gost_in_words():
    from belener.normative_extract import _find_pinpoint_rects

    words = [
        (72.0, 300.0, 118.0, 315.0, "(ГОСТ", 0, 0, 0),
        (120.0, 300.0, 175.0, 315.0, "12707-77)", 0, 0, 1),
    ]
    rects = _find_pinpoint_rects(words, "ГОСТ 12707-77")
    assert len(rects) == 2


def test_search_terms_for_highlight():
    from belener.normative_refs import search_terms_for_normative_ref

    terms = search_terms_for_normative_ref("В-ступ ГОСТ 10705-80")
    joined = " ".join(terms).casefold()
    assert "гост 10705-80" in joined
    assert "10705-80" not in terms


def test_ost_34_10_series_spacing():
    cases = {
        "34 10 699-97": "34 10.699-97",
        "34 10699-97": "34 10.699-97",
        "34 10.700-97": "34 10.700-97",
    }
    for raw, expected in cases.items():
        refs = extract_normative_refs(f"ОСТ {raw}")
        assert refs, raw
        assert expected.replace(" ", "") in refs[0]["ref"].replace(" ", ""), f"{raw} -> {refs[0]['ref']}"
        assert "34.10." not in refs[0]["ref"], f"bad dot: {refs[0]['ref']}"


def test_spec_fraction_gosts():
    text = (
        "ГОСТ 24379.1-80\n"
        "Лист 20 ГОСТ 19903-74 / Ст3сп3 ГОСТ 14637-89\n"
        "Лист 5 ГОСТ 19903-74 / Ст3сп3 ГОСТ 14637-89"
    )
    refs = merge_normative_refs_from_sources(text)
    got = {(r["kind"], r["ref"]) for r in refs}
    assert ("ГОСТ", "ГОСТ 24379.1-80") in got
    assert ("ГОСТ", "ГОСТ 19903-74") in got
    assert ("ГОСТ", "ГОСТ 14637-89") in got
    assert len(refs) == 3


def test_strip_po_before_gost():
    text = "по ГОСТ 9.402-2004\nпо ГОСТ 9467-75\nГОСТ 5264-80\nТКП 45-2.01-111-2008"
    refs = extract_normative_refs(text)
    for r in refs:
        assert not re.match(r"^по\s", r["ref"], re.I), r["ref"]
    assert any("9.402-2004" in r["ref"] for r in refs)
    assert any("9467-75" in r["ref"] for r in refs)


def test_multitile_no_po_or_ocr_garbage():
    tiles = [
        "по ГОСТ 9.402-2004",
        "ТКП 45-2.01-111-2008",
        "ГОСТ 5264-80",
        "по ГОСТ 9467-75",
        "ГОСТ 5264 80-11",
    ]
    refs = merge_normative_refs_from_sources(*tiles)
    got = {r["ref"] for r in refs}
    assert "ГОСТ 9.402-2004" in got
    assert "ГОСТ 9467-75" in got
    assert "ГОСТ 5264-80" in got
    assert not any(re.match(r"^по\s", r, re.I) for r in got)
    assert "ГОСТ 5264 80-11" not in got


def test_gost_spaced_number_normalizes():
    refs = extract_normative_refs("ГОСТ 5264 80-11")
    assert any("5264-80" in r["ref"] for r in refs)
    assert not any("80-11" in r["ref"] for r in refs)


def test_gost_27772_drops_truncated_2772():
    out = merge_normative_refs_from_sources(
        "С235 GОСТ 27772-2015",
        "ГОСТ 2772-2015 лист",
    )
    gosts = [r["ref"] for r in out if r["kind"] == "ГОСТ" and "277" in r["ref"]]
    assert any("27772-2015" in r for r in gosts)
    assert not any(re.match(r".*ГОСТ\s+2772-2015", r, re.I) for r in gosts)


def test_gost_one_digit_ocr_pair():
    from belener.normative_refs import _gost_one_digit_ocr_pair

    assert _gost_one_digit_ocr_pair("27772", "2772")
    assert not _gost_one_digit_ocr_pair("33259", "3325")
    assert not _gost_one_digit_ocr_pair("10704", "10705")
    refs = extract_normative_refs("Г0СТ94.67-75 Электроды")
    assert any("9467-75" in r["ref"] and "94.67" not in r["ref"] for r in refs)


def test_stp_dots_from_spaces():
    refs = extract_normative_refs("СТП 34 39 201\nСТП 34.17.101")
    assert any("34.39.201" in r["ref"] for r in refs)
    assert any("34.17.101" in r["ref"] for r in refs)


def test_ost_108_series_dots_restored():
    cases = {
        "108275.52-80": "108.275.52-80",
        "108 367 37-80": "108.367.37-80",
        "10827552-80": "108.275.52-80",
        "108632 02-80": "108.632.02-80",
        "10864301-80": "108.643.01-80",
        "108.764.01-80": "108.764.01-80",
        "108275 56-80": "108.275.56-80",
    }
    for raw, expected in cases.items():
        refs = extract_normative_refs(f"ОСТ {raw}")
        assert refs, raw
        assert expected.replace(" ", "") in refs[0]["ref"].replace(" ", ""), f"{raw} -> {refs[0]['ref']}"


def test_gost_not_parsed_as_ost():
    text = "ГОСТ 1050-88\nГОСТ 9467-75"
    refs = extract_normative_refs(text)
    assert not any(r["kind"] == "ОСТ" for r in refs)


def test_ost_zero_cyrillic_ocr():
    text = "19.0СТ 108275.52-80\n020СТ 108632 02-80"
    refs = extract_normative_refs(text)
    ost = [r["ref"] for r in refs if r["kind"] == "ОСТ"]
    assert any("108.275.52-80" in r.replace(" ", "") for r in ost)
    assert any("108.632.02-80" in r.replace(" ", "") for r in ost)


def test_ref_vote_counts_loosened_ost_ocr():
    from belener.normative_refs import _ref_vote_count

    src = "020СТ 108632 01-80"
    assert _ref_vote_count("ОСТ", "ОСТ 108.632.01-80", [src]) >= 1


def test_ost_lead_60_from_bolt_not_captured():
    text = "Болт М16х60 ОСТ 34.10.699-97"
    refs = extract_normative_refs(text)
    kinds = [r["ref"] for r in refs if r["kind"] == "ОСТ"]
    assert not any(r.startswith("60 ") for r in kinds)
    assert any("3410.699-97" in r.replace(" ", "") for r in kinds)


def test_ost_space_before_dot_normalized():
    text = "ОСТ 34 .10.700-97 Переход"
    refs = extract_normative_refs(text)
    ost = [r for r in refs if r["kind"] == "ОСТ"]
    assert ost
    assert "3410.700-97" in ost[0]["ref"].replace(" ", "")


def test_ost_incomplete_36_146_rejected():
    text = "ОСТ 36-146 опора"
    refs = extract_normative_refs(text)
    assert not any("36-146" in r["ref"] and "88" not in r["ref"] for r in refs if r["kind"] == "ОСТ")


def test_ost_full_36_146_88_accepted():
    text = "ОСТ 36-146-88 Опора"
    refs = extract_normative_refs(text)
    assert any("36-146-88" in r["ref"].replace(" ", "") for r in refs if r["kind"] == "ОСТ")


def test_dedupe_year_variants_prefers_source():
    table = "ГОСТ 7798-70 Болт М16х60 ГОСТ 11371-78 Шайба ГОСТ 15180-86"
    noisy = table + " ГОСТ 7798-71 ГОСТ 11371-71 ГОСТ 11371-74 ГОСТ 15180-81"
    raw = extract_normative_refs(noisy)
    out = dedupe_normative_year_variants(raw, table)
    refs = {r["ref"] for r in out if r["kind"] == "ГОСТ"}
    assert any("7798-70" in r for r in refs)
    assert not any("7798-71" in r for r in refs)
    assert any("11371-78" in r for r in refs)
    assert not any("11371-71" in r or "11371-74" in r for r in refs)


def test_tu_from_sheet_notes():
    text = "по ТУ 6-21-51-90 эмаль ХВ-785"
    refs = extract_normative_refs(text)
    assert any(r["kind"] == "ТУ" and "6-21-51-90" in r["ref"] for r in refs)


def test_tu_in_parentheses_with_leading_number():
    text = "10 (ТУ 6-21-51-90)."
    refs = extract_normative_refs(text)
    assert any(r["kind"] == "ТУ" and "6-21-51-90" in r["ref"] for r in refs)


def test_prune_drops_unconfirmed_year_typo():
    table = (
        "ГОСТ 33259-2015 Фланец ГОСТ 11371-78 Шайба ГОСТ 15180-86 "
        "ГОСТ 7798-70 ГОСТ 5915-70"
    )
    noisy = table + " ГОСТ 33259-1000 ГОСТ 11371-71 ГОСТ 15180-81"
    raw = extract_normative_refs(noisy)
    out = prune_unconfirmed_variants(
        dedupe_normative_year_variants(raw, table),
        table,
    )
    refs = {r["ref"] for r in out if r["kind"] == "ГОСТ"}
    assert any("33259-2015" in r for r in refs)
    assert not any("33259-1000" in r for r in refs)
    assert any("11371-78" in r for r in refs)
    assert not any("11371-71" in r for r in refs)


def test_merge_page_supplement_blocks_typo():
    table = "ГОСТ 11371-78 ГОСТ 7798-70"
    primary = extract_normative_refs(table)
    page = extract_normative_refs(table + " ГОСТ 11371-71 ГОСТ 7798-71")
    merged = merge_page_supplement(primary, page, table)
    refs = {r["ref"] for r in merged if r["kind"] == "ГОСТ"}
    assert any("11371-78" in r for r in refs)
    assert not any("11371-71" in r for r in refs)


def test_ost_spacing_variants_dedupe():
    text = "ОСТ 34 10 700-97 ОСТ 34.10.699-97 ОСТ 34.10.700-97"
    refs = extract_normative_refs(text)
    ost700 = [r for r in refs if "700-97" in r["ref"].replace(" ", "")]
    assert len(ost700) == 1


def test_truncated_gost_year_joined():
    text = "ГОСТ 33259-20 15 Фланец ГОСТ 33259-2015"
    refs = extract_normative_refs(text)
    gost = [r["ref"] for r in refs if r["kind"] == "ГОСТ" and "33259" in r["ref"]]
    assert any("33259-2015" in r for r in gost)


def test_gost_as_read_not_rejected_by_digit_rule():
    refs = extract_normative_refs("ГОСТ 33259-20 Фланец")
    assert any("33259-20" in r["ref"] for r in refs)


def test_prefers_full_ref_with_gost():
    text = "76х3,0 ГОСТ 10704-91 ГОСТ 10704-91"
    refs = extract_normative_refs(text)
    gost = [r for r in refs if "10704-91" in r["ref"]]
    assert len(gost) == 1


def test_glued_gost_year_with_table_column():
    text = "57х2,5 ГОСТ 10704-9120| 336 м\nТруб Т В_20 ГОСТ 10705-80"
    refs = extract_normative_refs(text)
    refs_str = " ".join(r["ref"] for r in refs)
    assert "10704-91" in refs_str
    assert "10705-80" in refs_str


def test_stb_from_spec_table_ocr():
    text = "8 | СТБ 1544-2005\nГОСТ 3634-99"
    refs = extract_normative_refs(text)
    assert any(r.get("kind") == "СТБ" and "1544-2005" in r["ref"] for r in refs)


def test_stb_bare_number_after_row():
    text = "8 | 1544-2005\nБетон С 12/15"
    refs = extract_normative_refs(text)
    assert any("1544-2005" in r["ref"] for r in refs)


def test_stb_ocr_sb_and_paren_glue():
    text = "8 | СБ 1544-2005\n8 |(151544-2005"
    refs = extract_normative_refs(text)
    assert sum(1 for r in refs if "1544-2005" in r["ref"]) >= 1


def test_stb_ocr_spaced_year_and_glued_prefix():
    text = "СТБ2073-2010\nСТБ 2073 2010\nГОСТ 21.501-2018"
    refs = extract_normative_refs(text)
    stb = [r for r in refs if r.get("kind") == "СТБ"]
    assert len(stb) >= 1
    assert any("2073-2010" in r["ref"] for r in stb)
    assert any(r.get("kind") == "ГОСТ" for r in refs)


def test_stb_tnpa_list_second_item_without_prefix():
    """Общие указания: второй пункт «- 2235-2011» без «СТБ» после OCR."""
    text = (
        "2 Чертежи разработаны в соответствии с действующими ТНПА:\n"
        '- СТБ 2073-2010 "Правила выполнения чертежей генеральных планов предприятий,\n'
        "сооружений и жилищно-гражданских объектов;\n"
        '- 2235-2011 "Условные графические обозначения'
    )
    refs = extract_normative_refs(text)
    nums = {r["ref"] for r in refs if r.get("kind") == "СТБ"}
    assert "СТБ 2073-2010" in nums
    assert "СТБ 2235-2011" in nums


def test_tnpa_gost_list_without_prefix():
    text = (
        "2 Чертежи разработаны в соответствии с действующими ТНПА:\n"
        "- ГОСТ 10704-91\n"
        "- 10705-80"
    )
    refs = extract_normative_refs(text)
    gost = {r["ref"] for r in refs if r.get("kind") == "ГОСТ"}
    assert "ГОСТ 10704-91" in gost
    assert "ГОСТ 10705-80" in gost


def test_gp9_general_notes_both_stb():
    text = (
        "Общие указания\n"
        "2 Чертежи разработаны в соответствии с действующими ТНПА:\n"
        '- СТБ 2073-2010 "Правила выполнения чертежей генеральных планов предприятий,\n'
        "сооружений и жилищно-гражданских объектов;\n"
        '- 2235-2011 "Условные графические обозначения'
    )
    refs = extract_normative_refs(text)
    stb = {r["ref"] for r in refs if r.get("kind") == "СТБ"}
    assert "СТБ 2073-2010" in stb
    assert "СТБ 2235-2011" in stb


def test_gp9_ocr_truncated_stb_year_in_tnpa():
    """Реальный OCR л.1: «СТБ 2235-20» — год обрезан после «20»."""
    text = (
        "Общие указания\n"
        "2 Чертежи разработаны в соответствии с бедствующими ТНЛА:\n"
        '- СТБ 2073-2010 "Правила выполнения чертеже генеральных п\n'
        "сооружений и жилищно-гражданских объектов;\n"
        '- СТБ 2235-20 "Условные графические обозначения'
    )
    refs = extract_normative_refs(text)
    stb = {r["ref"] for r in refs if r.get("kind") == "СТБ"}
    assert "СТБ 2073-2010" in stb
    assert "СТБ 2235-2011" in stb


def test_stb_truncated_year_without_sibling_not_invented():
    """Без полного года в том же блоке ТНПА не дополняем год «из головы»."""
    text = "2 Чертежи разработаны в соответствии с действующими ТНПА:\n- СТБ 2235-20"
    refs = extract_normative_refs(text)
    stb = [r["ref"] for r in refs if r.get("kind") == "СТБ"]
    assert "2235-2011" not in " ".join(stb)
    assert "2235-2010" not in " ".join(stb)


def test_no_hardcoded_gost_replacement():
    """OCR-текст не переписывается под «ожидаемые» номера."""
    raw = "ГОСТ 10705-91 ГОСТ 16705-80"
    refs = extract_normative_refs(raw)
    refs_str = " ".join(r["ref"] for r in refs)
    assert "10704-91" not in refs_str
    assert "10705-91" in refs_str or "16705-80" in refs_str


def test_resolve_single_digit_by_tile_votes():
    from belener.normative_refs import merge_normative_refs_from_sources

    t1 = "ГОСТ 5264-80"
    t2 = "ГОСТ 5266-80"
    out = merge_normative_refs_from_sources(t1, t2, t1, t1)
    gost = [r["ref"] for r in out if r["kind"] == "ГОСТ"]
    assert len(gost) == 1
    assert "5264-80" in gost[0]


def test_gost_without_space_after_type():
    text = "Труба В-20Г0СТ10705-80 76х3,0 ГОСТ 10704-91"
    refs = extract_normative_refs(text)
    refs_str = " ".join(r["ref"] for r in refs if r["kind"] == "ГОСТ")
    assert "10705-80" in refs_str
    assert "10704-91" in refs_str


def test_merge_keeps_8962_and_8969():
    from belener.normative_refs import merge_normative_refs_from_sources

    t1 = "12___| ГОСТ 8962-75"
    t2 = "13 ГОСТ 8969-75"
    out = merge_normative_refs_from_sources(t1, t2)
    nums = {r["ref"] for r in out if r["kind"] == "ГОСТ"}
    assert any("8962" in r for r in nums)
    assert any("8969" in r for r in nums)


def test_merge_picks_10704_over_1070():
    from belener.normative_refs import merge_normative_refs_from_sources

    t_bad = "25х2 ГОСТ 1070-91"
    t_good = "32х2 ГОСТ 10704-91"
    out = merge_normative_refs_from_sources(t_bad, t_good, t_good, t_good)
    gost91 = [r["ref"] for r in out if r["kind"] == "ГОСТ" and "-91" in r["ref"]]
    assert any("10704-91" in r for r in gost91)
    assert not any("1070-91" in r for r in gost91)


def test_gost_10705():
    text = "Труба 76x3,0 ГОСТ 10704-91 В-Ст3пс ГОСТ 10705-80"
    refs = extract_normative_refs(text)
    assert any("10705-80" in r["ref"] for r in refs)
    assert any("10704-91" in r["ref"] for r in refs)
    text = "10 (ТУ 6-21-51-\n90)."
    refs = extract_normative_refs(text)
    assert any(r["kind"] == "ТУ" and "6-21-51-90" in r["ref"].replace(" ", "") for r in refs)


def test_reject_implausible_gost_year():
    refs = extract_normative_refs("ГОСТ 33259-1000 Фланец")
    assert not any("33259-1000" in r["ref"] for r in refs)


def test_prune_drops_ocr_only_when_pdf_has_base():
    table = "ГОСТ 11371-78 ГОСТ 7798-70"
    ocr_only = "ГОСТ 11371-71 ГОСТ 33259-1000"
    merged = extract_normative_refs(table) + extract_normative_refs(ocr_only)
    out = prune_unconfirmed_variants(merged, table)
    refs = {r["ref"] for r in out if r["kind"] == "ГОСТ"}
    assert any("11371-78" in r for r in refs)
    assert not any("11371-71" in r for r in refs)
    assert not any("33259" in r for r in refs)


def test_strip_pos_before_gost():
    text = "13 ГОСТ 8969-75 кабелю"
    refs = extract_normative_refs(text)
    gost = [r for r in refs if r["kind"] == "ГОСТ"]
    assert gost
    assert not any(r["ref"].startswith("13 ") for r in gost)
    assert any("8969-75" in r["ref"] for r in gost)


def test_tkp_snip_from_notes():
    text = (
        'с Правилами промышленной безопасности ТКП 45-4.03-267-2012 " '
        'СНиП 3.05.02-88 " Газоснабжение'
    )
    refs = extract_normative_refs(text)
    assert any(r["kind"] == "ТКП" and "267-2012" in r["ref"] for r in refs)
    assert any(r["kind"] == "СНиП" and "3.05.02-88" in r["ref"] for r in refs)


def test_tkp_ocr_spaced_letters():
    text = "Т К П 45-4.03-267-2012"
    refs = extract_normative_refs(text)
    assert any(r["kind"] == "ТКП" and "267-2012" in r["ref"] for r in refs)
