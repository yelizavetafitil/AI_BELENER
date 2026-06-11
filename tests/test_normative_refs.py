from belener.normative_refs import (
    dedupe_normative_year_variants,
    extract_normative_refs,
    merge_page_supplement,
    prune_unconfirmed_variants,
)


def test_ost_lead_60_from_bolt_not_captured():
    text = "Болт М16х60 ОСТ 34.10.699-97"
    refs = extract_normative_refs(text)
    kinds = [r["ref"] for r in refs if r["kind"] == "ОСТ"]
    assert not any(r.startswith("60 ") for r in kinds)
    assert any("34.10.699-97" in r.replace(" ", "") for r in kinds)


def test_ost_space_before_dot_normalized():
    text = "ОСТ 34 .10.700-97 Переход"
    refs = extract_normative_refs(text)
    ost = [r for r in refs if r["kind"] == "ОСТ"]
    assert ost
    assert "34.10.700-97" in ost[0]["ref"].replace(" ", "")


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
