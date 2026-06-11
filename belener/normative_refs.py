"""Извлечение нормативных ссылок — универсально, текст как на листе."""

from __future__ import annotations

import re
from typing import Any

from belener.normative_context import accept_by_context, fuzzy_normative_text, is_noise_span

_WB_L = r"(?<![A-Za-zА-Яа-яёЁ])"
_WB_R = r"(?![A-Za-zА-Яа-яёЁ])"

_NUM_BODY = r"[\d\s.\-–—\+]+"

# Индекс позиции перед ОСТ: 01–20, не «60» из М16х60
_LEAD_OST = r"(?:(?<![\d.\-xх×])(?P<lead>0[1-9]|1[0-9]|20)\s+)?"

# (РД …) (СО …) в ТТ
_PAREN = r"(?:\(\s*)?"

_TYPE_SPECS: list[tuple[str, str, str]] = [
    ("ОСТ", rf"{_WB_L}(?:ОСТ|OST|OCT){_WB_R}", _LEAD_OST),
    ("СТП", rf"{_PAREN}{_WB_L}(?:СТП|STP){_WB_R}", ""),
    ("РД", rf"{_PAREN}{_WB_L}(?:РД|RD){_WB_R}", ""),
    ("СО", rf"{_PAREN}{_WB_L}(?:СО|CO|SO){_WB_R}", ""),
    ("ГОСТ", rf"{_WB_L}(?:ГОСТ|GOST){_WB_R}(?:\s*(?:Р|R)\.?)?", ""),
    ("СТБ", rf"{_WB_L}(?:СТБ|STB){_WB_R}", ""),
    ("ТУ", rf"{_PAREN}{_WB_L}(?:ТУ|TU){_WB_R}", ""),
    ("СНиП", rf"{_WB_L}(?:СНиП|SNIP){_WB_R}", ""),
    ("СП", rf"{_WB_L}(?:СП|SP){_WB_R}", ""),
    ("ISO", rf"{_WB_L}ISO{_WB_R}", ""),
    ("IEC", rf"{_WB_L}IEC{_WB_R}", ""),
    ("DIN", rf"{_WB_L}DIN{_WB_R}", ""),
    ("EN", rf"{_WB_L}EN{_WB_R}", ""),
    ("API", rf"{_WB_L}API{_WB_R}", ""),
    ("ASTM", rf"{_WB_L}ASTM{_WB_R}", ""),
    ("НПБ", rf"{_WB_L}(?:НПБ|NPB){_WB_R}", ""),
    ("ВСН", rf"{_WB_L}(?:ВСН|VSN){_WB_R}", ""),
]

