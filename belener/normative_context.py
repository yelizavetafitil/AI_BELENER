"""Контекстный фильтр шума (размер, координата) — без угадывания типа по номеру."""

from __future__ import annotations

import re

_FUZZY_GOST = re.compile(r"(?i)g[o0][cсs][tт]|g[o0]st|г[o0][cс][tт]|г[o0]ст")
_FUZZY_OST = re.compile(r"(?i)(?<![a-zа-яё])o[cс][tт](?![a-zа-яё])|(?<![a-zа-яё])0[cс][tт](?![a-zа-яё])")

_PREFIX_KIND: dict[str, str] = {
    "гost": "ГОСТ",
    "гост": "ГОСТ",
    "gost": "ГОСТ",
    "ост": "ОСТ",
    "oct": "ОСТ",
    "ost": "ОСТ",
    "стб": "СТБ",
    "stb": "СТБ",
    "ту": "ТУ",
    "tu": "ТУ",
    "стп": "СТП",
    "stp": "СТП",
    "снип": "СНиП",
    "snip": "СНиП",
    "ткп": "ТКП",
    "tkp": "ТКП",
    "сп": "СП",
    "iso": "ISO",
    "iec": "IEC",
    "din": "DIN",
    "en": "EN",
    "api": "API",
    "astm": "ASTM",
    "rd": "РД",
    "рд": "РД",
    "со": "СО",
    "co": "СО",
    "so": "СО",
    "нпб": "НПБ",
    "vsn": "ВСН",
}


def fuzzy_normative_text(text: str) -> str:
    s = text or ""
    s = _FUZZY_GOST.sub("ГОСТ", s)
    s = _FUZZY_OST.sub("ОСТ", s)
    return s


def is_noise_span(window: str, num: str) -> bool:
    compact = re.sub(r"\s+", "", num or "")
    if re.fullmatch(r"\d+[xх×]\d+", compact, re.I):
        return True
    if re.fullmatch(r"\d,\d{3}", compact):
        return True
    return False


def resolve_prefix_kind(raw_prefix: str) -> str | None:
    p = re.sub(r"\s+", " ", (raw_prefix or "").strip()).casefold()
    return _PREFIX_KIND.get(p)


def accept_by_context(window: str, num: str, *, prefix: str = "", min_score: int = 0) -> bool:
    """Явный префикс типа в паттерне — достаточно; иначе отсечь только шум."""
    if prefix:
        return not is_noise_span(window, num)
    return not is_noise_span(window, num)
