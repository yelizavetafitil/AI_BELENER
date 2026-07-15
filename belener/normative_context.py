"""Контекстный фильтр шума (размер, координата) — без угадывания типа по номеру."""

from __future__ import annotations

import re

_FUZZY_GOST = re.compile(r"(?i)g[o0][cсs][tт]|g[o0]st|f[o0]ct|t[o0]ct|г[o0][cс][tт]|г[o0]ст")
_FUZZY_OST = re.compile(
    r"(?i)(?<![a-zа-яё])(?:o[cсСC][tтТT]|0[cсСC][tтТT])(?![a-zа-яё])",
)
_FUZZY_STB = re.compile(r"(?i)(?<![a-zа-яё])(?:стб|stb|ctb|ct5|cte|cib)(?![a-zа-яё])")
_FUZZY_STP = re.compile(r"(?i)(?<![a-zа-яё])(?:стп|stp|ctn|stn)(?![a-zа-яё])")
_FUZZY_TKP = re.compile(r"(?i)(?<![a-zа-яё])(?:ткп|tkp|tkn)(?![a-zа-яё])")
_FUZZY_SNIP = re.compile(r"(?i)(?<![a-zа-яё])(?:снип|snip|chn|chip)(?![a-zа-яё])")
_FUZZY_SN = re.compile(r"(?i)(?<![a-zа-яё])(?:сн|ch)(?![иiпpnн]|ип|ip)(?![a-zа-яё0-9])")
_FUZZY_NRR = re.compile(r"(?i)(?<![a-zа-яё])(?:нрр|hrr|nrr)(?![a-zа-яё])")
_FUZZY_SP = re.compile(r"(?i)(?<![a-zа-яё0-9])(?:сп|sp)(?![a-zа-яё0-9])")
_FUZZY_TU = re.compile(r"(?i)(?<![a-zа-яё])(?:ту|tu)(?=\s*\d)")
_FUZZY_RD = re.compile(r"(?i)(?<![a-zа-яё])(?:рд|rd|pa)(?=\s*[\d(])")

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
    "сн": "СН",
    "ch": "СН",
    "нрр": "НРР",
    "hrr": "НРР",
    "nrr": "НРР",
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
    """OCR-латиница FOCT/CTB/TKP → ГОСТ/СТБ/ТКП до разбора обозначений."""
    s = text or ""
    s = _FUZZY_GOST.sub("ГОСТ", s)
    s = _FUZZY_OST.sub("ОСТ", s)
    s = _FUZZY_STB.sub("СТБ", s)
    s = _FUZZY_STP.sub("СТП", s)
    s = _FUZZY_TKP.sub("ТКП", s)
    s = _FUZZY_SNIP.sub("СНиП", s)
    s = _FUZZY_SN.sub("СН", s)
    s = _FUZZY_NRR.sub("НРР", s)
    s = _FUZZY_SP.sub("СП", s)
    s = _FUZZY_TU.sub("ТУ", s)
    s = _FUZZY_RD.sub("РД", s)
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
