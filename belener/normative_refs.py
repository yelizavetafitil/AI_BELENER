"""Извлечение нормативных ссылок — универсально, текст как на листе."""

from __future__ import annotations

import re
from typing import Any

from belener.normative_context import accept_by_context, fuzzy_normative_text, is_noise_span

_WB_L = r"(?<![A-Za-zА-Яа-яёЁ])"
_WB_R = r"(?![A-Za-zА-Яа-яёЁ])"
# После аббревиатуры может идти сразу цифра: ГОСТ10705-80, OCT34…
_TYPE_END = _WB_R

_NUM_BODY = r"[\d\s.\-–—\+]+"

# Индекс позиции перед ОСТ: 01–20, не «60» из М16х60
_LEAD_OST = r"(?:(?<![\d.\-xх×])(?P<lead>0[1-9]|1[0-9]|20)\s+)?"

# (РД …) (СО …) в ТТ
_PAREN = r"(?:\(\s*)?"

_TYPE_SPECS: list[tuple[str, str, str]] = [
    ("ОСТ", rf"{_WB_L}(?:ОСТ|OST|OCT){_TYPE_END}", _LEAD_OST),
    ("СТП", rf"{_PAREN}{_WB_L}(?:СТП|STP){_TYPE_END}", ""),
    ("РД", rf"{_PAREN}{_WB_L}(?:РД|RD){_TYPE_END}", ""),
    ("СО", rf"{_PAREN}{_WB_L}(?:СО|CO|SO){_TYPE_END}", ""),
    ("ГОСТ", rf"{_WB_L}(?:ГОСТ|GOST){_TYPE_END}(?:\s*(?:Р|R)\.?)?", ""),
    ("СТБ", rf"{_WB_L}(?:СТБ|STB){_TYPE_END}", ""),
    ("ТУ", rf"{_PAREN}{_WB_L}(?:ТУ|TU){_TYPE_END}", ""),
    ("СНиП", rf"{_WB_L}(?:СНиП|SNIP|СН\s*И\s*П){_TYPE_END}", ""),
    ("ТКП", rf"{_WB_L}(?:ТКП|TKP|Т\s*К\s*П){_TYPE_END}", ""),
    ("СП", rf"{_WB_L}(?:СП|SP){_TYPE_END}", ""),
    ("ISO", rf"{_WB_L}ISO{_TYPE_END}", ""),
    ("IEC", rf"{_WB_L}IEC{_TYPE_END}", ""),
    ("DIN", rf"{_WB_L}DIN{_TYPE_END}", ""),
    ("EN", rf"{_WB_L}EN{_TYPE_END}", ""),
    ("API", rf"{_WB_L}API{_TYPE_END}", ""),
    ("ASTM", rf"{_WB_L}ASTM{_TYPE_END}", ""),
    ("НПБ", rf"{_WB_L}(?:НПБ|NPB){_TYPE_END}", ""),
    ("ВСН", rf"{_WB_L}(?:ВСН|VSN){_TYPE_END}", ""),
]

# Минимально полный номер с начала захвата (не жадно до конца строки)
_CLIP: dict[str, re.Pattern[str]] = {
    "ГОСТ": re.compile(
        r"^("
        r"\d[\d\s.]*?-\d{2,4}"
        r")(?!\d)",
        re.I,
    ),
    "ОСТ": re.compile(
        r"^("
        r"\d{2}[\s.]\d[\d\s.]*-\d{2}"
        r"|\d+-\d+-\d{3,}-\d{2}"
        r"|\d+-\d+-\d{2}$"
        r"|[\d\s.]+-\d{2}"
        r")",
        re.I,
    ),
    "ТУ": re.compile(r"^(\d+(?:-\d+){2,})", re.I),
    "СТБ": re.compile(r"^(\d{3,4}-\d{4})", re.I),
    "СТП": re.compile(r"^(\d+(?:[\s.]\d+)+)", re.I),
    "РД": re.compile(r"^(\d+(?:[\s.]\d+)+)", re.I),
    "СО": re.compile(r"^(\d+(?:-\d+(?:\.\d+)+)+)", re.I),
    "СНиП": re.compile(r"^(\d+(?:[\s.]\d+)+(?:-\d{2,4})?)", re.I),
    "ТКП": re.compile(r"^(\d+(?:[\s.\-–—]\d+)+(?:-\d{2,4})?)", re.I),
    "СП": re.compile(r"^(\d+(?:[\s.\-–—]\d+)+(?:-\d{2,4})?)", re.I),
    "ISO": re.compile(r"^(\d+(?:-\d+)+)", re.I),
    "IEC": re.compile(r"^(\d+(?:-\d+)+)", re.I),
    "DIN": re.compile(r"^([\d\s.\-–—]+)", re.I),
    "EN": re.compile(r"^(\d+(?:-\d+)?)", re.I),
    "API": re.compile(r"^([\d\w.\-]+)", re.I),
    "ASTM": re.compile(r"^([A-Z]?\d+(?:[-/]\d+)*\w*)", re.I),
    "НПБ": re.compile(r"^(\d+(?:-\d+)+)", re.I),
    "ВСН": re.compile(r"^(\d+(?:-\d+)+)", re.I),
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        kind,
        re.compile(rf"{lead}(?P<type>{type_rx})\s*(?P<num>{_NUM_BODY})", re.I),
    )
    for kind, type_rx, lead in _TYPE_SPECS
]

# Материал перед ГОСТ: 25х2, 16-В, В-20 — не «5-70» (обрыв OCR)
_MAT_BEFORE = re.compile(
    r"([\d]+[xх×][\d\-–—]+|[\d]+[\-–—][А-Яа-яA-Za-z][\w\-–—]*|[А-Яа-яA-Za-z][\w\-–—]*)\s+$",
    re.I,
)


def _light_clean(raw: str) -> str:
    s = (raw or "").replace("–", "-").replace("—", "-")
    s = re.sub(r"(\d)\s+\.", r"\1.", s)
    s = re.sub(r"\.\s+(\d)", r".\1", s)
    s = re.sub(r"(\d)\s+-(\d)", r"\1-\2", s)
    return re.sub(r"\s+", " ", s.strip())


def _clip_num(raw: str, kind: str) -> str:
    s = _light_clean(raw)
    s = re.split(r"(?=[A-Za-zА-Яа-яёЁ]{2,})", s)[0].strip()
    rx = _CLIP.get(kind)
    if rx:
        m = rx.match(s)
        if m:
            s = _light_clean(m.group(1))
    return s


def _digits_count(s: str) -> int:
    return sum(1 for c in s if c.isdigit())


def _year_plausible(num: str, kind: str) -> bool:
    """Отсечь OCR-«годы» вроде 1000 или 1899 в хвосте номера."""
    n = _light_clean(num)
    m = re.search(r"-(\d{2,4})$", n)
    if not m:
        return True
    y = m.group(1)
    if len(y) == 4:
        try:
            yi = int(y)
        except ValueError:
            return False
        if kind == "ГОСТ":
            return 1950 <= yi <= 2039
        if kind in ("ТУ", "СТБ"):
            return 1900 <= yi <= 2039
        return 1900 <= yi <= 2039
    return True


