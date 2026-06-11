"""Оценка качества OCR таблицы — универсальные метрики без привязки к чертежу."""

from __future__ import annotations

import re


def table_ocr_quality(text: str) -> float:
    """
    0..1: выше — структура похожа на таблицу (табы, колонки, кириллица).
    Ниже ~0.35 — имеет смысл img2table / повторный проход.
    """
    t = (text or "").strip()
    if len(t) < 20:
        return 0.0
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if not lines:
        return 0.0

    score = 0.0
    tab_lines = sum(1 for ln in lines if "\t" in ln)
    multi_col = sum(1 for ln in lines if len(re.split(r"\t|\s{2,}", ln.strip())) >= 3)
    score += min(0.35, tab_lines / max(len(lines), 1) * 0.5)
    score += min(0.25, multi_col / max(len(lines), 1) * 0.4)

    letters = re.findall(r"[А-Яа-яЁёA-Za-z]", t)
    if letters:
        cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
        score += 0.2 * (cyr / len(letters))

    spec_marks = len(
        re.findall(
            r"поз\.?|обознач|наименован|кол\.?|примечан|условн",
            t,
            re.I,
        )
    )
    if spec_marks >= 2:
        score += 0.15

    if len(lines) <= 2 and len(t) > 120 and tab_lines == 0:
        score *= 0.5

    return min(1.0, score)


def table_ocr_weak(text: str, *, threshold: float = 0.32) -> bool:
    return table_ocr_quality(text) < threshold


def spec_table_header_present(text: str) -> bool:
    """Шапка перечня: Поз. + Обозначение/Наименование."""
    t = (text or "").casefold()
    if not t:
        return False
    has_pos = bool(re.search(r"поз\.?", t))
    has_cols = bool(re.search(r"обознач", t)) and bool(re.search(r"наименован", t))
    return has_pos and has_cols


def _readability(text: str) -> float:
    from belener.parse import _readability_score

    return _readability_score(text)


_RUSSIAN_LETTERS = frozenset(
    "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
)


def _non_russian_cyrillic(text: str) -> bool:
    for ch in text or "":
        if "\u0400" <= ch <= "\u04FF" and ch not in _RUSSIAN_LETTERS:
            return True
    return False


def mixed_script_ocr_glitch(text: str) -> bool:
    """Смешение латиницы и кириллицы в одном токене — типичный сбой OCR."""
    s = (text or "").strip()
    if not s or not re.search(r"[A-Za-z]", s) or not re.search(r"[А-Яа-яЁё]", s):
        return False
    for token in re.findall(r"\S+", s):
        if re.search(r"[A-Za-z]", token) and re.search(r"[А-Яа-яЁё]", token):
            return True
    lat = len(re.findall(r"[A-Za-z]", s))
    return lat >= 2 and lat / max(len(s), 1) > 0.06


def ocr_line_implausible_for_legend(line: str, *, min_readability: float = 8.0) -> bool:
    """True — строка не похожа на осмысленную запись легенды."""
    s = re.sub(r"\s+", " ", (line or "").strip())
    if not s or len(s) < 5:
        return True
    if _non_russian_cyrillic(s):
        return True
    if mixed_script_ocr_glitch(s):
        return True
    letters = re.findall(r"[А-Яа-яЁё]", s)
    if len(letters) < 4:
        return True
    vowels = sum(1 for c in letters if c.lower() in "аеёиоуыэюя")
    if vowels / max(len(letters), 1) < 0.18:
        return True
    if re.search(r"[бвгджзклмнпрстфхцчшщ]{5,}", s, re.I):
        return True
    if re.search(r"(.)\1{3,}", s, re.I):
        return True
    if _readability(s) < min_readability:
        return True
    return False


def legend_ocr_plausible(text: str, rows: list[dict] | None = None) -> bool:
    """Легенда похожа на таблицу условных обозначений, а не на подписи схемы / шапку."""
    from belener.parse import _is_column_header_line

    t = (text or "").strip()
    rs = rows or []
    notes = [str(r.get("note") or "").strip() for r in rs if isinstance(r, dict)]
    if not notes:
        notes = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(notes) < 2:
        return False

    good: list[str] = []
    for n in notes:
        if _is_column_header_line(n):
            continue
        if ocr_line_implausible_for_legend(n):
            continue
        good.append(n)

    if len(good) < 2:
        return False

    substantive = [
        n
        for n in good
        if len(n.split()) >= 2 and len(n) >= 18 and _readability(n) >= 12.0
    ]
    return len(substantive) >= 2


def spec_table_plausible(text: str, rows: list[dict] | None = None) -> bool:
    """Перечень аппаратуры — не подписи элементов схемы."""
    from belener.spec_table import is_schematic_caption_row

    rs = [r for r in (rows or []) if isinstance(r, dict)]
    if not rs:
        return False
    t = (text or "").strip()
    non_cap = [r for r in rs if not is_schematic_caption_row(r)]
    if spec_table_header_present(t):
        return len(non_cap) >= 1
    if len(non_cap) < 2:
        return False
    good = 0
    for r in non_cap:
        name = str(r.get("Наименование") or "").strip()
        if len(name) >= 8 and _readability(name) >= 10.0:
            good += 1
    return good >= 2


def explication_table_plausible(text: str, rows: list[dict] | None = None) -> bool:
    """Экспликация — не обрывок OCR из перечня."""
    rs = [r for r in (rows or []) if isinstance(r, dict)]
    if not rs:
        return False
    good = 0
    for r in rs:
        name = str(
            r.get("Наименование") or r.get("name") or r.get("Наименование помещения") or ""
        ).strip()
        coords = str(r.get("Координаты") or r.get("coordinates") or "").strip()
        if coords and coords not in ("—", "-", ""):
            good += 1
            continue
        if len(name) >= 12 and _readability(name) >= 10.0:
            good += 1
    if len(rs) >= 2 and good >= 1:
        return True
    if len(rs) == 1 and good >= 1 and len(str(rs[0].get("Наименование") or "")) >= 15:
        return True
    return False
