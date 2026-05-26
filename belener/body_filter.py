"""Фильтрация OCR текста поля чертежа перед отчётом (без привязки к проекту)."""

from __future__ import annotations

import re

from belener.report_clean import _looks_like_ocr_noise

# Заголовки колонок таблицы изменений штампа (ГОСТ) — не текст схемы
_STAMP_COL = re.compile(
    r"\b(изм\.?|кол\.?\s*уч|кол\.|лист|№\s*док|подп\.|дата)\b",
    re.I,
)


def _line_is_stamp_table_row(line: str) -> bool:
    if line.count("|") < 2:
        return False
    return len(_STAMP_COL.findall(line)) >= 2


def _line_is_title_block_prose(line: str) -> bool:
    """Длинная строка основной надписи (не подпись на схеме)."""
    s = line.strip()
    if len(s) < 90:
        return False
    words = re.findall(r"[А-Яа-яЁё]{4,}", s)
    return len(words) >= 8 and ("," in s or "«" in s or '"' in s)


def filter_body_text(raw: str) -> str:
    """Оставить читаемые подписи схемы; убрать сетку штампа и простыни мусора."""
    kept: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s or len(s) < 2:
            continue
        if _line_is_stamp_table_row(s):
            continue
        if _line_is_title_block_prose(s):
            continue
        if len(s) > 160 and _looks_like_ocr_noise(s):
            continue
        kept.append(s)
    return "\n".join(kept).strip()


def body_text_usable(raw: str) -> bool:
    text = filter_body_text(raw)
    if len(text) < 80:
        return False
    lines = [ln for ln in text.splitlines() if len(ln.strip()) >= 4]
    if len(lines) < 3:
        return False
    pipe_lines = sum(1 for ln in lines if ln.count("|") >= 2)
    if pipe_lines / max(len(lines), 1) > 0.25:
        return False
    xref = sum(1 for ln in lines if re.search(r"\b[хx]\d+:\d+\b", ln, re.I))
    if xref / max(len(lines), 1) > 0.35:
        return False
    good = sum(1 for ln in lines if not _looks_like_ocr_noise(ln))
    return good >= max(3, int(len(lines) * 0.5))