def _num_complete(num: str, kind: str) -> bool:
    n = _light_clean(num)
    if not n or _digits_count(n) < 3:
        return False
    if not _year_plausible(n, kind):
        return False
    if kind == "ГОСТ":
        return bool(re.search(r"-\d{2,4}$", n))
    if kind == "ОСТ":
        if bool(re.fullmatch(r"\d{4,5}-\d{2}", n)) and "." not in n:
            return False
        return bool(re.search(r"-\d{2}$", n)) and _digits_count(n) >= 7
    if kind in ("СТП", "РД", "СНиП", "ТКП", "СП"):
        return bool(re.search(r"\d", n)) and len(n) >= 5
    if kind == "СО":
        return "." in n and len(n) >= 8
    if kind == "ТУ":
        parts = [p for p in n.split("-") if p]
        return len(parts) >= 3 and len(parts[-1]) in (2, 4)
    if kind == "СТБ":
        return bool(re.fullmatch(r"\d{3,4}-\d{4}", n.replace(" ", "")))
    return len(n.replace(" ", "")) >= 4


def format_ost_number(num: str) -> str:
    """Точки/пробелы в номере ОСТ по структуре обозначения, не под чертёж."""
    s = _light_clean(num)
    ym = re.search(r"-(\d{2})$", s)
    if not ym:
        return s
    year = ym.group(0)
    body = s[: ym.start()]
    body_sp = re.sub(r"\s+", " ", body).strip()
    body_sp = re.sub(r"^34\s+\.", "34 10.", body_sp)
    compact = re.sub(r"[\s.]", "", body)

    m108 = re.match(r"^108(\d{5})$", compact)
    if m108:
        rest = m108.group(1)
        return f"108.{rest[:3]}.{rest[3:]}{year}"

    body_norm = re.sub(r"\s*\.\s*", ".", body_sp)
    m108p = re.match(r"^108(\d{3})\.(\d{2})$", body_norm.replace(" ", ""))
    if m108p:
        return f"108.{m108p.group(1)}.{m108p.group(2)}{year}"

    m34 = re.match(r"^34\s+10\s+\.?\s*(\d{3})$", body_sp)
    if m34:
        return f"34 10.{m34.group(1)}{year}"
    m34g = re.match(r"^34(10)(\d{3})$", compact)
    if m34g:
        return f"34 10.{m34g.group(2)}{year}"

    return f"{body_norm}{year}"


def format_gost_number(num: str) -> str:
    """OCR: 94.67-75 → 9467-75; 5264 80-11 → 5264-80 (хвост строки таблицы)."""
    s = _light_clean(num)
    m = re.match(r"^(\d{2})\.(\d{2})-(\d{2,4})$", s)
    if m:
        return f"{m.group(1)}{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{4,})\s+(\d{2})-(\d{2})$", s)
    if m and m.group(2) != m.group(3):
        return f"{m.group(1)}-{m.group(2)}"
    return s


def format_stb_number(num: str) -> str:
    """OCR: 2073 2010 → 2073-2010; 097-2012 → 1097-2012 (потеря «1»)."""
    s = _light_clean(num)
    m = re.match(r"^(\d{3,4})\s+(\d{4})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    s = re.sub(r"(\d+)\s*-\s*(\d{4})", r"\1-\2", s)
    compact = re.sub(r"\s+", "", s)
    m0 = re.match(r"^0(\d{2})-(\d{4})$", compact)
    if m0:
        return f"10{m0.group(1)}-{m0.group(2)}"
    return compact


def format_tkp_number(num: str) -> str:
    """ТКП: пробелы вокруг «-» и «.» → 45-3.02-7-2005, OCR-ошибки (+5 → 45)."""
    s = _light_clean(num)
    s = re.sub(r"^\+5", "45", s)
    s = re.sub(r"\s*\.\s*", ".", s)
    s = re.sub(r"\s*-\s*", "-", s)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(\d)\s+(\d)", r"\1.\2", s)
    return re.sub(r"\s+", "", s).strip(" .-")


def highlight_patterns_for_normative_ref(ref: str) -> list[str]:
    """Строгие фразы для подсветки на листе — только с типом документа, без коротких номеров."""
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str, *, require_space_after_kind: bool = True) -> None:
        s = re.sub(r"\s+", " ", (s or "").strip())
        if len(s) < 6:
            return
        if require_space_after_kind:
            if not re.search(
                r"(?i)(?:ГОСТ|GOST|ОСТ|OST|OCT|ТУ|TU|СТБ|STB|СТП|STP|ТКП|TKP|СНиП|SNIP|СП|SP)\s",
                s,
            ):
                return
        elif not re.search(
            r"(?i)(?:ГОСТ|GOST|ОСТ|OST|OCT|ТУ|TU|СТБ|STB|СТП|STP|ТКП|TKP|СНиП|SNIP|СП|SP)",
            s,
        ):
            return
        key = re.sub(r"\s+", "", s).casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(s)

    raw = _sanitize_normative_ref((ref or "").strip())
    if not raw:
        return out
    add(raw)

    stripped = re.sub(
        r"^(?:[\w\-А-Яа-яЁё]{1,16}\s+)+"
        r"((?:ГОСТ|GOST|ОСТ|OST|OCT|ТУ|TU|СТБ|STB|СТП|STP|ТКП|TKP|СНиП|SNIP|СП|SP)\s+.+)$",
        r"\1",
        raw,
        flags=re.I,
    )
    if stripped != raw:
        add(stripped)

    kind_m = re.match(
        r"^((?:ГОСТ|GOST|ОСТ|OST|OCT|ТУ|TU|СТБ|STB|СТП|STP|ТКП|TKP|СНиП|SNIP|СП|SP))\s+(.+)$",
        stripped,
        re.I,
    )
    if kind_m:
        kind_raw, rest = kind_m.group(1), kind_m.group(2).strip()
        kind_map = {
            "GOST": "ГОСТ",
            "OST": "ОСТ",
            "OCT": "ОСТ",
            "TU": "ТУ",
            "STB": "СТБ",
            "STP": "СТП",
            "TKP": "ТКП",
            "SNIP": "СНиП",
            "SP": "СП",
        }
        kind = kind_map.get(kind_raw.upper(), kind_raw.upper())
        num = _clip_num(rest, kind)
        if num:
            if kind == "ОСТ":
                num_fmt = format_ost_number(num)
            elif kind == "ГОСТ":
                num_fmt = format_gost_number(num)
            elif kind == "СТБ":
                num_fmt = format_stb_number(num)
            else:
                num_fmt = num
            add(f"{kind} {num_fmt}")
            add(f"{kind}{num_fmt}", require_space_after_kind=False)
            add(f"({kind} {num_fmt})")
            add(f"({kind}{num_fmt})", require_space_after_kind=False)

    return out


def search_terms_for_normative_ref(ref: str) -> list[str]:
    """Совместимость: подсветка использует только строгие паттерны."""
    return highlight_patterns_for_normative_ref(ref)