# Минимально полный номер с начала захвата (не жадно до конца строки)
_CLIP: dict[str, re.Pattern[str]] = {
    "ГОСТ": re.compile(
        r"^("
        r"(?:\d+\s*)?\d[\d\s.]*-\d{2,4}"  # 5264-80, 9.602-2016, 19903-74
        r")",
        re.I,
    ),
    "ОСТ": re.compile(
        r"^("
        r"\d{2}[\s.]\d[\d\s.]*-\d{2}"  # 34.10.700-97
        r"|\d+-\d+-\d{2}"  # 36-146-88
        r"|[\d\s.]+-\d{2}"  # прочие с годом из 2 цифр
        r")",
        re.I,
    ),
    "ТУ": re.compile(r"^(\d+(?:-\d+){2,})", re.I),
    "СТБ": re.compile(r"^(\d+-\d{4})", re.I),
    "СТП": re.compile(r"^(\d+(?:[\s.]\d+)+)", re.I),
    "РД": re.compile(r"^(\d+(?:[\s.]\d+)+)", re.I),
    "СО": re.compile(r"^(\d+(?:-\d+(?:\.\d+)+)+)", re.I),
    "СНиП": re.compile(r"^(\d+(?:[\s.]\d+)+)", re.I),
    "СП": re.compile(r"^(\d+(?:\.\d+)+-\d{2,4})", re.I),
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
        return bool(re.search(r"-\d{2}$", n)) and _digits_count(n) >= 7
    if kind in ("СТП", "РД", "СНиП"):
        return bool(re.search(r"\d", n)) and len(n) >= 5
    if kind == "СО":
        return "." in n and len(n) >= 8
    if kind == "ТУ":
        parts = [p for p in n.split("-") if p]
        return len(parts) >= 3 and len(parts[-1]) in (2, 4)
    if kind == "СТБ":
        return bool(re.fullmatch(r"\d+-\d{4}", n.replace(" ", "")))
    return len(n.replace(" ", "")) >= 4


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


def _ref_has_one_type(ref: str) -> bool:
    hits = re.findall(
        r"(?i)(?<![a-zа-яё])(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb)",
        ref,
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
        r"(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb)\s*(?:р\.?|r\.?)?\s*(.+)$",
        s,
        re.I,
    )
    body = _light_clean(num_m.group(1)) if num_m else s
    return re.sub(r"\D", "", body)


def _base_number_key(kind: str, ref: str) -> str:
    digits = _canonical_number(kind, ref)
    if not digits:
        return _canonical_key(kind, ref)
    if kind in ("ТУ", "СТБ", "СО"):
        return f"{kind.casefold()}:{digits}"
    base = re.sub(r"\d{2,4}$", "", digits) if len(digits) > 4 else digits
    return f"{kind.casefold()}:{base}"


def _ref_in_source_text(text: str, kind: str, ref: str) -> bool:
    blob = _light_clean(text)
    if not blob:
        return False
    ref_s = _light_clean(ref)
    if ref_s.casefold() in blob.casefold():
        return True
    num_m = re.search(
        r"(?i)(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb)\s*(?:р\.?|r\.?)?\s*(.+)$",
        ref_s,
    )
    if not num_m:
        return False
    body_digits = re.sub(r"\D", "", _light_clean(num_m.group(1)))
    if len(body_digits) < 4:
        return False
    hints = {
        "ГОСТ": r"гост|gost",
        "ОСТ": r"ост|oct|ost",
        "ТУ": r"ту|tu",
        "СТП": r"стп|stp",
    }
    hint = hints.get(kind, kind.casefold())
    low = blob.casefold()
    for m in re.finditer(rf"(?<![a-zа-яё]){hint}(?![a-zа-яё])", low):
        after = re.sub(r"\D", "", low[m.end() : m.end() + 72])
        if after.startswith(body_digits):
            nxt = after[len(body_digits) : len(body_digits) + 1]
            if not nxt or not nxt.isdigit():
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


def dedupe_normative_list(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Один канонический номер — одна строка в ответе."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in refs or []:
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "")
        key = _canonical_key(kind, ref)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _ref_vote_count(kind: str, ref: str, sources: list[str]) -> int:
    return sum(1 for src in sources if _ref_in_source_text(src, kind, ref))


def _digits_one_apart(a: str, b: str) -> bool:
    if not a or not b or len(a) != len(b):
        return False
    return sum(x != y for x, y in zip(a, b)) == 1


def resolve_single_digit_variants(
    refs: list[dict[str, str]],
    sources: list[str],
) -> list[dict[str, str]]:
    """Один символ в номере: оставить вариант с большим числом совпадений в тайлах."""
    drop: set[int] = set()
    for i, a in enumerate(refs):
        if id(a) in drop:
            continue
        ka = str(a.get("kind") or "")
        da = _canonical_number(ka, str(a.get("ref") or ""))
        if not da:
            continue
        for b in refs[i + 1 :]:
            if id(b) in drop:
                continue
            kb = str(b.get("kind") or "")
            if ka != kb:
                continue
            db = _canonical_number(kb, str(b.get("ref") or ""))
            if not _digits_one_apart(da, db):
                continue
            va = _ref_vote_count(ka, str(a.get("ref") or ""), sources)
            vb = _ref_vote_count(kb, str(b.get("ref") or ""), sources)
            if va > vb:
                drop.add(id(b))
            elif vb > va:
                drop.add(id(a))
    return [r for r in refs if id(r) not in drop]


def merge_normative_refs_from_sources(*source_texts: str) -> list[dict[str, str]]:
    """Слияние OCR по тайлам: голосование за вариант подписи на листе."""
    uniq = [str(t or "").strip() for t in source_texts if str(t or "").strip()]
    if not uniq:
        return []

    combined = "\n\n".join(uniq)
    combined_refs = extract_normative_refs(combined)
    if not combined_refs:
        return []

    variants: dict[str, list[dict[str, str]]] = {}
    for src in uniq:
        for item in extract_normative_refs(src):
            key = _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or ""))
            variants.setdefault(key, []).append(item)

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in combined_refs:
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "")
        key = _canonical_key(kind, ref)
        if not key or key in seen:
            continue
        seen.add(key)
        opts = variants.get(key, [item])
        by_ref: dict[str, dict[str, str]] = {}
        for opt in opts:
            by_ref[str(opt.get("ref") or "")] = opt
        if len(by_ref) == 1:
            out.append(next(iter(by_ref.values())))
            continue
        best_ref = max(
            by_ref.keys(),
            key=lambda r: (_ref_vote_count(kind, r, uniq), len(_light_clean(r))),
        )
        out.append(by_ref[best_ref])

    merged = dedupe_normative_year_variants(out, *uniq)
    merged = resolve_single_digit_variants(merged, uniq)
    return dedupe_normative_list(merged)


def _canonical_key(kind: str, ref: str) -> str:
    """Ключ дедупликации: тип + номер."""
    s = _light_clean(ref).casefold()
    s = re.sub(r"^\d{1}\s+(?=гост|gost)", "", s)
    s = re.sub(r"^\(\s*", "", s)
    if kind == "ОСТ":
        digits = _ost_key_digits(ref)
        if digits:
            return f"{kind.casefold()}:{digits}"
    if kind in ("ГОСТ", "ТУ", "СТБ"):
        digits = _canonical_number(kind, ref)
        if digits:
            return f"{kind.casefold()}:{digits}"
    num_m = re.search(
        r"(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so)\s*(?:р\.?|r\.?)?\s*(.+)$",
        s,
        re.I,
    )
    if not num_m:
        return _dedupe_key(ref)
    num = _light_clean(num_m.group(1))
    num = num.replace(" ", "")
    return f"{kind.casefold()}:{num}"


