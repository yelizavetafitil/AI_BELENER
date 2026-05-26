"""Универсальные якоря разделов САПР-листа (без привязки к проекту)."""

from __future__ import annotations

import re

# Экспликация / ведомость объектов (в т.ч. OCR: «эксп…» + здан/сооруж без подстановки слов)
EXPLICATION_START_RX = (
    r"(?:"
    r"экспликац\w*"
    r"|эксп\w{4,20}(?:\s+\w+){0,8}(?:здан|сооруж|объект|элемент)"
    r"|ведомост\w*(?:\s+\w+){0,4}\s*(?:здан|сооруж|объект|элемент)"
    r"|перечень\s+(?:здан|сооруж|объект)"
    r"|спецификац\w*\s+(?:здан|сооруж|объект)"
    r")"
)

# Спецификация / ведомость (поз., материалы, перечень аппаратуры)
SPECIFICATION_START_RX = (
    r"(?:"
    r"спецификац\w*"
    r"|ведомост\w*\s+(?:материал|оборудован|элемент)"
    r"|перечень\s+материал"
    r"|перечень\s+аппаратур"
    r"|продолжен\w*\s+таблиц"
    r"|поз\.?\s+обозначен"
    r")"
)

# Условные обозначения / легенда
LEGEND_START_RX = (
    r"(?:"
    r"условн\w*\s+обознач"
    r"|легенд\w*"
    r"|условн\w*\s+знак"
    r"|обозначен\w*\s+и\s+легенд"
    r"|сведения\s+об\s+условн\w*\s+обознач"
    r")"
)

# Конец блока экспликации — начало легенды или штампа
EXPLICATION_END_RX = (
    LEGEND_START_RX + r"|(?:^|\s)(?:таблица\s*\d+|таблица\s*[^\s\d]{1,3})\b|разро[бь]|формат\s*a\d"
)

# Заголовок произвольной таблицы на листе (цифра в номере)
GENERIC_TABLE_RX = r"таблица\s*\d+(?:\.\d+)?"

# Метка «Таблица N» в OCR: цифра или 1–3 не-цифровых символа вместо цифры (^, |, l…)
TABLE_LABEL_LOOSE_RX = r"таблица\s*(?:\d+(?:\.\d+)?|[^\s\d]{1,3})"

# Якоря штампа ГОСТ
STAMP_MARK_RX = (
    r"(?:"
    r"разро[бьёe]|разраб\.|"
    r"обозначен\w*\s*/\s*шифр|"
    r"ру\s*[\"«]|ооо\s*[\"«]|"
    r"\d{3,6}\s*-\s*\d+\s*-\s*[\wА-Яа-яЁё\d\-]{3,}|"
    r"стадия|лист(?:ов)?\b|"
    r"гип\b|н\.?\s*контр"
    r")"
)


def has_explication_anchor(text: str) -> bool:
    return bool(re.search(EXPLICATION_START_RX, text or "", re.I))


def has_legend_anchor(text: str) -> bool:
    return bool(re.search(LEGEND_START_RX, text or "", re.I))


def has_specification_anchor(text: str) -> bool:
    return bool(re.search(SPECIFICATION_START_RX, text or "", re.I))


def stamp_score(text: str) -> int:
    t = text or ""
    score = 0
    if re.search(STAMP_MARK_RX, t, re.I):
        score += 3
    if re.search(r"\d{3,6}\s*-\s*\d+\s*-", t):
        score += 4
    if re.search(r"1\s*:\s*\d{2,5}", t):
        score += 2
    if re.search(r"формат\s*a\d|формат\s*а\d", t, re.I):
        score += 2
    if re.search(r"разро[бь]", t, re.I):
        score += 2
    return score