def _ref_highlight_target(ref: str) -> tuple[str, str, str]:
    """(kind, canonical_number, dedupe_key) для сопоставления попаданий."""
    s = _sanitize_normative_ref(ref)
    kind_m = re.match(
        r"^((?:ГОСТ|GOST|ОСТ|OST|OCT|ТУ|TU|СТБ|STB|СТП|STP|ТКП|TKP|СНиП|SNIP|СП|SP))\s+",
        s,
        re.I,
    )
    kind = kind_m.group(1).upper() if kind_m else ""
    kind_map = {"GOST": "ГОСТ", "OST": "ОСТ", "OCT": "ОСТ", "TU": "ТУ", "STB": "СТБ", "STP": "СТП", "TKP": "ТКП", "SNIP": "СНиП", "SP": "СП"}
    kind = kind_map.get(kind, kind)
    canon = _canonical_number(kind, s) if kind else ""
    return kind, canon, _dedupe_key(s)


def _phrase_is_tight_normative_match(
    phrase: str,
    *,
    kind: str,
    canon: str,
    dedupe: str,
    max_extra_chars: int = 18,
) -> bool:
    """Фраза из соседних слов — только целевой норматив, без лишнего текста строки."""
    blob = _light_clean(phrase)
    if not blob:
        return False
    matched = [
        item
        for item in extract_normative_refs(blob)
        if item.get("kind") == kind
        and _canonical_number(kind, item.get("ref") or "") == canon
    ]
    if not matched:
        return False
    ref_len = max(len(_sanitize_normative_ref(m.get("ref") or "")) for m in matched)
    return len(blob) <= ref_len + max_extra_chars


def _phrase_matches_highlight_ref(
    phrase: str,
    *,
    kind: str,
    canon: str,
    dedupe: str,
    ref_str: str,
    max_extra_chars: int = 22,
) -> bool:
    """Совпадение для подсветки: точное или тот же номер с усечённым годом на листе."""
    if _phrase_is_tight_normative_match(
        phrase, kind=kind, canon=canon, dedupe=dedupe, max_extra_chars=max_extra_chars
    ):
        return True
    blob = _light_clean(phrase)
    if not blob or not kind:
        return False
    kind_re = {
        "ГОСТ": r"гост|gost",
        "ОСТ": r"ост|ost|oct",
        "ТУ": r"ту|tu",
        "СТБ": r"стб|stb",
        "СТП": r"стп|stp",
        "ТКП": r"ткп|tkp",
        "СНиП": r"снип|snip",
        "СП": r"сп|sp",
    }.get(kind, re.escape(kind))
    if not re.search(rf"(?<![a-zа-яё]){kind_re}(?![a-zа-яё])", blob, re.I):
        return False
    body, _year = _body_year_digits(kind, _sanitize_normative_ref(ref_str))
    if len(body) < 4:
        return False
    blob_digits = re.sub(r"\D", "", blob)
    if not blob_digits.startswith(body):
        return False
    ref_len = len(_sanitize_normative_ref(ref_str))
    return len(blob) <= ref_len + max_extra_chars


def _text_contains_normative_ref(text: str, *, kind: str, canon: str, dedupe: str) -> bool:
    blob = _light_clean(text)
    if not blob:
        return False
    for item in extract_normative_refs(blob):
        if kind and item.get("kind") != kind:
            continue
        item_kind = item.get("kind") or kind
        if _canonical_number(item_kind, item.get("ref") or "") == canon:
            return True
        if _dedupe_key(item.get("ref") or "") == dedupe:
            return True
    return False


def format_stp_number(num: str) -> str:
    s = _light_clean(num)
    compact = re.sub(r"[\s.]", "", s)
    m = re.match(r"^34(\d{2})(\d{3})$", compact)
    if m:
        return f"34.{m.group(1)}.{m.group(2)}"
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(\d)\s+(\d)", r"\1.\2", s)
    return s


def _is_noise_gost_prefix(prefix: str) -> bool:
    """«по ГОСТ», «выполнить по» — не материал/марка перед обозначением."""
    p = _light_clean(prefix)
    if not p:
        return True
    if re.fullmatch(r"по", p, re.I):
        return True
    if re.fullmatch(r"(?:по|с|в|на|для|и)", p, re.I):
        return True
    if re.search(r"\s+по\s*$", p, re.I):
        return True
    if re.fullmatch(r"[а-яё\s]+", p, re.I) and not re.search(r"[\d\-xх×]", p, re.I):
        return True
    return False


def _material_start(text: str, type_start: int) -> int:
    line_start = text.rfind("\n", 0, type_start) + 1
    chunk = text[max(line_start, type_start - 30) : type_start]
    m = _MAT_BEFORE.search(chunk)
    if m:
        mat = m.group(1)
        if re.search(r"[xх×]\d", mat, re.I) or re.match(r"[мm]\d", mat, re.I):
            return type_start
        if re.match(r"(?i)болт|гайк|шайб|труб", chunk[max(0, m.start() - 12) : m.start()] + mat):
            return type_start
        return type_start - len(m.group(0))
    return type_start


def _digit_prefix_before_type(text: str, type_start: int) -> int | None:
    """1–2 цифры перед типом (10 ГОСТ, 20 ГОСТ) — не номер колонки и не 1070."""
    line_start = text.rfind("\n", 0, type_start) + 1
    chunk = text[max(line_start, type_start - 8) : type_start]
    m = re.search(r"((?<![\d.\-xх×])(\d{1,2}))\s+$", chunk)
    if m:
        return type_start - len(m.group(1)) - 1
    return None


def _is_steel_grade_prefix(prefix: str) -> bool:
    """Ст3сп3, В-St3 — марка стали перед ГОСТ в дроби спецификации."""
    p = _light_clean(prefix)
    if not p:
        return False
    return bool(
        re.match(
            r"^(?:Ст|ST)\d+(?:сп|SP)\d+|^(?:Ст|ST)\d+(?:сп|SP)?\d*$|"
            r"^(?:В|B)[\-–—]?\d+(?:сп|SP)?\d*$",
            p,
            re.I,
        )
    )


def _ref_has_one_type(ref: str) -> bool:
    s = _light_clean(ref)
    s = re.sub(r"Ст\d+(?:сп|SP)\d+", " ", s, flags=re.I)
    hits = re.findall(
        r"(?i)(?<![a-zа-яё])(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb|"
        r"снип|snip|ткп|tkp|сп|sp|всн|нпб|iso|iec|din|en|api|astm)",
        s,
    )
    return len(hits) == 1


def _dedupe_key(ref: str) -> str:
    s = _light_clean(ref).casefold().replace(" ", "")
    return s


def _ost_key_digits(ref: str) -> str:
    s = _light_clean(ref)
    m = re.search(r"(?i)(?:ост|oct|ost)\s*(.+)$", s)
    if not m:
        return ""
    return re.sub(r"\D", "", _light_clean(m.group(1)))


def _canonical_number(kind: str, ref: str) -> str:
    if kind == "ОСТ":
        d = _ost_key_digits(ref)
        if d:
            return d
    s = _light_clean(ref).casefold()
    num_m = re.search(
        r"(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb|"
        r"снип|snip|ткп|tkp|сп|sp)\s*(?:р\.?|r\.?)?\s*(.+)$",
        s,
        re.I,
    )
    body = _light_clean(num_m.group(1)) if num_m else s
    return re.sub(r"\D", "", body)


