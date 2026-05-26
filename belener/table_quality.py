"""Оценка качества OCR таблицы — для выбора fallback без привязки к проекту."""

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

    # Штраф за одну длинную «простыню» без разбиения
    if len(lines) <= 2 and len(t) > 120 and tab_lines == 0:
        score *= 0.5

    return min(1.0, score)


def table_ocr_weak(text: str, *, threshold: float = 0.32) -> bool:
    return table_ocr_quality(text) < threshold
