"""Политика: без подстановки номеров и без привязки к конкретным чертежам."""

import re
from pathlib import Path

import pytest

from belener.config import stn_ocr_variant_limit
from belener.normative_refs import (
    _canonical_number,
    extract_normative_refs,
    merge_normative_refs_from_sources,
)

ROOT = Path(__file__).resolve().parents[1]
NORMATIVE_SRC = ROOT / "belener" / "normative_refs.py"


def _digits_in_sources(ref: dict[str, str], *sources: str) -> bool:
    kind = str(ref.get("kind") or "")
    body = _canonical_number(kind, str(ref.get("ref") or ""))
    if not body:
        return True
    blob = re.sub(r"\D", "", " ".join(sources))
    return body in blob


def test_merge_never_invents_gost_digits():
    """Итог merge — только цифры, встречающиеся в OCR-тексте тайлов."""
    sources = (
        "25х2 ГОСТ 1070-91",
        "32х2 ГОСТ 10704-91",
        "32х2 ГОСТ 10704-91",
        "ГОСТ 10705-91",
    )
    out = merge_normative_refs_from_sources(*sources)
    for item in out:
        assert _digits_in_sources(item, *sources), item


def test_extract_does_not_substitute_known_typo_pair():
    """Нет таблицы «исправлений» 10705→10704 и т.п."""
    raw = "ГОСТ 10705-91 ГОСТ 16705-80"
    refs = extract_normative_refs(raw)
    joined = " ".join(r["ref"] for r in refs)
    assert "10704" not in joined
    assert "10705-91" in joined or "16705-80" in joined


def test_stn_ocr_digit_swaps_limited_on_retry():
    """OCR-варианты только при повторном поиске STN, с ограничением."""
    assert 0 < stn_ocr_variant_limit() <= 8


def test_production_normative_module_has_no_fixed_gost_literals():
    """В коде парсера нет литералов вида «ГОСТ 12345-XX» (кроме комментариев)."""
    text = NORMATIVE_SRC.read_text(encoding="utf-8")
    code_lines = [
        ln
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert not re.search(r'["\']ГОСТ\s+\d{3,}[-\d]+["\']', code)
    assert not re.search(r'=\s*["\']\d{4,}-\d{2,4}["\']', code)
    assert not re.search(r'["\']СТБ\s+\d{3,4}-\d{4}["\']', code)


def test_stb_year_recovery_needs_tnpa_context():
    """Восстановление обрезанного года СТБ — только в блоке ТНПА и при наличии полного года."""
    outside = "СТБ 2073-2010 и СТБ 2235-20 в тексте"
    refs_out = extract_normative_refs(outside)
    stb_out = {r["ref"] for r in refs_out if r.get("kind") == "СТБ"}
    assert "СТБ 2073-2010" in stb_out
    assert "СТБ 2235-2011" not in stb_out

    inside = (
        "2 Чертежи разработаны в соответствии с действующими ТНПА:\n"
        "- СТБ 2073-2010\n"
        "- СТБ 2235-20"
    )
    refs_in = extract_normative_refs(inside)
    stb_in = {r["ref"] for r in refs_in if r.get("kind") == "СТБ"}
    assert "СТБ 2073-2010" in stb_in
    assert "СТБ 2235-2011" in stb_in


@pytest.mark.parametrize(
    "blob",
    [
        "75 ОСТ 34-10-425-90\nГОСТ 481-80\n01 ОСТ 34 10.757-97",
        "ОСТ 34 10.756-97\nОСТ 34 10.757-97",
        "СТП 34.17.101 (РД 34.17.101)\nГОСТ 8.586.2-2005",
    ],
)
def test_universal_patterns_on_anonymous_ocr(blob: str):
    """Анонимные OCR-фрагменты — только regex/структура, без имён файлов."""
    refs = extract_normative_refs(blob)
    assert refs
    for item in refs:
        assert _digits_in_sources(item, blob)