def _base_number_key(kind: str, ref: str) -> str:
    body, year = _body_year_digits(kind, ref)
    if body:
        return f"{kind.casefold()}:{body}"
    digits = _canonical_number(kind, ref)
    if not digits:
        return _canonical_key(kind, ref)
    if kind in ("ТУ", "СТБ", "СО"):
        return f"{kind.casefold()}:{digits}"
    base = re.sub(r"\d{2,4}$", "", digits) if len(digits) > 4 else digits
    return f"{kind.casefold()}:{base}"


def _ref_in_source_text(text: str, kind: str, ref: str) -> bool:
    blob = _light_clean(_ocr_loosen_normative_spacing(text))
    if not blob:
        return False
    ref_s = _light_clean(ref)
    if ref_s.casefold() in blob.casefold():
        return True
    num_m = re.search(
        r"(?i)(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb|"
        r"снип|snip|ткп|tkp|сп|sp)\s*(?:р\.?|r\.?)?\s*(.+)$",
        ref_s,
    )
    if not num_m:
        return False
    body, year = _body_year_digits(kind, ref_s)
    if not body or len(body) < 3:
        return False
    hints = {
        "ГОСТ": r"гост|gost",
        "ОСТ": r"ост|oct|ost",
        "ТУ": r"ту|tu",
        "СТП": r"стп|stp",
        "СНиП": r"снип|snip",
        "ТКП": r"ткп|tkp",
        "СП": r"сп|sp",
    }
    hint = hints.get(kind, kind.casefold())
    low = blob.casefold()
    for m in re.finditer(rf"(?<![a-zа-яё]){hint}(?![a-zа-яё])", low):
        after = re.sub(r"\D", "", low[m.end() : m.end() + 72])
        if not after.startswith(body):
            continue
        tail = after[len(body) :]
        if year:
            if tail.startswith(year):
                return True
        elif not tail or not tail[0].isdigit():
            return True
    return False


def prune_unconfirmed_variants(
    refs: list[dict[str, str]],
    *trusted_texts: str,
) -> list[dict[str, str]]:
    """Убрать OCR-варианты года/номера, не подтверждённые PDF-текстом таблиц/ТТ."""
    trusted = "\n".join(str(t or "") for t in trusted_texts if str(t or "").strip())
    if not refs:
        return []
    if not trusted.strip():
        return list(refs or [])

    confirmed_bases: set[str] = set()
    for item in refs or []:
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "")
        if _ref_in_source_text(trusted, kind, ref):
            confirmed_bases.add(_base_number_key(kind, ref))

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in refs or []:
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "")
        key = _canonical_key(kind, ref)
        if not key or key in seen:
            continue
        base = _base_number_key(kind, ref)
        if _ref_in_source_text(trusted, kind, ref):
            out.append(item)
            seen.add(key)
            continue
        if base in confirmed_bases:
            continue
    return out


def merge_page_supplement(
    primary: list[dict[str, str]],
    page_refs: list[dict[str, str]],
    *trusted_texts: str,
) -> list[dict[str, str]]:
    """Добавить с page OCR только новые позиции, не спорящие с таблицей/ТТ."""
    if not page_refs:
        return list(primary or [])
    trusted = "\n".join(str(t or "") for t in trusted_texts if str(t or "").strip())
    have = {_canonical_key(str(x.get("kind") or ""), str(x.get("ref") or "")) for x in primary or []}
    have_bases = {_base_number_key(str(x.get("kind") or ""), str(x.get("ref") or "")) for x in primary or []}
    confirmed_bases = {
        _base_number_key(str(x.get("kind") or ""), str(x.get("ref") or ""))
        for x in primary or []
        if trusted and _ref_in_source_text(trusted, str(x.get("kind") or ""), str(x.get("ref") or ""))
    }
    extra: list[dict[str, str]] = []
    for item in page_refs or []:
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "")
        key = _canonical_key(kind, ref)
        base = _base_number_key(kind, ref)
        if not key or key in have:
            continue
        if base in have_bases or base in confirmed_bases:
            if not _ref_in_source_text(trusted, kind, ref):
                continue
        extra.append(item)
        have.add(key)
        have_bases.add(base)
    return merge_normative_refs(primary, extra)


def dedupe_normative_year_variants(
    refs: list[dict[str, str]],
    *source_texts: str,
) -> list[dict[str, str]]:
    """Один номер — одна запись: убрать OCR-варианты года (7798-71 при 7798-70 в таблице)."""
    combined = "\n".join(str(t or "") for t in source_texts if str(t or "").strip())
    if not refs:
        return []
    groups: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for item in refs:
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "")
        base = _base_number_key(kind, ref)
        if base not in groups:
            groups[base] = []
            order.append(base)
        groups[base].append(item)

    sources_list = [str(t or "") for t in source_texts if str(t or "").strip()]

    def _rank(it: dict[str, str]) -> tuple[int, int, int]:
        kind = str(it.get("kind") or "")
        ref = str(it.get("ref") or "")
        in_src = 0 if combined and _ref_in_source_text(combined, kind, ref) else 1
        votes = sum(1 for src in sources_list if _ref_in_source_text(src, kind, ref))
        ref_len = len(_light_clean(ref))
        return (in_src, -votes, -ref_len)

    out: list[dict[str, str]] = []
    for base in order:
        items = groups[base]
        if len(items) == 1:
            out.append(items[0])
            continue
        out.append(min(items, key=_rank))
    return out


def _sanitize_normative_ref(ref: str) -> str:
    """Финальная подпись для таблицы: без «по», без OCR-пробелов в номере."""
    s = _polish_normative_ref(ref)
    s = re.sub(
        r"^(?:по|в|на|с|для|и)\s+(?=(?:ГОСТ|GOST|ОСТ|OST|OCT|ТКП|TKP|"
        r"СНиП|SNIP|СП|SP|ТУ|TU|СТП|STP|РД|RD|СО|CO|SO|СТБ|STB)\b)",
        "",
        s,
        flags=re.I,
    )
    m = re.match(r"^((?:ГОСТ|GOST)\s+)(.+)$", s, re.I)
    if m:
        num = format_gost_number(m.group(2))
        s = f"{m.group(1)}{num}"
    m = re.match(r"^((?:СТБ|STB)\s+)(.+)$", s, re.I)
    if m:
        num = format_stb_number(m.group(2))
        s = f"{m.group(1)}{num}"
    m = re.match(r"^((?:ТКП|TKP)\s+)(.+)$", s, re.I)
    if m:
        num = format_tkp_number(m.group(2))
        s = f"{m.group(1)}{num}"
    return _light_clean(s)


def _ref_display_score(ref: str, *, kind: str = "") -> tuple[int, ...]:
    """Меньше — чище подпись для ответа."""
    s = _sanitize_normative_ref(ref)
    raw = _light_clean(ref)
    noise_prefix = 1 if re.match(
        r"^(?:по|в|на|с|для)\s+(?:ГОСТ|GOST|ОСТ|OST|ТКП|TKP|"
        r"СНиП|SNIP|СП|SP|ТУ|TU|СТБ|STB)\b",
        raw,
        re.I,
    ) else 0
    spaced_num = 1 if re.search(r"(?i)(?:ГОСТ|GOST)\s+\d{4,}\s+\d", raw) else 0
    tail = 1 if re.search(r"\s+\d+(?:[.,]\d+)?\s*$", s) else 0
    gq = _gost_variant_quality(s) if kind == "ГОСТ" else 0
    return (noise_prefix, spaced_num, tail, -gq, len(s))