def _polish_normative_ref(ref: str) -> str:
    """Убрать OCR-мусор перед типом (ю ГОСТ → ГОСТ), сохранить «по ГОСТ», материал."""
    s = _light_clean(ref)
    if not s:
        return s
    m = re.search(
        r"(?i)(?:гост|gost|ост|oct|ту|tu|стп|stp|рд|rd|со|co|so|стб|stb)\s",
        s,
    )
    if not m:
        return s
    start = m.start()
    if start >= 3 and s[start - 3 : start].casefold() == "по ":
        start -= 3
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
        r"^(?!(?:0[1-9]|1[0-9]|20)\s)\d{2}\s+(?=(?:ОСТ|OST|OCT)\b)",
        "",
        out,
        flags=re.I,
    )
    return out


def _pick_better_ref(a: str, b: str) -> str:
    """Без хвоста таблицы; полнее — ближе к подписи на листе (материал, размер)."""
    la, lb = _light_clean(a), _light_clean(b)
    if la and lb:
        if la.casefold() in lb.casefold() and len(lb) > len(la):
            return b
        if lb.casefold() in la.casefold() and len(la) > len(lb):
            return a

    def score(r: str) -> tuple[int, int]:
        s = _light_clean(r)
        tail = 1 if re.search(r"\s+\d+(?:[.,]\d+)?\s*$", s) else 0
        return (tail, -len(s))

    return a if score(a) <= score(b) else b


def _ocr_loosen_normative_spacing(text: str) -> str:
    s = fuzzy_normative_text(text or "")
    s = re.sub(r"(?i)г\s*о\s*с\s*т", "ГОСТ", s)
    s = re.sub(r"(?i)(?<![a-zа-яё])о\s*с\s*т(?![a-zа-яё])", "ОСТ", s)
    s = re.sub(r"(?i)с\s*т\s*б", "СТБ", s)
    s = re.sub(r"(?i)с\s*т\s*п", "СТП", s)
    s = re.sub(r"(?i)р\s*д", "РД", s)
    s = re.sub(r"(?i)с\s*о\s*(\d)", r"СО \1", s)
    s = re.sub(r"(?i)т\s*у\s*(\d)", r"ТУ \1", s)
    s = re.sub(r"(?i)\(\s*(?:ту|tu)\s*\n+\s*", "(ТУ ", s)
    s = re.sub(r"(\d)-\s*\n+\s*(\d)", r"\1-\2", s)
    s = re.sub(r"(\d{4,})-(\d{2})\s+(\d{2})(?![\d])", r"\1-\2\3", s)
    s = re.sub(
        r"((?:ГОСТ|GOST)\s+[\d\s.\-]+-\d{2,4})\s+([A-Za-zА-Яа-я]\-[\w\-]+(?:\s+(?:ГОСТ|GOST)))",
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
    s = re.sub(
        r"((?:ОСТ|OST|OCT|ГОСТ|GOST|СТБ|STB|ТУ|СТП|РД|СО|DIN|EN|ISO|IEC))\s*\n+\s*([\d\+])",
        r"\1 \2",
        s,
        flags=re.I,
    )
    return s


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

    type_start = m.start("type")
    span_start = type_start
    if _match_lead(m):
        span_start = m.start("lead")
    elif kind == "ГОСТ":
        span_start = _material_start(text, type_start)
        dp = _digit_prefix_before_type(text, type_start)
        if dp is not None:
            span_start = min(span_start, dp)

    num_in_raw = num_raw[: len(num)] if num_raw.startswith(num.replace(" ", "")) else num
    for i in range(len(num_raw), 0, -1):
        if _light_clean(num_raw[:i]).replace(" ", "") == num.replace(" ", ""):
            num_in_raw = num_raw[:i]
            break

    end = m.start("num") + len(num_in_raw)
    ref = _light_clean(text[span_start:end])
    if not ref:
        ref = _light_clean(f"{m.group('type')} {num}")

    if kind == "ГОСТ" and re.match(r"^\d{1}\s+ГОСТ", ref, re.I):
        ref = ref.split(None, 1)[1] if " " in ref else ref

    ref = re.sub(r"[.,;:]+$", "", ref).rstrip()
    ref = re.sub(r"^\(\s*", "", ref)
    ref = re.sub(r"\)\.?$", "", ref).strip()
    if not _ref_has_one_type(ref):
        return None

    win = _window(text, span_start, end)
    if is_noise_span(win, num) or not accept_by_context(win, num, prefix=kind):
        return None
    return _polish_normative_ref(ref)


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
        if key in best:
            best[key]["ref"] = _pick_better_ref(best[key]["ref"], item["ref"])
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
                seen[key]["ref"] = _pick_better_ref(seen[key]["ref"], item["ref"])
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
                seen[key]["ref"] = _pick_better_ref(seen[key]["ref"], item["ref"])
                continue
            seen[key] = item
            order.append(key)
    return [seen[k] for k in order]