def dedupe_normative_list(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Один канонический номер — одна строка в ответе."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in refs or []:
        kind = str(item.get("kind") or "")
        ref = _sanitize_normative_ref(str(item.get("ref") or ""))
        if not ref:
            continue
        key = _canonical_key(kind, ref)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned = dict(item)
        cleaned["ref"] = ref
        out.append(cleaned)
    return out


def _ref_vote_count(kind: str, ref: str, sources: list[str]) -> int:
    return sum(1 for src in sources if _ref_in_source_text(src, kind, ref))


def _digits_one_apart(a: str, b: str) -> bool:
    if not a or not b or len(a) != len(b):
        return False
    return sum(x != y for x, y in zip(a, b)) == 1


def _gost_one_digit_ocr_pair(a: str, b: str) -> bool:
    """27772 vs 2772 — пропуск одной цифры при OCR (не префикс по строке)."""
    if not a or not b or abs(len(a) - len(b)) != 1:
        return False
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    if longer.startswith(shorter) or shorter.startswith(longer):
        return False
    for i in range(len(longer)):
        if longer[:i] + longer[i + 1 :] == shorter:
            return True
    return False


def _body_year_digits(kind: str, ref: str) -> tuple[str, str]:
    """Цифры номера без года и год из хвоста (-75, -2012)."""
    s = _light_clean(ref)
    ym = re.search(r"-(\d{2,4})$", s)
    year = ym.group(1) if ym else ""
    num_m = re.search(
        r"(?i)(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb|"
        r"снип|snip|ткп|tkp|сп|sp)\s*(?:р\.?|r\.?)?\s*(.+)$",
        s,
    )
    num = _light_clean(num_m.group(1)) if num_m else s
    if ym:
        num = num[: num.rfind("-")].strip()
    return re.sub(r"\D", "", num), year


def _contexts_distinct(a: dict[str, str], b: dict[str, str]) -> bool:
    """Разные строки таблицы (12 … vs 13 …) — не сливать похожие номера."""
    ca = _light_clean(str(a.get("context") or a.get("ref") or ""))
    cb = _light_clean(str(b.get("context") or b.get("ref") or ""))
    if not ca or not cb or ca == cb:
        return False
    ma = re.match(r"^(\d{1,3})\b", ca)
    mb = re.match(r"^(\d{1,3})\b", cb)
    return bool(ma and mb and ma.group(1) != mb.group(1))


def resolve_number_conflicts(
    refs: list[dict[str, str]],
    sources: list[str],
) -> list[dict[str, str]]:
    """Слить OCR-варианты номера: голосование по тайлам, без подстановок."""
    drop: set[int] = set()
    for i, a in enumerate(refs):
        if id(a) in drop:
            continue
        ka = str(a.get("kind") or "")
        ba, ya = _body_year_digits(ka, str(a.get("ref") or ""))
        if not ba:
            continue
        for b in refs[i + 1 :]:
            if id(b) in drop:
                continue
            kb = str(b.get("kind") or "")
            if ka != kb:
                continue
            bb, yb = _body_year_digits(kb, str(b.get("ref") or ""))
            if not bb:
                continue
            if ya and yb and ya != yb:
                continue
            prefix = (
                (len(ba) < len(bb) and bb.startswith(ba) and 1 <= len(bb) - len(ba) <= 2)
                or (len(bb) < len(ba) and ba.startswith(bb) and 1 <= len(ba) - len(bb) <= 2)
            )
            one_digit = _digits_one_apart(ba, bb)
            gost_ocr = (
                ka == "ГОСТ"
                and ya
                and yb
                and ya == yb
                and _gost_one_digit_ocr_pair(ba, bb)
            )
            if not prefix and not one_digit and not gost_ocr:
                continue
            if one_digit and not prefix and _contexts_distinct(a, b):
                continue
            if one_digit and ka == "ОСТ" and len(ba) == len(bb):
                continue
            va = _ref_vote_count(ka, str(a.get("ref") or ""), sources)
            vb = _ref_vote_count(kb, str(b.get("ref") or ""), sources)
            if one_digit and ka == "ОСТ" and va >= 1 and vb >= 1:
                continue
            if gost_ocr and not prefix and not one_digit:
                if len(ba) > len(bb):
                    drop.add(id(b))
                elif len(bb) > len(ba):
                    drop.add(id(a))
                elif va > vb:
                    drop.add(id(b))
                elif vb > va:
                    drop.add(id(a))
                continue
            if va > vb:
                drop.add(id(b))
            elif vb > va:
                drop.add(id(a))
            elif len(bb) > len(ba):
                drop.add(id(a))
            elif len(ba) > len(bb):
                drop.add(id(b))
    return [r for r in refs if id(r) not in drop]


def resolve_single_digit_variants(
    refs: list[dict[str, str]],
    sources: list[str],
) -> list[dict[str, str]]:
    """Совместимость: делегирует в resolve_number_conflicts."""
    return resolve_number_conflicts(refs, sources)


def merge_normative_refs_from_sources(*source_texts: str) -> list[dict[str, str]]:
    """Слияние OCR по тайлам: голосование за вариант подписи на листе."""
    uniq = [str(t or "").strip() for t in source_texts if str(t or "").strip()]
    if not uniq:
        return []

    combined = "\n\n".join(uniq)
    combined_refs = extract_normative_refs(combined)

    pool: list[dict[str, str]] = list(combined_refs)
    for src in uniq:
        pool.extend(extract_normative_refs(src))

    order_keys: list[str] = []
    for item in combined_refs:
        key = _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or ""))
        if key and key not in order_keys:
            order_keys.append(key)
    for item in pool:
        key = _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or ""))
        if key and key not in order_keys:
            order_keys.append(key)

    variants: dict[str, list[dict[str, str]]] = {}
    for item in pool:
        key = _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or ""))
        variants.setdefault(key, []).append(item)

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in order_keys:
        if key in seen:
            continue
        seen.add(key)
        opts = variants.get(key, [])
        if not opts:
            continue
        kind = str(opts[0].get("kind") or "")
        by_ref: dict[str, dict[str, str]] = {}
        for opt in opts:
            by_ref[str(opt.get("ref") or "")] = opt
        if len(by_ref) == 1:
            out.append(next(iter(by_ref.values())))
            continue
        best_ref = max(
            by_ref.keys(),
            key=lambda r: (
                _ref_vote_count(kind, r, uniq),
                _dot_score(kind, r),
                _gost_variant_quality(r) if kind == "ГОСТ" else 0,
                1 if re.match(r"(?i)^(ГОСТ|GOST|ОСТ|OST|OCT|ТКП|TKP|СНиП|SNIP|СП|SP|СТБ|STB)", _light_clean(r)) else 0,
                len(_light_clean(r)),
            ),
        )
        out.append(by_ref[best_ref])

    merged = dedupe_normative_year_variants(out, *uniq)
    merged = resolve_number_conflicts(merged, uniq)
    merged = _drop_ost_one_digit_ocr_swaps(merged)
    return dedupe_normative_list(merged)


def _drop_ost_one_digit_ocr_swaps(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    """ОСТ с одной OCR-цифрой (532↔632): оставить ветку, подтверждённую другими номерами."""
    drop: set[int] = set()
    ost = [r for r in refs if str(r.get("kind") or "") == "ОСТ"]
    for i, a in enumerate(ost):
        if id(a) in drop:
            continue
        ba, _ = _body_year_digits("ОСТ", str(a.get("ref") or ""))
        if not ba:
            continue
        for b in ost[i + 1 :]:
            if id(b) in drop:
                continue
            bb, ya = _body_year_digits("ОСТ", str(b.get("ref") or ""))
            if not bb or len(ba) != len(bb) or not _digits_one_apart(ba, bb):
                continue
            fam_a = sum(1 for r in ost if ba[:6] in _ost_key_digits(str(r.get("ref") or "")))
            fam_b = sum(1 for r in ost if bb[:6] in _ost_key_digits(str(r.get("ref") or "")))
            if fam_a > fam_b:
                drop.add(id(b))
            elif fam_b > fam_a:
                drop.add(id(a))
    return [r for r in refs if id(r) not in drop]


def _canonical_key(kind: str, ref: str) -> str:
    """Ключ дедупликации: тип + номер."""
    s = _light_clean(ref).casefold()
    s = re.sub(r"^\d{1,3}\s+(?=гост|gost)", "", s)
    s = re.sub(r"^\(\s*", "", s)
    if kind == "ОСТ":
        digits = _ost_key_digits(ref)
        if digits:
            return f"{kind.casefold()}:{digits}"
    if kind in ("ГОСТ", "ТУ", "СТБ", "СНиП", "ТКП", "СП"):
        digits = _canonical_number(kind, ref)
        if digits:
            return f"{kind.casefold()}:{digits}"
    num_m = re.search(
        r"(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb|"
        r"снип|snip|ткп|tkp|сп|sp)\s*(?:р\.?|r\.?)?\s*(.+)$",
        s,
        re.I,
    )
    if not num_m:
        return _dedupe_key(ref)
    num = re.sub(r"[\s.]", "", _light_clean(num_m.group(1)))
    return f"{kind.casefold()}:{num}"


def _polish_normative_ref(ref: str) -> str:
    """Убрать OCR-мусор перед типом (ю ГОСТ → ГОСТ); «по ГОСТ» → ГОСТ."""
    s = _light_clean(ref)
    if not s:
        return s
    m = re.search(
        r"(?i)(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb|"
        r"снип|snip|ткп|tkp|сп|sp)(?:\s|\d)",
        s,
    )
    if not m:
        return s
    start = m.start()
    head = s[max(0, start - 24) : start]
    mat = re.search(
        r"(?:\d{1,2}\s+|[\d]+[xх×][\d\-–—]+\s+|[\d]+[\-–—][А-Яa-zA-Z]\s+|[A-Za-zА-Яа-я]\-[\w\-–—]+\s+)$",
        head,
        re.I,
    )
    if mat:
        start = max(0, start - 24) + mat.start()
    out = s[start:].strip()
    out = re.sub(
        r"^(?!(?:0[1-9]|1[0-9]|20)\s)\d{1,3}\s+(?="
        r"(?:ГОСТ|GOST|ОСТ|OST|OCT|ТКП|TKP|СНиП|SNIP|СП|SP|"
        r"ТУ|TU|СТП|STP|РД|RD|СО|CO|SO|СТБ|STB)\b)",
        "",
        out,
        flags=re.I,
    )
    out = re.sub(r"^по\s+", "", out, flags=re.I)
    return out


def _dot_score(kind: str, ref: str) -> int:
    if (kind or "").strip() == "ОСТ":
        return _light_clean(ref).count(".")
    return 0


def _gost_variant_quality(ref: str) -> int:
    """9467-75 лучше OCR-варианта 94.67-75 (точка внутри номера)."""
    m = re.search(r"(?i)(?:гост|gost)\s*(.+)$", _light_clean(ref))
    if not m:
        return 0
    num = m.group(1)
    if re.match(r"^\d{2}\.\d{2,}", num):
        return 0
    return 1


def _pick_better_ref(a: str, b: str, *, kind: str = "") -> str:
    """Без «пo» и OCR-пробелов; полнее — ближе к подписи на листе (материал, размер)."""
    la, lb = _sanitize_normative_ref(a), _sanitize_normative_ref(b)
    sa, sb = _ref_display_score(a, kind=kind), _ref_display_score(b, kind=kind)
    if sa < sb:
        return la or a
    if sb < sa:
        return lb or b
    if la and lb:
        if la.casefold() in lb.casefold() and len(lb) > len(la):
            return la
        if lb.casefold() in la.casefold() and len(la) > len(lb):
            return lb
    return la if len(la or "") >= len(lb or "") else lb


def _expand_stb_partial_years_in_block(block: str) -> str:
    """В перечне ТНПА OCR часто обрезает год: «СТБ 2235-20» вместо «2235-2011»."""
    full_years: list[int] = []
    for fm in re.finditer(r"(?i)(?:СТБ|STB)\s+\d{3,4}-((?:19|20)\d{2})\b", block):
        try:
            full_years.append(int(fm.group(1)))
        except ValueError:
            pass
    for fm in re.finditer(
        r"(?i)(?:^|[;\n])\s*[-–—]?\s*(\d{4})-((?:19|20)\d{2})\b",
        block,
        flags=re.M,
    ):
        try:
            full_years.append(int(fm.group(2)))
        except ValueError:
            pass

    used_years = list(full_years)

    def _next_year(yy_prefix: str) -> int | None:
        if not used_years:
            return None
        guess = max(used_years) + 1
        if 1900 <= guess <= 2039:
            used_years.append(guess)
            return guess
        return None

    def repl_stb(m: re.Match[str]) -> str:
        year = _next_year(m.group(2))
        if year is None:
            return m.group(0)
        return f"СТБ {m.group(1)}-{year}"

    block = re.sub(
        r"(?i)(?:СТБ|STB)\s+(\d{3,4})-(19|20)(?!\d)",
        repl_stb,
        block,
    )

    def repl_bare(m: re.Match[str]) -> str:
        year = _next_year(m.group(2))
        if year is None:
            return m.group(0)
        return f"\nСТБ {m.group(1)}-{year}"

    return re.sub(
        r"(?i)(?:^|[;\n])\s*[-–—]?\s*(\d{4})-(19|20)(?!\d)",
        repl_bare,
        block,
        flags=re.M,
    )


def _recover_stb_truncated_year_in_tnpa(s: str) -> str:
    """Дополняет обрезанные годы СТБ в блоках после «ТНПА» (и OCR «ТНЛА»)."""
    if not s:
        return s
    marks = list(re.finditer(r"(?i)тн[пл][аa]", s))
    if not marks:
        return s
    out: list[str] = []
    last = 0
    for m in marks:
        out.append(s[last : m.start()])
        block_start = m.start()
        block_end = min(len(s), block_start + 2500)
        out.append(_expand_stb_partial_years_in_block(s[block_start:block_end]))
        last = block_end
    out.append(s[last:])
    return "".join(out)


def _ocr_loosen_normative_spacing(text: str) -> str:
    s = fuzzy_normative_text(text or "")
    s = re.sub(r"(?i)г\s*о\s*с\s*т", "ГОСТ", s)
    s = re.sub(r"[ГгG][0OоО][СсC][ТтT]", "ГОСТ", s)
    s = re.sub(r"(?i)(?<![a-zа-яё])о\s*с\s*т(?![a-zа-яё])", "ОСТ", s)
    s = re.sub(r"0[\u0421\u0441Cc][\u0422\u0442Tt]", "ОСТ", s)
    s = re.sub(r"(\d{2})0(?:ОСТ|OST)(?=\s+\d)", r"\1 ОСТ ", s, flags=re.I)
    s = re.sub(r"(\d{1,2})\.\s*(?:ОСТ|OST)(?=\s+\d)", r"\1 ОСТ ", s, flags=re.I)
    s = re.sub(r"(?i)с\s*т\s*б", "СТБ", s)
    s = re.sub(r"(?i)(?<![a-zа-яё])сб(?![a-zа-яё])", "СТБ", s)
    s = re.sub(r"(?i)с\s*т\s*п", "СТП", s)
    s = re.sub(r"(?i)р\s*д", "РД", s)
    s = re.sub(r"(?i)с\s*о\s*(\d)", r"СО \1", s)
    s = re.sub(r"(?i)т\s*у\s*(\d)", r"ТУ \1", s)
    s = re.sub(r"(?i)\(\s*(?:ту|tu)\s*\n+\s*", "(ТУ ", s)
    s = re.sub(r"(\d)-\s*\n+\s*(\d)", r"\1-\2", s)
    s = re.sub(r"\(\d*(\d{4}-\d{4})", r"СТБ \1", s)
    s = re.sub(
        r"(?:^|\n)\s*\d+\s*(?:[\|]\s*)?(\d{4}-\d{4})\b",
        r"\nСТБ \1",
        s,
        flags=re.M,
    )
    # Перечень ТНПА: «- 2235-2011» или «2235-2011» без префикса СТБ (OCR второй строки)
    s = re.sub(
        r"(?:^|[;\n])\s*[-–—]?\s*((?:\d{4}-(?:19|20)\d{2}))\b",
        r"\nСТБ \1",
        s,
        flags=re.M,
    )
    # Перечень ТНПА: «- 10704-91» без префикса ГОСТ
    s = re.sub(
        r"(?:^|[;\n])\s*[-–—]?\s*((?:\d{4,})-\d{2})\b(?!\d)",
        r"\nГОСТ \1",
        s,
        flags=re.M,
    )
    s = re.sub(
        r"(\d{4,})-((?:19|20)\d)(\d)(?=\s|\||$)",
        r"\1-\2\3",
        s,
    )
    # ГОСТ 10704-9120| — год слеился с колонкой таблицы (не трогаем -2015 / -2001)
    s = re.sub(
        r"(\d{4,})-(\d{2})(\d{2})(?=\||\s)",
        lambda m: m.group(0)
        if m.group(2) in ("19", "20")
        else f"{m.group(1)}-{m.group(2)} {m.group(3)}",
        s,
    )
    s = re.sub(
        r"(\d{4,})-(\d{2})\s+(\d{2})(?![\d])",
        lambda m: f"{m.group(1)}-{m.group(2)}{m.group(3)}"
        if m.group(2) in ("19", "20")
        else m.group(0),
        s,
    )
    s = re.sub(
        r"(-\d{2,4})(0[1-9]|1[0-9]|20)\s+(?=(?:ОСТ|OST|OCT)\b)",
        r"\1\n\2 ",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"(-\d{2,4})(0[1-9]|1[0-9]|20)(?=(?:ОСТ|OST|OCT)\b)",
        r"\1\n\2 ",
        s,
        flags=re.I,
    )
    s = re.sub(r"(-\d{2,4})(?=(?:ГОСТ|GOST)\b)", r"\1\n", s, flags=re.I)
    s = re.sub(r"(-\d{4})(?=(?:СТП|STP)\b)", r"\1\n", s, flags=re.I)
    s = re.sub(
        r"((?:СТП|STP)\s+\d+(?:\.\d+)+)\s+(\d{1,2})(?=\s*[\.«\"])",
        r"\1\n\2",
        s,
        flags=re.I,
    )
    s = re.sub(r"(-\d{2})(?=(?:ОСТ|OST|OCT)\b)", r"\1\n", s, flags=re.I)
    s = re.sub(
        r"((?:ГОСТ|GOST)\s+[\d\s.\-]+-\d{2,4})\s*[/\\|]\s*([A-Za-zА-Яа-яЁё\d][\w\d]*(?:\s+(?:ГОСТ|GOST)))",
        r"\1\n\2",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"((?:ГОСТ|GOST)\s+[\d\s.\-]+-\d{2,4})\s+([A-Za-zА-Яа-яЁё][\w\d]*(?:\s+(?:ГОСТ|GOST)))",
        r"\1\n\2",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"((?:ГОСТ|GOST)\s+[\d\s.\-]+-\d{2,4})\s*[-—_=\.]{2,}\s*([A-Za-zА-Яа-я][\w\-]*\s+(?:ГОСТ|GOST))",
        r"\1\n\2",
        s,
        flags=re.I,
    )
    s = re.sub(r"(?i)(?<=\s)с\s+(?=СО\s+\d)", "", s)
    s = re.sub(r"(?i)(с\s*н\s*и\s*п|snip)", "СНиП", s)
    s = re.sub(r"(?i)(т\s*к\s*п|tkp)\s*\+5", r"ТКП 45", s)
    s = re.sub(r"(?i)т\s*к\s*п", "ТКП", s)
    s = re.sub(r"(?i)(гост|gost)(\d)", r"\1 \2", s)
    s = re.sub(r"(?i)(стб|stb)(\d)", r"\1 \2", s)
    s = re.sub(r"(?i)(ост|oct|ost)(\d)", r"\1 \2", s)
    # ГОСТ10705-8076х3 → год -80 и размер 76х3 (OCR слеил строки)
    s = re.sub(r"(-\d{2})(\d{2,}(?=[xх×]))", r"\1 \2", s)
    s = re.sub(
        r"((?:ОСТ|OST|OCT|ГОСТ|GOST|СТБ|STB|ТУ|СТП|STP|РД|RD|СО|CO|SO|"
        r"СНиП|SNIP|ТКП|TKP|СП|SP|DIN|EN|ISO|IEC))\s*\n+\s*([\d\+])",
        r"\1 \2",
        s,
        flags=re.I,
    )
    return _recover_stb_truncated_year_in_tnpa(s)


def _match_lead(m: re.Match[str]) -> str | None:
    if "lead" not in m.groupdict():
        return None
    return m.group("lead")


def _window(text: str, start: int, end: int, *, radius: int = 80) -> str:
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    return text[a:b]


def _ref_from_match(m: re.Match[str], text: str, kind: str) -> str | None:
    num_raw = m.group("num") or ""
    num = _clip_num(num_raw, kind)
    if not num or not _num_complete(num, kind):
        return None
    if kind == "ОСТ":
        num = format_ost_number(num)
    elif kind == "ГОСТ":
        num = format_gost_number(num)
    elif kind == "СТБ":
        num = format_stb_number(num)
    elif kind == "ТКП":
        num = format_tkp_number(num)
    elif kind in ("СТП", "РД"):
        num = format_stp_number(num)

    type_start = m.start("type")
    span_start = type_start
    if _match_lead(m):
        span_start = m.start("lead")
    elif kind == "ГОСТ":
        span_start = _material_start(text, type_start)

    num_in_raw = num_raw[: len(num)] if num_raw.startswith(num.replace(" ", "")) else num
    for i in range(len(num_raw), 0, -1):
        if _light_clean(num_raw[:i]).replace(" ", "") == num.replace(" ", ""):
            num_in_raw = num_raw[:i]
            break

    end = m.start("num") + len(num_in_raw)
    type_label = _light_clean(m.group("type") or kind)
    if kind == "ОСТ":
        ref = _light_clean(f"{type_label} {num}")
    elif kind in ("СТП", "РД"):
        ref = _light_clean(f"{type_label} {num}")
    elif kind == "ГОСТ":
        raw = _light_clean(text[span_start:end])
        mg = re.match(r"^(.*?)(?:ГОСТ|GOST)\s", raw, re.I | re.S)
        prefix = _light_clean(mg.group(1)) if mg else ""
        if prefix and (_is_noise_gost_prefix(prefix) or _is_steel_grade_prefix(prefix)):
            prefix = ""
        if prefix and len(prefix) <= 24:
            ref = _light_clean(f"{prefix} ГОСТ {num}")
        else:
            ref = _light_clean(f"ГОСТ {num}")
    else:
        ref = _light_clean(text[span_start:end])
        if not ref:
            ref = _light_clean(f"{type_label} {num}")

    if kind == "ГОСТ" and re.match(r"^\d{1,3}\s+(?:ГОСТ|GOST)", ref, re.I):
        ref = re.sub(r"^\d{1,3}\s+", "", ref, count=1)
    if kind == "ГОСТ" and re.match(r"^(?:В|B)?-?\d{1,3}\s+(?:ГОСТ|GOST)", ref, re.I):
        ref = re.sub(r"^(?:В|B)?-?\d{1,3}\s+", "", ref, count=1)
    if kind == "ОСТ" and re.match(r"^(?:0[1-9]|1[0-9]|20)\s+(?:ОСТ|OST|OCT)", ref, re.I):
        ref = re.sub(r"^(?:0[1-9]|1[0-9]|20)\s+", "", ref, count=1)

    ref = re.sub(r"[.,;:]+$", "", ref).rstrip()
    ref = re.sub(r"^\(\s*", "", ref)
    ref = re.sub(r"\)\.?$", "", ref).strip()
    if kind in ("СТП", "РД"):
        ref = re.sub(
            r"^((?:СТП|STP|РД|RD)\s+\d+(?:\.\d+)+)\s+\d{1,2}$",
            r"\1",
            ref,
            flags=re.I,
        )
    if not _ref_has_one_type(ref):
        if kind == "ГОСТ":
            ref = _sanitize_normative_ref(f"ГОСТ {num}")
        if not _ref_has_one_type(ref):
            return None

    win = _window(text, span_start, end)
    if is_noise_span(win, num) or not accept_by_context(win, num, prefix=kind):
        return None
    return _sanitize_normative_ref(ref)


def _parse_match(m: re.Match[str], text: str, kind: str) -> dict[str, str] | None:
    ref = _ref_from_match(m, text, kind)
    if not ref:
        return None
    span_start = m.start("lead") if _match_lead(m) else m.start("type")
    if kind == "ГОСТ":
        span_start = min(span_start, _material_start(text, m.start("type")))
    ctx = re.sub(r"\s+", " ", _window(text, span_start, m.end()).strip())[:160]
    return {"kind": kind, "ref": ref, "context": ctx or ref}


def extract_normative_refs(text: str) -> list[dict[str, str]]:
    """Уникальные нормативы в порядке появления в тексте."""
    if not (text or "").strip():
        return []
    text = _ocr_loosen_normative_spacing(text)
    spans: list[tuple[int, int, dict[str, str], str]] = []

    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            item = _parse_match(m, text, kind)
            if not item:
                continue
            key = _canonical_key(kind, item["ref"])
            start = m.start("lead") if _match_lead(m) else m.start("type")
            spans.append((start, m.end(), item, key))

    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    kept: list[tuple[int, int, dict[str, str], str]] = []
    for s0, e0, item, key in spans:
        if any(s1 <= s0 and e1 >= e0 and (e1 - s1) > (e0 - s0) for s1, e1, _, _ in kept):
            continue
        kept = [
            (s1, e1, it, k)
            for s1, e1, it, k in kept
            if not (s0 <= s1 and e0 >= e1 and (e0 - s0) > (e1 - s1))
        ]
        kept.append((s0, e0, item, key))

    best: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for _, _, item, key in sorted(kept, key=lambda x: x[0]):
        item_kind = str(item.get("kind") or "")
        if key in best:
            best[key]["ref"] = _pick_better_ref(
                best[key]["ref"], item["ref"], kind=item_kind
            )
            continue
        best[key] = item
        order.append(key)

    return [best[k] for k in order]


def _iter_text_blobs(drawing: dict[str, Any]) -> list[str]:
    blobs: list[str] = []
    for page in drawing.get("full_text_pages") or []:
        blobs.append(str(page.get("text") or ""))
    for key in ("table_text", "body_text", "normative_scan_text", "full_ocr_text"):
        blobs.append(str(drawing.get(key) or ""))
    for text in (drawing.get("zone_ocr_texts") or {}).values():
        blobs.append(str(text or ""))
    notes = drawing.get("sheet_notes") or {}
    blobs.append(str(notes.get("full_text") or ""))
    for sec in notes.get("sections") or []:
        blobs.append(str(sec.get("text") or ""))
    stamp = drawing.get("stamp") or {}
    raw_frame = stamp.get("raw_frame")
    if isinstance(raw_frame, dict):
        for v in raw_frame.values():
            if isinstance(v, (list, tuple)):
                for item in v:
                    blobs.append(str(item))
            else:
                blobs.append(str(v or ""))
    elif raw_frame:
        blobs.append(str(raw_frame))
    for item in stamp.get("kv") or []:
        blobs.append(str(item.get("value") or ""))
    for t in stamp.get("titles") or []:
        blobs.append(str(t or ""))
    for ln in stamp.get("other_lines") or []:
        blobs.append(str(ln or ""))
    for tbl in drawing.get("tables") or []:
        for row in tbl.get("rows") or []:
            if isinstance(row, dict):
                blobs.extend(str(v or "") for v in row.values())
    for block in drawing.get("text_blocks") or []:
        blobs.append(str(block.get("text") or ""))
    return blobs


def collect_normative_refs(drawing: dict[str, Any]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for blob in _iter_text_blobs(drawing):
        for item in extract_normative_refs(blob):
            key = _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or ""))
            if key in seen:
                seen[key]["ref"] = _pick_better_ref(seen[key]["ref"], item["ref"], kind=str(item.get("kind") or ""))
                continue
            seen[key] = item
            order.append(key)
    return [seen[k] for k in order]


def merge_normative_refs(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for group in groups:
        for item in group or []:
            key = _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or ""))
            if not key:
                continue
            if key in seen:
                seen[key]["ref"] = _pick_better_ref(seen[key]["ref"], item["ref"], kind=str(item.get("kind") or ""))
                continue
            seen[key] = item
            order.append(key)
    return [seen[k] for k in order]
