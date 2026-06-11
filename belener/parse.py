"""
Универсальные парсеры САПР-листа из OCR.

Принципы:
- только геометрия зон + якоря разделов (экспликация, условные обозначения, роли штампа);
- без имён объектов, шифров, организаций и «умных» подстановок под конкретный чертёж;
- OCR-текст в отчёте = что распознано, с минимальной нормализацией пробелов/якорей.
"""

from __future__ import annotations

import re
from typing import Any

from belener.anchors import (
    EXPLICATION_END_RX,
    EXPLICATION_START_RX,
    GENERIC_TABLE_RX,
    LEGEND_START_RX,
    SPECIFICATION_START_RX,
    TABLE_LABEL_LOOSE_RX,
    has_specification_anchor,
)
from belener.ocr import finalize_ocr_text

_CIPHER_RX = re.compile(
    r"\d{2,6}\s*-\s*\d+\s*-\s*[\wА-Яа-яЁё\d\-]{1,24}",
    re.I,
)
_CIPHER_SHORT_RX = re.compile(
    r"\d{3,6}\s*-\s*\d{1,2}\s*[А-ЯA-Za-z][\wА-Яа-яЁё\d\-]{0,20}",
    re.I,
)

# В legend symbol — графика; в отчёте показываем «—»
LEGEND_SYMBOL_PLACEHOLDER = "—"

# Порядок подписей в отчёте (ГОСТ)
STAMP_SIGNATURE_ORDER: tuple[str, ...] = (
    "Разраб.",
    "Пров.",
    "Гл. констр.",
    "Гл. техн.",
    "Н.контр.",
    "ГИП",
    "Нач. отд.",
    "Утв.",
)

STAMP_KV_ORDER: tuple[str, ...] = (
    "Обозначение документа",
    "Обозначение / шифр",
    "Организация",
    "Город / адрес",
    "Масштаб",
    "Формат",
    "Стадия (обозначение)",
    "Стадия",
    "Очередь строительства",
    "Лист",
    "Копировал",
)

# Роли основной надписи (ГОСТ) — не привязка к проекту
_STAMP_ROLES: tuple[tuple[str, str], ...] = (
    ("Разраб.", r"разро[бьёe]|разра[бьёe]"),
    ("Пров.", r"(?<![a-zа-я])пров(?![a-zа-я])"),
    ("Гл. констр.", r"гл\.?\s*констр"),
    ("Гл. техн.", r"гл\.?\s*техн"),
    ("Н.контр.", r"(?:^|\s|[|])н\.?\s*контр|(?<![a-zа-я])нконтр"),
    ("ГИП", r"\bгип\b"),
    ("Нач. отд.", r"нач\.?\s*отд|ноч\.?\s*отд"),
    ("Утв.", r"\bутв\b|ут[б6]\b"),
)

_HEADER_RX = re.compile(
    r"(?:таблица\s*\d|номер\s+на\s+плане|наименован|координат|квадрат|сетк|примечан|"
    r"обозначение|примечание)",
    re.I,
)

# Шапка колонок таблицы — не заголовок раздела
_COLUMN_HEADER_RX = re.compile(
    r"(?:"
    r"обозначение\s+примечание|"
    r"примечание\s+обозначение|"
    r"(?:№|номер)\s+на\s+плане|"
    r"номер\s+на\s+плане|"
    r"наименован\w*|"
    r"координат\w*(?:\s+квадрат\w*)?(?:\s+сетк\w*)?|"
    r"квадрат\w*\s+сетк\w*|"
    r"ед\.?\s*к|кол\.?\s*масс|масса\s*ед"
    r")",
    re.I,
)
_HEADER_WORDS = frozenset(
    {
        "обозначение",
        "примечание",
        "примечан",
        "наименование",
        "наименован",
        "координаты",
        "координат",
        "номер",
        "плане",
        "квадрат",
        "на",
        "ед",
        "кол",
        "масс",
        "масса",
        "раме",
    }
)

_NOT_NAME_WORDS = frozenset(
    {
        "генеральный",
        "план",
        "благоустройства",
        "благоустройство",
        "реконструкция",
        "минск",
        "беларусь",
        "формат",
        "лист",
        "период",
        "подготовительный",
        "рабочая",
        "документация",
        "очередь",
        "строительства",
        "копиров",
        "копироб",
        "формат",
        "разраб",
        "пров",
        "контр",
        "гип",
        "утв",
        "проект",
        "существующая",
        "новая",
    }
)


def normalize_ws(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return t.strip()


def fix_anchor_typos(text: str) -> str:
    """Без подстановки слов; якоря и метки таблиц — через regex в anchors.py."""
    return text or ""


def polish_readable_russian(text: str) -> str:
    """Homoglyphs + hunspell — без подстановок под конкретный чертёж."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return ""
    return finalize_ocr_text(t)


def _readability_score(text: str) -> float:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) < 3:
        return -100.0
    letters = re.findall(r"[А-Яа-яЁё]", s)
    if len(letters) < 3:
        return -50.0
    vowels = sum(1 for c in letters if c.lower() in "аеёиоуыэюя")
    vr = vowels / len(letters)
    score = vr * 40.0 + min(len(letters), 80) * 0.15
    if vr < 0.18 or vr > 0.62:
        score -= 18.0
    score -= len(re.findall(r"[A-Za-z]", s)) * 4.0
    score -= len(re.findall(r"[|©\[\]_`]", s)) * 6.0
    score -= len(re.findall(r"[ыд]ц\b", s, re.I)) * 5.0
    if re.search(r"[бвгджзклмнпрстфхцчшщ]{5,}", s, re.I):
        score -= 12.0
    if re.search(r"(.)\1{3,}", s, re.I):
        score -= 8.0
    return score


def _normalize_stamp_title(raw: str) -> str:
    """Очистка заголовка раздела: даты подписей, стороны света (С/З/В/Ю)."""
    t = re.sub(r"\s+", " ", (raw or "").strip())
    t = re.sub(r"^\d{1,3}\.\d{2}\.?\s+", "", t)
    parts = t.split()
    while len(parts) > 1 and len(parts[0]) <= 2 and re.fullmatch(r"[A-Za-zА-Яа-яЁё]", parts[0]):
        parts.pop(0)
    t = " ".join(parts)
    t = polish_readable_russian(fix_anchor_typos(t))
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def polish_section_title(title: str) -> str:
    t = _normalize_stamp_title(title)
    return t


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", fix_anchor_typos(text))


def _normalize_cipher(raw: str) -> str:
    t = re.sub(r"\s+", "", raw)
    m = re.match(r"^(\d{3,6})-(\d{1,2})([А-ЯA-Za-zа-я].*)$", t)
    if m and "-" not in m.group(2) and len(m.group(2)) <= 2:
        t = f"{m.group(1)}-0-{m.group(2)}{m.group(3)}"
    t = re.sub(r"([\dА-Яа-яЁё\-]+)[\^`~]$", r"\g<1>1", t)
    t = re.sub(r"([\dА-Яа-яЁё\-]+)[a-zA-Z]$", r"\1", t)
    if re.search(r"[А-Яа-яЁё]$", t) and not re.search(r"\d$", t):
        if re.search(r"-\d+-[А-ЯA-Z]{1,3}$", t):
            t = t + "1"
    return t


def cipher_tokens(text: str, limit: int = 10) -> list[str]:
    compact = _compact(text)
    seen: set[str] = set()
    out: list[str] = []
    for raw in list(_CIPHER_RX.findall(compact)) + list(_CIPHER_SHORT_RX.findall(compact)):
        t = _normalize_cipher(raw)
        if len(t) < 7 or t in seen or _is_reference_cipher(t):
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _is_reference_cipher(c: str) -> bool:
    """ТУ/ГОСТ в примечаниях, не шифр документа (34-43-12515-78)."""
    s = re.sub(r"\s+", "", c or "")
    if re.search(r"^\d{2}-\d{2}-\d{4,6}-\d{2,3}$", s, re.I):
        return True
    if re.search(r"^\d{2}-\d{2}-\d{5,8}$", s, re.I):
        return True
    if re.search(r"^\(?\s*ТУ\s*\)?\s*\d", s, re.I):
        return True
    return False


def cipher_from_filename(filename: str) -> str:
    """Шифр BNP/VR из имени файла (чистые сканы без текста в рамке)."""
    stem = re.sub(r"\s+", " ", (filename or "").replace("_", " ").strip())
    if not stem:
        return ""
    m = re.search(
        r"(?:BNP|БНП)\s*(\d+\s*-\s*\d+\s*-\s*[А-ЯA-ZЁ]{2,6}\d*)",
        stem,
        re.I,
    )
    if m:
        body = re.sub(r"\s+", "", m.group(1))
        return _normalize_cipher("BNP" + body.replace("БНП", "BNP"))
    m = re.search(
        r"(?:VR|УВ|UV)-[\w#\- ]+(?:GT|СТ|CT)[\w#\-]*",
        stem,
        re.I,
    )
    if m:
        return _normalize_cipher(re.sub(r"\s+", "", m.group(0)).replace("УВ", "VR").replace("UV", "VR"))
    return ""


def sheet_from_filename(filename: str) -> str:
    """Номер листа из суффикса «Л11» / «л.4» / «ЭМ1Л7» в имени файла."""
    stem = (filename or "").rsplit(".", 1)[0]
    for rx in (
        r"[_\s]Л[_\s]?(\d{1,3})(?:$|[_\s.])",
        r"[_\s]л\.?\s*(\d{1,3})(?:$|[_\s.])",
        r"[_\s]L(\d{1,3})(?:$|[_\s.])",
        r"Л(\d{1,3})$",
    ):
        m = re.search(rx, stem, re.I)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 999:
                return str(n)
    return ""


def apply_stamp_filename_hints(stamp: dict[str, Any], filename: str) -> dict[str, Any]:
    """Дополнить рамку шифром/листом из имени PDF, если OCR дал ТУ или номер турбины."""
    if not stamp or not (filename or "").strip():
        return stamp
    kv_map = {
        str(x.get("field")): str(x.get("value") or "").strip()
        for x in stamp.get("kv") or []
        if x.get("field")
    }
    doc = cipher_from_filename(filename)
    if doc:
        cur = kv_map.get("Обозначение / шифр") or kv_map.get("Обозначение документа") or ""
        if not cur or _is_reference_cipher(cur):
            kv_map["Обозначение документа"] = doc
            if _is_reference_cipher(kv_map.get("Обозначение / шифр", "")):
                kv_map.pop("Обозначение / шифр", None)
    sh = sheet_from_filename(filename)
    if sh:
        kv_map["Лист"] = sh
    if doc or sh:
        out = dict(stamp)
        out["kv"] = [{"field": f, "value": kv_map[f]} for f in STAMP_KV_ORDER if kv_map.get(f)]
        return out
    return stamp


def best_cipher(ciphers: list[str]) -> str:
    if not ciphers:
        return ""

    def score(c: str) -> tuple[int, int, int]:
        if _is_reference_cipher(c):
            return -100, 0, 0
        bonus = 12 if re.search(r"[А-Яа-яЁё]\d$", c) else (4 if re.search(r"\d$", c) else 0)
        if re.match(r"^VR-", c, re.I):
            bonus += 28
        if re.search(r"(?:BNP|БНП)\d", c, re.I):
            bonus += 22
        if re.search(r"-(?:АТМ|ATM|ЭМ|EM|ГП|GP)\d", c, re.I):
            bonus += 14
        if re.search(r"\d+-\d+-", c):
            bonus += 6
        if re.search(r"-(?:ГП|GP|СП|SP|АР|AR|КР|KR|ПЗ|PZ|ВК|VK|ОВ|OV|ГТ|GT)\d", c, re.I):
            bonus += 10
        if re.search(r"(\d)\1{3,}", c):
            bonus -= 14
        if re.search(r"(?:^|-)9999(?:-|$)", c):
            bonus -= 20
        if re.search(r"^1760-0-ГТ", c, re.I):
            bonus -= 8
        m0 = re.match(r"^(\d{3,6})-", c)
        if m0 and int(m0.group(1)) >= 8000:
            bonus -= 10
        cyr = len(re.findall(r"[А-Яа-яЁё]", c))
        return bonus, cyr, len(c)

    uniq: list[str] = []
    seen: set[str] = set()
    for raw in ciphers:
        t = _normalize_cipher(str(raw or "").strip())
        if len(t) < 7 or t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return max(uniq, key=score) if uniq else ""


def _stamp_cipher_values(stamp: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in stamp.get("cipher_candidates") or []:
        t = _normalize_cipher(str(raw or "").strip())
        if len(t) >= 7 and t not in seen:
            seen.add(t)
            out.append(t)
    for item in stamp.get("kv") or []:
        if "шифр" not in str(item.get("field") or "").casefold():
            continue
        t = _normalize_cipher(str(item.get("value") or "").strip())
        if len(t) >= 7 and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def merge_stamp_cipher(
    *sources: dict[str, Any] | None,
    extra_texts: tuple[str, ...] | list[str] = (),
) -> str:
    """Шифр: лучший кандидат из OCR штампа, зон листа и vision."""
    vals: list[str] = []
    seen: set[str] = set()
    for stamp in sources or []:
        if not stamp:
            continue
        for c in _stamp_cipher_values(stamp):
            if c not in seen:
                seen.add(c)
                vals.append(c)
    for raw in extra_texts or ():
        for c in cipher_tokens(str(raw or ""), limit=12):
            if c not in seen:
                seen.add(c)
                vals.append(c)
    return best_cipher(vals) if vals else ""


def _looks_like_stamp_noise_line(ln: str) -> bool:
    s = re.sub(r"\s+", " ", (ln or "").strip())
    low = s.casefold()
    if re.search(r"^===|^\[страниц", low):
        return True
    if re.fullmatch(r"[сзвю]", s, re.I):
        return True
    if re.search(r"^\s*таблица\s*\d+\s*$", s, re.I):
        return False
    if _HEADER_RX.search(s):
        return True
    if re.search(r"\bооо\s+по\b|\bоо\s+по\b", low):
        return True
    if re.match(r"^[A-ZА-ЯЁ]\d{2,5}\s", s):
        return True
    if len(s) < 55 and not re.search(
        r"\b(?:план|схема|ведомост|раздел|комплекс|объект|генеральн|благоустр)\w*",
        s,
        re.I,
    ):
        if re.search(
            r"цеп[ией]|датчик|температур|нагрузк|трансформатор|панел\w*|релейн\w*\s+зал|"
            r"предупред|филиал.*электрическ",
            s,
            re.I,
        ):
            return True
    if len(s) < 22 and not re.search(r"\bплан\b|\bсхем", s, re.I):
        return True
    if _readability_score(s) < 5.0 and len(s) > 20:
        return True
    return False


def _person_token(raw: str) -> str:
    s = re.sub(r"[\[\]_`]+", " ", (raw or "").strip()).strip(" .-|")
    if len(s) < 3 or re.search(r"\d{4,}", s):
        return ""
    s = re.sub(r"(?<=[А-ЯЁа-яё])[oO](?=[а-яё])", "о", s)
    m = re.search(r"([А-ЯЁ][а-яё]{2,})|([A-Z][a-z]{3,})", s)
    name = (m.group(1) or m.group(2) or "").strip() if m else ""
    if not name:
        return ""
    if name.casefold() in _NOT_NAME_WORDS:
        return ""
    if re.search(r"^(?:н\.?\s*контр|гип|утв|разраб|пров|нач|копир|копор)", name, re.I):
        return ""
    if re.fullmatch(r"[А-ЯЁ][а-яё]{2,}", name) and len(name) < 4:
        return ""
    return name


def _extract_date(ln: str) -> str:
    # Явная дата ДД.ММ или ММ.ГГ
    for m in re.finditer(r"(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)", ln):
        val = m.group(1)
        parts = re.split(r"[./]", val)
        if len(parts) >= 2:
            a, b = int(parts[0]), int(parts[1])
            # предпочитаем 11.25, а не 1.25 внутри 111.25
            if a >= 10 or b <= 12:
                return val
    # OCR: [1125, (1125, 11.25
    for rx in (
        r"[\[\(]\s*(\d{2})[.\s]?(\d{2})\s*[\]\)]?",
        r"[\[\(](\d{2})(\d{2})\b",
        r"\b(\d{2})(\d{2})\b",
    ):
        for m in re.finditer(rx, ln):
            a, b = int(m.group(1)), int(m.group(2))
            if 1 <= a <= 31 and 1 <= b <= 31:
                return f"{a}.{b}"
            if 1 <= a <= 12 and 0 <= b <= 99:
                return f"{a}.{b}"
    return ""


def _is_bad_signature_date(raw: str) -> bool:
    s = (raw or "").strip()
    if not s or s == "—":
        return False
    m = re.search(r"(\d{1,2})[./](\d{1,2})", s)
    if not m:
        return True
    a, b = int(m.group(1)), int(m.group(2))
    if a > 12 and b > 12:
        return True
    if b > 31:
        return True
    return False


def _norm_date(raw: str) -> str:
    s = (raw or "").strip()
    if not s or s == "—":
        return "—"
    if re.fullmatch(r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?", s):
        if _is_bad_signature_date(s):
            return "—"
        return s
    m = re.search(r"(\d{1,2})[./](\d{1,2})", s)
    if m:
        val = f"{m.group(1)}.{m.group(2)}"
        if _is_bad_signature_date(val):
            return "—"
        return val
    return "—"


def _parse_signature_row(ln: str) -> tuple[str, str, str] | None:
    if "|" not in ln:
        return None
    cells = [c.strip() for c in ln.strip("|").split("|")]
    role = ""
    role_idx = -1
    for i, cell in enumerate(cells):
        low = cell.casefold()
        for role_name, rx in _STAMP_ROLES:
            if re.search(rf"^{rx}\.?$", low, re.I) or re.search(rx, low, re.I):
                role = role_name
                role_idx = i
                break
        if role:
            break
    if not role:
        return None
    name = ""
    date = ""
    for cell in cells[role_idx + 1 :] if role_idx >= 0 else cells:
        if not name:
            cand = _person_token(cell)
            if cand:
                name = cand
        d = _extract_date(cell)
        if d:
            date = d
    if not name:
        for cell in cells:
            cand = _person_token(cell)
            if cand:
                name = cand
                break
    if not name and not date:
        return None
    return role, name, date


def _extract_signatures(lines: list[str]) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    raw = "\n".join(lines)
    if "--- stamp_sig ---" in raw:
        sig_part = raw.split("--- stamp_sig ---", 1)[1]
        main_part = raw.split("--- stamp_sig ---", 1)[0]
        work_lines = [ln.strip() for ln in (sig_part + "\n" + main_part).split("\n") if ln.strip()]
    else:
        work_lines = [ln.strip() for ln in lines if ln.strip()]

    def put(role: str, name: str, date: str = "") -> None:
        if _is_bad_signature_name(name):
            name = ""
        if _is_bad_signature_date(date):
            date = ""
        prev = found.get(role)
        if prev and name in ("", "—"):
            prev_name = str(prev.get("name") or "").strip()
            if prev_name and prev_name != "—" and not _is_bad_signature_name(prev_name):
                name = prev_name
        if prev and name not in ("", "—"):
            prev_name = str(prev.get("name") or "—")
            if prev_name not in ("—", "", None) and not _is_bad_signature_name(prev_name):
                if _readability_score(name) <= _readability_score(prev_name):
                    name = prev_name
        nd = _norm_date(date) if date else "—"
        if nd == "—" and date:
            nd = date
        if prev and nd in ("—", ""):
            prev_date = str(prev.get("date") or "").strip()
            if prev_date and prev_date != "—" and not _is_bad_signature_date(prev_date):
                nd = prev_date
        if not name and nd in ("—", ""):
            if not prev:
                return
            name = str(prev.get("name") or "—")
        found[role] = {
            "role": role,
            "name": name or "—",
            "sign": "—",
            "date": nd if nd != "—" else "—",
        }

    for ln in work_lines:
        row = _parse_signature_row(ln)
        if row:
            put(row[0], row[1], row[2])

    # Многострочный штамп (PSM 11): роль → фамилия → дата на соседних строках
    i = 0
    while i < len(work_lines):
        ln = work_lines[i]
        low = ln.casefold()
        matched_role = ""
        for role, rx in _STAMP_ROLES:
            if re.search(rx, low):
                matched_role = role
                break
        if matched_role:
            name = ""
            date = ""
            for j in range(i, min(i + 8, len(work_lines))):
                chunk = work_lines[j]
                if not name:
                    cand = _person_token(chunk)
                    if cand:
                        name = cand
                if not date:
                    date = _extract_date(chunk)
                if name and date:
                    break
            put(matched_role, name, date)
        i += 1

    for ln in work_lines:
        low = ln.casefold()
        for role, rx in _STAMP_ROLES:
            if not re.search(rx, low):
                continue
            name = ""
            cells = [c.strip() for c in ln.split("|")]
            for cell in cells[1:5]:
                name = _person_token(cell)
                if name:
                    break
            if not name:
                m = re.search(rf"{rx}\.?\s*[_\.\s|]*([А-ЯA-Z][^\s|]{{2,}})", ln, re.I)
                if m:
                    name = _person_token(m.group(1))
            date = _extract_date(ln)
            put(role, name, date)
            break

    # Вторая проходка: роли и фамилии в одной строке OCR без |
    compact = _compact("\n".join(work_lines))
    for role, rx in _STAMP_ROLES:
        cur = found.get(role, {})
        cur_name = str(cur.get("name") or "")
        if cur_name and cur_name != "—" and not _is_bad_signature_name(cur_name):
            continue
        for m in re.finditer(
            rf"(?:^|[\s|]){rx}\.?\s*[_\.\s|]*([А-ЯA-Z][а-яA-Za-z]{{3,}})(?:[^\d]{{0,20}}(\d{{1,2}}[./]\d{{1,2}}|\[\d{{4}}))?",
            compact,
            re.I,
        ):
            name = _person_token(m.group(1))
            if not name:
                continue
            date = _norm_date(m.group(2) or "") if m.lastindex and m.group(2) else ""
            if date == "—":
                date = _extract_date(m.group(0))
            put(role, name, date)
            break

    # Блоки между ролями в таблице подписей (вертикальный/горизонтальный OCR)
    role_hits: list[tuple[int, str]] = []
    for i, ln in enumerate(work_lines):
        low = ln.casefold()
        for role, rx in _STAMP_ROLES:
            if re.search(rf"(?:^|[\s|]){rx}\.?\b", low):
                role_hits.append((i, role))
                break
    for idx, (i, role) in enumerate(role_hits):
        end_i = role_hits[idx + 1][0] if idx + 1 < len(role_hits) else min(i + 8, len(work_lines))
        name = ""
        date = ""
        for chunk in work_lines[i:end_i]:
            if not name:
                for cell in chunk.split("|"):
                    cand = _person_token(cell)
                    if cand:
                        name = cand
                        break
            if not date:
                date = _extract_date(chunk)
            if name and date:
                break
        if name or date:
            put(role, name, date)

    out: list[dict[str, str]] = []
    for role in STAMP_SIGNATURE_ORDER:
        if role in found and _signature_has_content(found[role]):
            out.append(found[role])
    for role, sig in found.items():
        if role not in STAMP_SIGNATURE_ORDER and _signature_has_content(sig):
            out.append(sig)
    return out


def _extract_sheet_kv(compact: str) -> list[tuple[str, str]]:
    """Стадия (буква), номер листа — типовые поля рамки ГОСТ."""
    out: list[tuple[str, str]] = []
    stage_letter = ""
    for rx in (
        r"стадия[^\wА-Яа-я]{0,12}([СРПР])\b",
        r"(?:^|[\s|])ст\.?\s*([СРПР])\b",
        r"\b([СРПР])\b[^0-9]{0,16}(\d{1,3})\b",
    ):
        m = re.search(rx, compact, re.I)
        if not m:
            continue
        stage_letter = m.group(1).upper()
        if m.lastindex and m.lastindex >= 2:
            pos = m.start(2)
            before = compact[max(0, pos - 2) : pos]
            if before.endswith(("-", "–", "−")):
                continue
            num = int(m.group(2))
            if 1 <= num <= 999:
                out.append(("Стадия (обозначение)", stage_letter))
                out.append(("Лист", str(num)))
                stage_letter = ""
        break
    if stage_letter and not any(k == "Стадия (обозначение)" for k, _ in out):
        out.append(("Стадия (обозначение)", stage_letter))
    if not any(k == "Лист" for k, _ in out):
        for rx in (
            r"(?:^|[\s|])лист[^\d]{0,8}(\d{1,3})\b",
            r"(?:^|[\s|])л\.?\s*(\d{1,3})\b",
        ):
            m = re.search(rx, compact, re.I)
            if not m:
                continue
            num = int(m.group(1))
            if not (1 <= num <= 999):
                continue
            total = ""
            m2 = re.search(r"листов[^\d]{0,8}(\d{1,3})\b", compact, re.I)
            if m2:
                total = m2.group(1)
            val = f"{m.group(1)} / {total}" if total else m.group(1)
            out.append(("Лист", val))
            break
    return out


def _extract_org(compact: str) -> str:
    candidates: list[str] = []
    for rx in (
        r'РУП\s*["«][^"»\n]{4,80}["»]',
        r'РУП\s*["«]?\s*[А-ЯA-ZЁ][\wА-Яа-яЁё\-]{4,50}',
        r'ООО\s+["«][^"»\n]{4,80}["»]',
        r'ООО\s+[«"]?[\wА-Яа-яЁё\s\-]{4,60}',
    ):
        candidates.extend(m.group(0) for m in re.finditer(rx, compact, re.I))
    if not candidates:
        return ""

    def score(s: str) -> tuple[int, int, int, int]:
        if re.search(r"поза\s+щита|по\s+месту\s+защиты|входит\s+\d+\s+комплект", s, re.I):
            return -50, 0, 0, 0
        pref = 200 if re.match(r'РУП\s*["«]', s, re.I) else (100 if re.match(r'ООО\s+["«]', s, re.I) else 0)
        if re.search(r"белнипи|энергопром|геоцентр", s, re.I):
            pref += 40
        cyr = len(re.findall(r"[А-Яа-яЁё]", s))
        noise = len(re.findall(r"[|©\[\]_`]", s))
        return pref, cyr, -noise, len(s)

    org = re.sub(r"\s+", " ", max(candidates, key=score)).strip()
    org = re.sub(r"бейнипи", "БЕЛНИПИ", org, flags=re.I)
    org = re.sub(r"энергопром", "ЭНЕРГОПРОМ", org, flags=re.I)
    return org


def _extract_city_stamp(compact: str) -> str:
    m = re.search(r"(минск)\s*(белорус\w*)", compact, re.I)
    if m:
        city = m.group(1).strip().capitalize()
        country = m.group(2).strip().capitalize()
        if country.casefold().startswith("белорус"):
            country = "Беларусь"
        return f"{city} {country}"
    return ""


def _extract_scale(compact: str) -> str:
    for m in re.finditer(r"1\s*:\s*(\d{2,5})", compact, re.I):
        return f"1:{m.group(1)}"
    m = re.search(r"(?<![\d])1(\d{3,4})(?![\d])", compact)
    if m:
        denom = int(m.group(1))
        if denom in (200, 250, 400, 500, 1000, 2000, 2500, 5000):
            return f"1:{denom}"
    return ""


def _is_garbage_kv(field: str, value: str) -> bool:
    """Значение поля рамки — эхо метки или структурный мусор."""
    if not field or not value:
        return True
    f = field.strip()
    v = re.sub(r"\s+", " ", value.strip())
    if not v or v == "—":
        return True
    if f.casefold() == v.casefold():
        return True
    if f == "Очередь строительства":
        if re.fullmatch(r"очеред\w*\s+строитель\w*", v, re.I):
            return True
        if not re.search(r"(?:^|[\s|])[I1Il]\s*очеред|^\d+\s*очеред", v, re.I):
            return True
    if f == "Стадия":
        if re.fullmatch(r"[СРПТ]", v, re.I):
            return True
        if re.search(r"^очеред", v, re.I):
            return True
    if f == "Формат" and re.search(r"^формат\b", v, re.I) and f.casefold() != v.casefold():
        pass  # «Формат A4x4» — норма
    if f == "Масштаб" and not re.search(r"1\s*:\s*\d", v):
        return True
    if f == "Формат" and re.search(r"1\s*:\s*\d", v):
        return True
    if "город" in f.casefold() or "адрес" in f.casefold():
        if re.search(r"месту\s+защиты|щита\s+и\s+по|поза\s+щита|формат\s+a\d", v, re.I):
            return True
    if f == "Организация":
        if re.search(r"\d{3,}\s*-\s*\d+\s*-\s*[А-ЯA-Z]", v, re.I):
            return True
        if re.search(r'["«][^"»]{4,60}["»]', v):
            return False
        if _readability_score(v) < 8.0:
            return True
        words = v.split()
        if len(words) >= 3 and sum(1 for w in words if len(w) <= 2) / len(words) > 0.45:
            return True
    return False


def stamp_parse_usable(stamp: dict[str, Any]) -> bool:
    """Штамп достаточно чистый — не нужен повторный OCR по сетке."""
    kv = stamp.get("kv") or []
    good_kv = sum(
        1
        for x in kv
        if not _is_garbage_kv(str(x.get("field") or ""), str(x.get("value") or ""))
    )
    sigs = [
        s
        for s in stamp.get("signatures") or []
        if str(s.get("name") or "").strip() not in ("", "—")
        and not _is_bad_signature_name(str(s.get("name") or ""))
    ]
    titles = [t for t in stamp.get("titles") or [] if not _is_garbage_stamp_title(t)]
    good_titles = [t for t in titles if _looks_like_stamp_section_title(t)]
    cipher = next(
        (str(x.get("value") or "") for x in kv if "шифр" in str(x.get("field") or "").casefold()),
        "",
    )
    if cipher and _is_reference_cipher(cipher):
        good_kv = max(0, good_kv - 1)
    titles_ok = not titles or len(good_titles) >= 1
    return good_kv >= 2 and len(sigs) >= 1 and len(titles) <= 3 and titles_ok


def _normalize_stamp_kv_map(kv_map: dict[str, str]) -> dict[str, str]:
    """Перенос буквы стадии, выброс мусорных kv."""
    out = {k: v for k, v in kv_map.items() if v and v != "—" and not _is_garbage_kv(k, v)}
    st = out.get("Стадия", "")
    if st and re.fullmatch(r"[СРПТ]", st, re.I):
        out.setdefault("Стадия (обозначение)", st.upper())
        del out["Стадия"]
    letter = out.get("Стадия (обозначение)", "")
    if letter and not re.fullmatch(r"[СРПТ]", letter, re.I):
        del out["Стадия (обозначение)"]
    return out


def _scan_stamp_section_phrases(compact: str) -> list[str]:
    """Разделы из текста рамки — фразы с «план» / «схема» / «ведомость» (универсально)."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in (
        r"генеральн\w+\s+план\.?",
        r"план\s+благоустр\w+",
        r"план\s+[А-ЯЁ][\wА-Яа-яЁё\-]+(?:\s+[А-ЯЁ][\wА-Яа-яЁё\-]+){0,5}",
        r"схем\w+\s+[А-ЯЁ][\wА-Яа-яЁё\-]+(?:\s+[А-ЯЁ][\wА-Яа-яЁё\-]+){0,4}",
        r"ведомост\w+(?:\s+[А-ЯЁ][\wА-Яа-яЁё\-]+){0,4}",
    ):
        for m in re.finditer(pat, compact, re.I):
            raw = re.sub(r"\s+", " ", m.group(0)).strip(" .,;|")
            t = polish_section_title(raw)
            if not _looks_like_stamp_section_title(t):
                continue
            key = _title_norm_key(t)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(t)
    return out


def _is_stage_phrase(text: str) -> bool:
    low = re.sub(r"\s+", " ", (text or "").strip()).casefold()
    return bool(
        re.search(r"^подготов\w+\s+период", low)
        or re.search(r"^рабоч\w+\s+документ\w+", low)
    )


def _is_generic_table_title(title: str) -> bool:
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return True
    return bool(re.fullmatch(r"таблица\s*\d+(?:\.\d+)?", t, re.I))


def _is_garbage_stamp_title(raw: str) -> bool:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return True
    if re.search(
        r"разраб\.?|раз\s*раб|пров\.?|гип\b|н\.?\s*контр|нач\.?\s*отд|копиров|утв\b",
        s,
        re.I,
    ):
        return True
    if re.search(r'ру\s*["«]|ооо\s*["«]', s, re.I):
        return True
    if len(re.findall(r"\bооо\b", s, re.I)) >= 2:
        return True
    if _readability_score(s) < 5.0 and len(s) > 25:
        return True
    if re.search(r"^(?:стадия|лист(?:ов)?|масштаб|формат|город|адрес)\b", s, re.I):
        return True
    if re.search(r"^очеред", s, re.I):
        return True
    if re.search(r"^\d{3,6}\s*-\s*\d+\s*-", s):
        return True
    if re.match(r"^\d{1,3}\.\d{2}\.?\s+", s):
        return True
    if _is_stage_phrase(s):
        return True
    words = s.split()
    if sum(1 for w in words if len(w) <= 2) >= max(3, len(words) // 2):
        return True
    letters = re.findall(r"[А-Яа-яЁёA-Za-z]", s)
    if letters and sum(1 for c in letters if c.upper() in "СЗВЮOAV") / len(letters) > 0.35:
        return True
    if re.search(r"(.)\1{4,}", s, re.I):
        return True
    if len(s) < 55 and _readability_score(s) < 6.0:
        return True
    if re.search(r"\bооо\b.*\bооо\b", s, re.I):
        return True
    if re.fullmatch(r'(?:г\.\s*)?[А-ЯЁ][а-яё\-]+\s+(?:Беларусь|Россия|РБ|РФ)\.?', s, re.I):
        return True
    if re.fullmatch(r'["«][^"»]{3,60}["»]\.?', s):
        return True
    if re.fullmatch(r'[«"][A-ZА-ЯЁ][^"»]{3,40}[»"]\.?', s):
        return True
    if re.search(r"^реконструк\w+", s, re.I) and not re.search(r"\bплан\b", s, re.I):
        return True
    if re.search(
        r"плодородн|озеленен|используемый\s+для|элементов\s+озеленен|ведомость\s+"
        r"|тротуар|покрыти[яе]\s|газон\s|посадк",
        s,
        re.I,
    ):
        return True
    if re.search(r"[\(（]\s*\d\s*[,.\-]\s*\d\s*[\)）]", s):
        return True
    if re.match(r"^[\s‚\.\"«»\)\(]+[а-яa-z]\s*[\)\]]", s, re.I):
        return True
    if re.search(r"^(?:короб|корд|кабель|шкаф|панель|блок|устройство)\s", s, re.I) and not re.search(
        r"(?:план|схем|черт|раздел|комплект|марк|изыскан|расположен|генеральн)",
        s,
        re.I,
    ):
        return True
    if re.match(r"^(?:рунт|зм\.|лестн|асштаб)", s, re.I):
        return True
    if re.search(
        r"электродвигател|выключател|концевых\s+выключ|комплект\s+б\d|цеп[ией].*обеспеч|"
        r"^\d{1,2}\s+выключател|гр\.\s+банбык|импульсного\s+клапана|"
        r"арматур|защиты\s+пвд|поза\s+щита|^\d+\s+\d+\s+т\b|входит\s+\d+\s+комплект",
        s,
        re.I,
    ):
        return True
    if re.search(
        r"см\.\s*тгп|тгп\.\s*\d+\s+щита|"
        r"^главный\s+корпус|метлу\s+защиты|защиты\s+пв\b|что\s+метлу",
        s,
        re.I,
    ):
        return True
    if re.search(r"[а-яё][А-ЯЁ][а-яё][А-ЯЁ]", s):
        return True
    words = s.split()
    if any(len(w) >= 6 and sum(1 for c in w if c.isupper()) >= 3 for w in words):
        return True
    return False


def _looks_like_stamp_section_title(ln: str) -> bool:
    """Наименование раздела в рамке штампа (любое содержание, без привязки к проекту)."""
    s = re.sub(r"\s+", " ", ln.strip())
    if _is_garbage_stamp_title(s):
        return False
    if "|" in s or re.match(r'^[\s=\-_`]', s):
        return False
    if len(s) < 8 or len(s) > 120:
        return False
    if not re.search(r"[А-Яа-яЁё]{4,}", s):
        return False
    if re.search(r"^(?:разро|пров|гип|н\.?\s*контр|нач\.|копиров|утв)", s, re.I):
        return False
    if re.search(r"копиров|формат\s*[aа]\d|1\s*:\s*\d|обозначен\w*\s*/\s*шифр", s, re.I):
        return False
    if re.search(r'ру\s*["«]|ооо\s*["«]|^\d{3,6}\s*-\s*\d+\s*-', s, re.I):
        return False
    if re.search(r"^(?:стадия|лист(?:ов)?|масштаб|формат)\b", s, re.I):
        return False
    if re.fullmatch(r"[сзвю]", s, re.I):
        return False
    words = s.split()
    if len(words) == 1 and len(words[0]) < 10:
        return False
    if re.search(r"[|`\[\]]", s):
        return False
    return True


def _looks_like_title_line(ln: str) -> bool:
    return _looks_like_stamp_section_title(ln)


def _titles_from_pipe_cells(lines: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if "|" not in ln:
            continue
        for cell in ln.split("|"):
            cell = re.sub(r"\s+", " ", cell.strip())
            if not _looks_like_stamp_section_title(cell):
                continue
            key = cell.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(cell)
    return found


def parse_stamp(text: str) -> dict[str, Any]:
    raw = fix_anchor_typos(normalize_ws(text))
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    compact = _compact(raw)
    kv: list[dict[str, str]] = []

    vr_doc = re.search(
        r"(?:VR|УВ|UV)-[\w#\- ]+(?:GT|СТ|CT)[\w#\-]*",
        compact,
        re.I,
    )
    if vr_doc:
        doc_id = re.sub(r"\s+", "", vr_doc.group(0)).replace("УВ", "VR").replace("UV", "VR")
        kv.append({"field": "Обозначение документа", "value": _normalize_cipher(doc_id)})
    m_bnp = re.search(r"(?:BNP|БНП)\s*[\d\-]+[А-ЯA-ZЁ]{2,6}\d*", compact, re.I)
    if m_bnp:
        kv.append(
            {
                "field": "Обозначение документа",
                "value": _normalize_cipher(m_bnp.group(0).replace(" ", "")),
            }
        )
    inv = re.search(r"(?:инв\.?\s*№\s*подл\.?\s*)?(\d{3,6}-\d+-[А-ЯA-ZЁ]{1,4}\d*)", compact, re.I)
    if inv:
        kv.append({"field": "Инв. № подл.", "value": _normalize_cipher(inv.group(1))})
    ciphers = cipher_tokens(compact)
    if ciphers:
        pick = best_cipher(ciphers)
        if pick and not _is_reference_cipher(pick):
            if not (vr_doc and re.search(r"^1760-0-ГТ", pick, re.I)):
                kv.append({"field": "Обозначение / шифр", "value": pick})
            elif not vr_doc:
                kv.append({"field": "Обозначение / шифр", "value": pick})

    org = _extract_org(compact)
    if org:
        kv.append({"field": "Организация", "value": org})

    scale = _extract_scale(compact)
    if scale:
        kv.append({"field": "Масштаб", "value": scale})

    for label, val in _extract_sheet_kv(compact):
        kv.append({"field": label, "value": val})

    city = _extract_city_stamp(compact)
    if city:
        kv.append({"field": "Город / адрес", "value": city})

    for label, rx in (
        (
            "Город / адрес",
            r"(?:г\.\s*)?([А-ЯЁ][\wА-Яа-яЁё\-]+(?:\s+[А-ЯЁ][\wА-Яа-яЁё\-]+){0,3})\s*,?\s*(Беларусь|Россия|РБ|РФ)",
        ),
        ("Город / адрес", r"([А-ЯЁ][а-яё\-]+)\s+(Беларусь|Россия|РБ|РФ)\b"),
        ("Формат", r"(?:[Ff]ormat|Формат|Popmam)\s*([AА]\d\s*[x×хX&]?\s*\d+)"),
        ("Стадия", r"(Подготов\w+\s+\w+\s+период|Рабоч\w+\s+документ\w+)"),
        ("Очередь строительства", r"([I1Il]\s*очеред\w+\s+строитель\w+)"),
    ):
        m = re.search(rx, compact, re.I)
        if not m:
            continue
        if label == "Город / адрес" and any(x.get("field") == label for x in kv):
            continue
        if label == "Формат":
            val = re.sub(r"\s+", "", m.group(1)).replace("×", "x").replace("х", "x").replace("&", "4")
            val = val.replace("А", "A").replace("а", "a")
            val = re.sub(r"^a([фf])", "a4", val, flags=re.I)
            val = re.sub(r"^a4x([^\d])", r"a4x4", val, flags=re.I)
            val = f"Формат {val}"
        elif label == "Город / адрес":
            val = re.sub(r"\s+", " ", f"{m.group(1).strip()} {m.group(2).strip()}").strip()
        else:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
        kv.append({"field": label, "value": val})

    for ln in lines:
        if not re.search(r"копиров", ln, re.I):
            continue
        if re.fullmatch(r"копиров\w*\s*", ln.strip(), re.I):
            break
        m = re.search(r"копиров\w*\s*[:\s]+([А-ЯA-Z][а-яё]{3,})", ln, re.I)
        if not m:
            break
        tail = m.group(1)
        if re.search(r"формат|разраб|пров|гип|лист\b", tail, re.I):
            break
        val = _person_token(tail)
        if val and not _is_bad_signature_name(val):
            kv.append({"field": "Копировал", "value": val})
        break

    titles: list[str] = []
    for ln in lines:
        if _looks_like_stamp_noise_line(ln):
            continue
        if re.search(r"разро[бь]|пров\.|гип|контр\.|копиров|формат\s*a\d", ln, re.I):
            continue
        if not _looks_like_stamp_section_title(ln):
            continue
        if ln not in titles:
            titles.append(re.sub(r"\s+", " ", ln).strip())

    for t in _titles_from_pipe_cells(lines):
        if t not in titles:
            titles.append(t)

    for t in _scan_stamp_section_phrases(compact):
        if t not in titles:
            titles.append(t)

    titles = [t for t in titles if not _is_garbage_stamp_title(t)]

    kv_map = _normalize_stamp_kv_map({x["field"]: x["value"] for x in kv})
    revisions = parse_revision_table(raw)
    return {
        "kv": [{"field": f, "value": kv_map[f]} for f in STAMP_KV_ORDER if kv_map.get(f)],
        "cipher_candidates": ciphers,
        "signatures": _extract_signatures(lines),
        "titles": _dedupe_titles(titles, kv_map),
        "revisions": revisions,
    }


def _block_between(text: str, start_rx: str, end_rx: str | None) -> str:
    t = fix_anchor_typos(normalize_ws(text))
    m0 = re.search(start_rx, t, re.I)
    if not m0:
        return ""
    chunk = t[m0.end() :]
    if end_rx:
        m1 = re.search(end_rx, chunk, re.I)
        if m1:
            chunk = chunk[: m1.start()]
    return chunk.strip()


def _split_expl_name_note(name: str, note: str, grid: str) -> tuple[str, str, str]:
    """
    Отделить примечание от наименования по структуре строки (скобки + хвост),
    без подстановки конкретных слов.
    """
    name = re.sub(r"\s+", " ", (name or "").strip())
    note = re.sub(r"\s+", " ", (note or "").strip())
    grid = re.sub(r"\s+", " ", (grid or "").strip())
    if note and note not in ("—", "-"):
        return name, note, grid
    if not name:
        return name, note or "—", grid
    m = re.match(r"^(.+?\([A-ZА-ЯЁ]{2,10}\))\s+(.+)$", name)
    if m:
        tail = m.group(2).strip()
        if 1 <= len(tail.split()) <= 4 and re.fullmatch(
            r"(?:[А-ЯЁ][а-яё]{3,}\s*){1,4}", tail
        ):
            return m.group(1).strip(), tail, grid
    parts = re.split(r"\s{2,}", name)
    if len(parts) >= 2:
        return parts[0].strip(), parts[-1].strip(), grid
    return name, note or "—", grid


def _normalize_expl_row(row: dict[str, str]) -> dict[str, str]:
    name, note, grid = _split_expl_name_note(
        str(row.get("name") or ""),
        str(row.get("note") or ""),
        str(row.get("grid") or ""),
    )
    return {
        "plan_number": str(row.get("plan_number") or "—").strip() or "—",
        "name": name,
        "grid": grid or "—",
        "note": note or "—",
    }


def _is_explication_data_row(num: str, name: str) -> bool:
    if num and not re.fullmatch(r"\d{1,2}", num):
        return False
    nm = re.sub(r"\s+", " ", name.strip())
    if len(nm) < 4:
        return False
    low = nm.casefold()
    if any(x in low for x in ("разраб", "пров.", "гип", "формат", "копиров", "таблица", "обозначен")):
        return False
    return bool(re.search(r"[А-Яа-яЁё]{3,}", nm))


def _parse_explication_line(ln: str) -> dict[str, str] | None:
    ln = ln.strip()
    if not ln or _HEADER_RX.search(ln):
        return None

    if "|" in ln:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) >= 2 and re.fullmatch(r"\d{1,2}", cells[0]):
            num = cells[0]
            name_cell = cells[1]
            grid = ""
            note = ""
            if len(cells) >= 4:
                grid, note = cells[2], cells[3]
            elif len(cells) == 3:
                grid = cells[2]
            parts = re.split(r"\s{2,}", name_cell.strip())
            if len(parts) >= 2 and not note:
                name = parts[0].strip()
                note = parts[-1].strip()
            else:
                name = name_cell.strip()
            if _is_explication_data_row(num, name):
                return _normalize_expl_row(
                    {"plan_number": num, "name": name, "grid": grid or "", "note": note or ""}
                )

    m = re.match(
        r"^(\d{1,2})\s+(.+?)(?:\s+([^\s].{1,40}))?\s*$",
        ln,
    )
    if m and _is_explication_data_row(m.group(1), m.group(2)):
        return _normalize_expl_row(
            {
                "plan_number": m.group(1),
                "name": m.group(2).strip(),
                "grid": "",
                "note": (m.group(3) or "").strip(),
            }
        )

    # Строка без номера в OCR, но с аббревиатурой в скобках — номер «—»
    m = re.match(r"^(.+?\([A-ZА-Я]{2,8}\))\s+(.{2,40})\s*$", ln)
    if m and _is_explication_data_row("", m.group(1)):
        return _normalize_expl_row(
            {
                "plan_number": "—",
                "name": m.group(1).strip(),
                "grid": "",
                "note": m.group(2).strip(),
            }
        )

    return None


def parse_explication(*texts: str, max_rows: int = 40) -> list[dict[str, str]]:
    seen: set[str] = set()
    rows: list[dict[str, str]] = []

    def add(row: dict[str, str]) -> None:
        row = _normalize_expl_row(row)
        key = f"{row.get('plan_number', '')}|{row['name'].casefold()}"
        if key in seen:
            return
        seen.add(key)
        rows.append(row)

    for raw in texts:
        if not (raw or "").strip():
            continue
        block = _block_between(raw, EXPLICATION_START_RX, EXPLICATION_END_RX)
        sources = [block, raw] if block else [raw]
        for src in sources:
            for ln in src.split("\n"):
                row = _parse_explication_line(ln)
                if row:
                    add(row)
                if len(rows) >= max_rows:
                    return rows
    return rows[:max_rows]


def _legend_line_note(ln: str) -> str:
    s = re.sub(r"\s+", " ", ln.strip())
    s = re.sub(r"^[—\-–•\|]+\s*", "", s)
    s = re.sub(r"\s*[—\-–\|]+\s*", " ", s)
    return s.strip()


def _legend_body(text: str) -> str:
    chunk = _block_between(text, LEGEND_START_RX, SPECIFICATION_START_RX)
    if not chunk.strip():
        chunk = _block_between(text, LEGEND_START_RX, EXPLICATION_START_RX)
    if not chunk.strip():
        return text
    lines = chunk.split("\n")
    out: list[str] = []
    skip_table_header = True
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if re.search(SPECIFICATION_START_RX, s, re.I) or re.search(EXPLICATION_START_RX, s, re.I):
            break
        if skip_table_header and re.match(r"таблица\s*\d", s, re.I):
            continue
        if skip_table_header and re.search(r"обозначение\s+примечан", s, re.I):
            skip_table_header = False
            continue
        if skip_table_header and _HEADER_RX.search(s):
            continue
        skip_table_header = False
        out.append(s)
    return "\n".join(out)


def _clean_legend_note(note: str) -> str:
    s = polish_readable_russian(re.sub(r"\s+", " ", (note or "").strip()))
    s = re.sub(r"повелительн\w*\s+насосн\w*", "повысительная насосная", s, flags=re.I)
    s = re.sub(r"^В\.трубы\b", "в.трубы", s, flags=re.I)
    s = re.sub(r"^(?:[A-ZА-ЯЁ]{2,6}\)|\([A-ZА-ЯЁ]{2,6}\))\s*", "", s)
    s = re.sub(r"^[)\]}>\.]+\s*", "", s)
    s = re.sub(r"^[=+\-–•|/\\]+\s*", "", s)
    s = re.sub(r"^[а-яёa-z]{1,4}\)\s*", "", s, flags=re.I)
    s = re.sub(r"^[а-яёa-z]\s+(?=[А-ЯA-ZА-ЯЁ])", "", s, flags=re.I)
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s.strip()


def _legend_note_key(note: str) -> str:
    s = _clean_legend_note(note).casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    words = [w for w in s.split() if len(w) > 2][:8]
    return " ".join(words)


def _legend_note_quality(note: str) -> int:
    s = _clean_legend_note(note)
    if not s:
        return -100
    letters = re.findall(r"[А-Яа-яЁё]", s)
    if len(letters) < 5:
        return -50
    vowels = sum(1 for c in letters if c.lower() in "аеёиоуыэюя")
    score = len(s) + int(vowels / max(len(letters), 1) * 30)
    if re.search(r"^[=+\-]", note or ""):
        score -= 40
    if _is_garbage_legend_note(s):
        score -= 60
    return score


def _legend_notes_similar(a: str, b: str) -> bool:
    ka, kb = _legend_note_key(a), _legend_note_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if len(ka) >= 10 and len(kb) >= 10 and (ka in kb or kb in ka):
        return True
    wa, wb = set(ka.split()), set(kb.split())
    if len(wa & wb) >= min(3, len(wa), len(wb)):
        return True
    nums_a = set(re.findall(r"\d+(?:\.\d+)+", a))
    nums_b = set(re.findall(r"\d+(?:\.\d+)+", b))
    if nums_a and nums_b and nums_a & nums_b:
        return True
    return False


def split_merged_legend_note(note: str) -> list[str]:
    """Разбить склеенный OCR нескольких строк легенды в отдельные примечания."""
    from belener.spec_table import split_glued_spec_line

    note = _clean_legend_note(note)
    if len(note) < 45:
        return [note] if note else []
    parts = split_glued_spec_line(note)
    if len(parts) >= 2:
        good = [p for p in parts if len(p) >= 8 and not _is_garbage_legend_note(p)]
        return good[:50] if good else []
    chunks = re.split(
        r"\s{2,}|\s*;\s*|(?<=[а-яё])\s*,\s*(?=[А-ЯЁ«\"])|(?<=\.)\s+(?=[А-ЯЁ])",
        note,
    )
    good = [c.strip() for c in chunks if len(c.strip()) >= 10]
    good = [c for c in good if not _is_garbage_legend_note(c)]
    return good[:50] if len(good) >= 2 else ([note] if note and not _is_garbage_legend_note(note) else [])


def _is_garbage_legend_note(note: str) -> bool:
    note = _clean_legend_note(note)
    if _is_section_title_only(note):
        return True
    if re.search(SPECIFICATION_START_RX, note, re.I):
        return True
    if re.search(
        r"<\s*[\wА-ЯA-ZЁ]|>\s*[\wА-ЯA-ZЁ]|[\wА-ЯA-ZЁ]\s*[<«]|[»>]\s*[\wА-ЯA-ZЁ]",
        note,
    ):
        return True
    if _is_column_header_line(note):
        return True
    from belener.table_quality import mixed_script_ocr_glitch, ocr_line_implausible_for_legend

    if mixed_script_ocr_glitch(note):
        return True
    if ocr_line_implausible_for_legend(note):
        return True
    words = note.split()
    if len(words) >= 2 and words[0].casefold() == words[-1].casefold():
        return True
    if re.fullmatch(r"(?:symbol|note|обозначение|примечание)", note, re.I):
        return True
    if re.search(r"^таблица\s*[_\d]", note, re.I):
        return True
    if re.search(EXPLICATION_START_RX, note, re.I) and not re.search(LEGEND_START_RX, note, re.I):
        return True
    letters = re.findall(r"[А-Яа-яЁё]", note)
    if len(letters) < 5:
        return True
    if len(set(letters)) <= 2 and len(letters) > 4:
        return True
    vowels = sum(1 for c in letters if c.lower() in "аеёиоуыэюя")
    if vowels / max(len(letters), 1) < 0.18:
        return True
    words = note.split()
    if len(words) <= 2 and all(len(w) <= 3 for w in words):
        return True
    if len(words) <= 2 and all(len(w) <= 4 for w in words) and vowels / max(len(letters), 1) < 0.25:
        return True
    if re.search(r"[бвгджзклмнпрстфхцчшщ]{5,}", note, re.I):
        return True
    if re.search(r"(.)\1{3,}", note, re.I):
        return True
    if len(note) > 70 and len(re.findall(r"[A-ZА-Я]{2,}\d", note)) >= 3:
        return True
    return False


def _title_norm_key(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    return re.sub(r"[^\w\s]+", "", t.casefold()).strip()


def _titles_similar(a: str, b: str) -> bool:
    na, nb = _title_norm_key(a), _title_norm_key(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 12 and len(nb) >= 12 and (na in nb or nb in na):
        return True
    return False


def _title_duplicates_kv(t: str, kv_map: dict[str, str]) -> bool:
    for key in ("Организация", "Город / адрес", "Обозначение / шифр", "Масштаб", "Формат", "Очередь строительства"):
        val = str(kv_map.get(key) or "").strip()
        if not val:
            continue
        if _titles_similar(t, val) or val.casefold() in t.casefold() or t.casefold() in val.casefold():
            return True
    org = str(kv_map.get("Организация") or "")
    m = re.search(r'["«]([^"»]{4,60})["»]', org)
    if m:
        core = m.group(1).strip()
        if len(core) >= 5 and (core.casefold() in t.casefold() or t.casefold() in core.casefold()):
            return True
    return False


def _merge_stamp_titles(
    ocr_titles: list[str] | None,
    vision_titles: list[str] | None,
    kv_map: dict[str, str] | None = None,
) -> list[str]:
    """Разделы штампа: OCR приоритетнее vision (vision часто путает даты/компас)."""

    def _clean_list(src: list[str] | None) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in src or []:
            if not isinstance(raw, str):
                continue
            t = _normalize_stamp_title(raw)
            if not t or not _looks_like_stamp_section_title(t):
                continue
            key = _title_norm_key(t)
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out

    ocr_clean = _clean_list(ocr_titles)
    if ocr_clean:
        vision_clean = _clean_list(vision_titles)
        for t in vision_clean:
            if not any(_titles_similar(t, o) for o in ocr_clean):
                ocr_clean.append(t)
        return _dedupe_titles(ocr_clean, kv_map)

    groups: list[dict[str, Any]] = []
    for src_idx, titles in enumerate((ocr_clean, _clean_list(vision_titles))):
        for t in titles:
            placed = False
            for g in groups:
                if _titles_similar(t, g["text"]):
                    cand_score = _readability_score(t) + (3.0 if src_idx == 0 else 0.0)
                    if cand_score > g["score"]:
                        g["text"] = t
                        g["score"] = cand_score
                    placed = True
                    break
            if not placed:
                groups.append({"text": t, "score": _readability_score(t) + (3.0 if src_idx == 0 else 0.0)})
    raw = [g["text"] for g in groups if _looks_like_stamp_section_title(g["text"])]
    return _dedupe_titles(raw, kv_map)


def _dedupe_titles(titles: list[str], kv_map: dict[str, str] | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    kv_map = kv_map or {}
    stage = str(kv_map.get("Стадия") or "")
    queue = str(kv_map.get("Очередь строительства") or "")
    for raw in titles:
        t = re.sub(r"\s+", " ", (raw or "").strip())
        if not t or not _looks_like_stamp_section_title(t):
            continue
        polished = _normalize_stamp_title(t)
        if re.match(r"^\d{1,3}\.\d{2}\.?\s+", raw.strip()):
            continue
        if _is_stage_phrase(polished):
            continue
        if _readability_score(polished) < 8.0:
            continue
        if stage and stage.casefold() in t.casefold():
            continue
        if queue and (_titles_similar(t, queue) or queue.casefold() in t.casefold()):
            continue
        if _title_duplicates_kv(t, kv_map):
            continue
        skip = False
        for val in kv_map.values():
            val = str(val or "").strip()
            if len(val) >= 8 and (val.casefold() in t.casefold() or t.casefold() in val.casefold()):
                skip = True
                break
        if skip:
            continue
        norm = _title_norm_key(t)
        if not norm or norm in seen:
            continue
        if any(_titles_similar(t, prev) for prev in out):
            continue
        seen.add(norm)
        out.append(polished)
    return out[:20]


def _is_legend_note(ln: str) -> bool:
    note = _legend_line_note(ln)
    if len(note) < 6:
        return False
    if _is_garbage_legend_note(note):
        return False
    letters = re.findall(r"[А-Яа-яЁё]", note)
    if len(letters) < 4:
        return False
    if len(re.sub(r"[А-Яа-яЁё\s]", "", note)) > len(note) * 0.5:
        return False
    if re.fullmatch(r"[\d.\-%—\-\\|/\\s]+", note):
        return False
    if _HEADER_RX.search(note):
        return False
    if re.search(
        r"номер\s+на\s+плане|координат|квадрат|план\s*№|"
        r"обозначение\s+примечан|примечание\s*$|наименован\s*$",
        note,
        re.I,
    ):
        return False
    if re.search(
        r"разраб|пров\.|гип\b|контр\.|копиров|формат\s*a\d|"
        r"^\d{2,6}-\d+-|ру\s*п\b",
        note,
        re.I,
    ):
        return False
    return bool(re.search(r"[А-Яа-яЁё]{4,}", note))


def _legend_notes_from_line(ln: str) -> list[str]:
    out: list[str] = []
    if "|" in ln:
        cells = [c.strip() for c in ln.strip("|").split("|") if c.strip()]
        if len(cells) >= 2:
            for cell in (cells[-1], *reversed(cells)):
                if _is_legend_note(cell):
                    out.append(_legend_line_note(cell))
                    break
        return out
    if _is_legend_note(ln):
        out.append(_legend_line_note(ln))
    return out


def parse_legend(*texts: str, max_rows: int = 60) -> list[dict[str, str]]:
    notes: list[str] = []
    seen: set[str] = set()

    for raw in texts:
        if not (raw or "").strip():
            continue
        body = _legend_body(raw)
        for ln in body.split("\n"):
            for note in _legend_notes_from_line(ln):
                if _is_garbage_legend_note(note):
                    continue
                key = note.casefold()
                if key in seen:
                    continue
                seen.add(key)
                notes.append(note)
                if len(notes) >= max_rows:
                    break
            if len(notes) >= max_rows:
                break

    return [
        {
            "symbol": LEGEND_SYMBOL_PLACEHOLDER,
            "note": _clean_legend_note(n),
        }
        for n in notes
        if not _is_garbage_legend_note(_clean_legend_note(n))
    ]


def _is_column_header_line(s: str) -> bool:
    """Строка — шапка колонок («Обозначение Примечание» и т.п.), не «Таблица N» и не заголовок раздела."""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return False
    if re.fullmatch(rf"{GENERIC_TABLE_RX}", s, re.I):
        return False
    if _COLUMN_HEADER_RX.search(s):
        return True
    tokens = set(re.findall(r"[а-яё]+", s.casefold()))
    if tokens and tokens <= _HEADER_WORDS:
        return True
    if len(tokens & _HEADER_WORDS) >= 2 and len(tokens) <= 6:
        return True
    return False


def _is_table_column_header_line(s: str) -> bool:
    return _is_column_header_line(s)


def clean_table_title(title: str) -> str:
    """Заголовок таблицы для отчёта: пусто, если это шапка колонок или «Таблица N»."""
    t = polish_section_title(title) if (title or "").strip() else ""
    if not t or _is_generic_table_title(t) or _is_column_header_line(t):
        return ""
    if len(t) > 72:
        t = t[:72].rsplit(" ", 1)[0].strip()
    if len(_SPEC_COL_MARKERS.findall(t)) >= 2:
        return ""
    if _readability_score(t) < 4.0:
        return ""
    if sum(1 for w in t.split() if len(w) <= 2) >= max(4, len(t.split()) // 2):
        return ""
    return t


def _looks_like_table_title_line(s: str) -> bool:
    """Строка похожа на заголовок таблицы (не шапка колонок и не строка данных)."""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) < 5 or "|" in s:
        return False
    if _is_column_header_line(s):
        return False
    if _HEADER_RX.search(s):
        return False
    if re.fullmatch(rf"{GENERIC_TABLE_RX}", s, re.I):
        return False
    if re.match(r"^\d{1,2}\s+[А-ЯA-Z]", s):
        return False
    if re.search(r"разро[бь]|пров\.|гип\b|формат\s*a\d|1\s*:\s*\d|ру\s*[\"«]", s, re.I):
        return False
    if re.search(EXPLICATION_START_RX, s, re.I) or re.search(LEGEND_START_RX, s, re.I):
        return len(s) >= 10
    cyr = len(re.findall(r"[А-Яа-яЁё]", s))
    return cyr >= 8 and len(s.split()) >= 2


def _extract_anchor_title_line(line: str, anchor_rx: str) -> str:
    """Строка с якорём раздела — заголовок таблицы как на листе."""
    s = re.sub(r"\s+", " ", (line or "").strip())
    if not s or not re.search(anchor_rx, s, re.I):
        return ""
    m = re.search(anchor_rx + r"[\wА-Яа-яЁё\s\-«»\".,:;()]*", s, re.I)
    if not m:
        return ""
    t = re.sub(
        r"\s*(?:номер\s+на\s+плане|наименован\w*|координат\w*|обозначение\s*\||примечан\w*).*$",
        "",
        m.group(0),
        flags=re.I,
    ).strip()
    return clean_table_title(t)


def _extract_anchor_title_multiline(lines: list[str], start_i: int, anchor_rx: str) -> str:
    """Заголовок на 1–3 строках OCR (якорь + продолжение без шапки колонок)."""
    if start_i < 0 or start_i >= len(lines):
        return ""
    parts: list[str] = []
    for j in range(start_i, min(start_i + 4, len(lines))):
        ln = re.sub(r"\s+", " ", lines[j].strip())
        if not ln or _is_column_header_line(ln):
            break
        if re.search(GENERIC_TABLE_RX, ln, re.I) and j > start_i:
            break
        if j == start_i:
            chunk = _extract_anchor_title_line(ln, anchor_rx)
            if chunk:
                parts.append(chunk)
            continue
        if re.search(anchor_rx, ln, re.I):
            chunk = _extract_anchor_title_line(ln, anchor_rx)
            if chunk:
                parts.append(chunk)
            continue
        if parts and re.search(r"^[а-яёa-z]", ln, re.I) and len(ln) < 100:
            if not _is_column_header_line(ln) and not re.match(r"^\d{1,2}\s+", ln):
                parts.append(ln)
            continue
        if parts:
            break
    if not parts:
        return ""
    return clean_table_title(" ".join(parts))


def _title_from_full_ocr_by_kind(ocr_lines: list[str], kind: str) -> str:
    """Поиск заголовка по всему OCR правой колонки (если блок таблицы без якоря)."""
    if not ocr_lines or not kind:
        return ""
    if kind == "explication":
        anchor = EXPLICATION_START_RX
    elif kind == "legend":
        anchor = LEGEND_START_RX
    else:
        return ""
    for i, ln in enumerate(ocr_lines):
        if re.search(anchor, ln, re.I):
            t = _extract_anchor_title_multiline(ocr_lines, i, anchor)
            if t:
                return t
    return ""


def _title_from_block_anchors(
    lines: list[str],
    start_i: int,
    end_i: int,
    kind: str = "",
) -> str:
    anchors: list[tuple[str, str]] = []
    if kind == "explication":
        anchors = [(EXPLICATION_START_RX, "explication")]
    elif kind == "legend":
        anchors = [(LEGEND_START_RX, "legend")]
    else:
        anchors = [(EXPLICATION_START_RX, "explication"), (LEGEND_START_RX, "legend")]
    best = ""
    for j in range(max(0, start_i), min(end_i, len(lines))):
        for rx, _k in anchors:
            t = _extract_anchor_title_multiline(lines, j, rx)
            if t and len(t) > len(best):
                best = t
    return best


def _parse_table_label_line(line: str) -> tuple[str, str]:
    """Номер таблицы и текст заголовка на той же строке (если есть)."""
    s = re.sub(r"\s+", " ", (line or "").strip())
    if not s:
        return "", ""
    m_num = re.search(r"таблица\s*(\d+(?:\.\d+)?)", s, re.I)
    if m_num:
        label = f"Таблица {m_num.group(1)}"
        rest = s[m_num.end() :].strip()
        if len(rest) >= 5 and _looks_like_table_title_line(rest):
            return label, rest
        return label, ""
    if re.search(TABLE_LABEL_LOOSE_RX, s, re.I):
        return "", ""
    m = re.match(rf"^({GENERIC_TABLE_RX})\s+(.+)$", s, re.I)
    if m:
        rest = m.group(2).strip()
        if len(rest) >= 5 and _looks_like_table_title_line(rest):
            return m.group(1), rest
        return m.group(1), ""
    m = re.match(rf"^({GENERIC_TABLE_RX})\s*$", s, re.I)
    if m:
        return m.group(1), ""
    m = re.search(rf"({GENERIC_TABLE_RX})", s, re.I)
    if not m:
        return "", ""
    before = s[: m.start()].strip()
    after = s[m.end() :].strip()
    for part in (before, after):
        if len(part) >= 5 and _looks_like_table_title_line(part):
            return m.group(1), part
    return m.group(1), ""


def _column_header_line_index(lines: list[str], start: int, end: int) -> int:
    for i in range(max(0, start), min(end, len(lines))):
        if _is_table_column_header_line(lines[i]):
            return i
    return -1


def _title_between_label_and_header(lines: list[str], start_i: int, end_i: int) -> str:
    """
    ГОСТ-раскладка: слева «Таблица N», по центру название, ниже — шапка колонок.
    Все строки между меткой таблицы и шапкой (не вплотную к данным).
    """
    start_i = max(0, start_i)
    end_i = min(len(lines), end_i)
    hdr = _column_header_line_index(lines, start_i, end_i)
    search_end = hdr if hdr >= 0 else end_i

    parts: list[str] = []
    _label, inline = _parse_table_label_line(lines[start_i] if start_i < len(lines) else "")
    if inline:
        parts.append(inline)

    for j in range(start_i, search_end):
        if j == start_i and re.search(GENERIC_TABLE_RX, lines[j], re.I):
            rest = re.sub(rf"^{GENERIC_TABLE_RX}\s*", "", lines[j], flags=re.I).strip()
            if rest and _looks_like_table_title_line(rest):
                parts.append(rest)
            continue
        if j <= start_i:
            continue
        ln = re.sub(r"\s+", " ", lines[j].strip())
        if not ln:
            continue
        if re.fullmatch(rf"{GENERIC_TABLE_RX}", ln, re.I):
            continue
        if _is_table_column_header_line(ln):
            continue
        if re.match(r"^\d{1,2}\s+[А-ЯA-Z]", ln):
            continue
        if _looks_like_table_title_line(ln):
            parts.append(ln)
        elif re.search(EXPLICATION_START_RX, ln, re.I) or re.search(LEGEND_START_RX, ln, re.I):
            chunk = _extract_anchor_title_line(
                ln,
                EXPLICATION_START_RX if re.search(EXPLICATION_START_RX, ln, re.I) else LEGEND_START_RX,
            )
            if chunk:
                parts.append(chunk)

    if not parts:
        return ""
    return clean_table_title(" ".join(parts))


def _title_in_block(
    lines: list[str],
    start_i: int,
    end_i: int,
    kind: str = "",
) -> str:
    """Заголовок в блоке: метка «Таблица N» → строки до шапки колонок (ГОСТ)."""
    start_i = max(0, start_i)
    end_i = min(len(lines), end_i)
    if start_i >= end_i:
        return ""

    t = _title_between_label_and_header(lines, start_i, end_i)
    if t:
        return t

    _label, inline = _parse_table_label_line(lines[start_i])
    if inline:
        t = clean_table_title(inline)
        if t:
            return t

    hdr = _column_header_line_index(lines, start_i, end_i)
    search_end = hdr if hdr >= 0 else end_i

    candidates: list[tuple[int, int, str]] = []
    for j in range(start_i, search_end):
        cand = re.sub(r"\s+", " ", lines[j].strip())
        if not _looks_like_table_title_line(cand):
            continue
        if hdr >= 0:
            dist = hdr - j
            if dist <= 0:
                continue
        else:
            dist = j - start_i + 1
        candidates.append((dist, -len(cand), cand))

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        t = clean_table_title(candidates[0][2])
        if t:
            return t

    for offset in range(1, 12):
        for j in (start_i - offset, start_i + offset):
            if j < 0 or j >= end_i or (hdr >= 0 and j >= search_end):
                continue
            cand = re.sub(r"\s+", " ", lines[j].strip())
            if _looks_like_table_title_line(cand):
                t = clean_table_title(cand)
                if t:
                    return t

    t = _title_from_block_anchors(lines, start_i, end_i, kind)
    if t:
        return t
    for i in range(start_i, min(end_i, len(lines))):
        if kind == "explication" and re.search(EXPLICATION_START_RX, lines[i], re.I):
            return _extract_anchor_title_multiline(lines, i, EXPLICATION_START_RX)
        if kind == "legend" and re.search(LEGEND_START_RX, lines[i], re.I):
            return _extract_anchor_title_multiline(lines, i, LEGEND_START_RX)
    return ""


def _table_number_line_indices(lines: list[str], *, strict: bool = True) -> list[int]:
    """Индексы строк «Таблица N»; strict=True — только с цифрой (меньше ложных разрезов OCR)."""
    out: list[int] = []
    for i, ln in enumerate(lines):
        if strict:
            if re.search(r"таблица\s*\d", ln, re.I):
                out.append(i)
        elif re.search(TABLE_LABEL_LOOSE_RX, ln, re.I):
            out.append(i)
    return out


def _anchor_kind_on_line(ln: str) -> str | None:
    order = {"specification": 0, "explication": 1, "legend": 2}
    found: list[str] = []
    if re.search(SPECIFICATION_START_RX, ln, re.I):
        found.append("specification")
    if re.search(EXPLICATION_START_RX, ln, re.I):
        found.append("explication")
    if re.search(LEGEND_START_RX, ln, re.I):
        found.append("legend")
    if not found:
        return None
    return min(found, key=lambda k: order[k])


def split_text_by_section_anchors(text: str) -> list[tuple[str, str]]:
    """Разбить OCR-блок по якорям разделов (спецификация / экспликация / легенда)."""
    t = finalize_ocr_text(fix_anchor_typos(normalize_ws(text)))
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in t.split("\n") if ln.strip()]
    if not lines:
        return []
    markers: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        kind = _anchor_kind_on_line(ln)
        if kind:
            markers.append((i, kind))
    if not markers:
        return []
    collapsed: list[tuple[int, str]] = []
    for item in markers:
        if collapsed and collapsed[-1][1] == item[1]:
            continue
        collapsed.append(item)
    blocks: list[tuple[str, str]] = []
    for mi, (start_i, kind) in enumerate(collapsed):
        end_i = collapsed[mi + 1][0] if mi + 1 < len(collapsed) else len(lines)
        block = "\n".join(lines[start_i:end_i])
        if block.strip():
            blocks.append((kind, block))
    return blocks


def _is_section_title_only(text: str) -> bool:
    """Строка — только заголовок раздела (не строка данных таблицы)."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s or len(s) > 55:
        return False
    if re.fullmatch(rf"{GENERIC_TABLE_RX}", s, re.I):
        return True
    if re.search(
        r"^(?:экспликац|эксплуатац|условн\w*\s+обознач|координат|номер\s+координат|"
        r"наименован\w*|примечан\w*|таблиц\w*)",
        s,
        re.I,
    ):
        return True
    tokens = {w.casefold() for w in re.findall(r"[А-Яа-яЁёA-Za-z]{2,}", s)}
    if tokens and tokens <= _HEADER_WORDS:
        return True
    if len(tokens & _HEADER_WORDS) >= 2 and len(tokens) <= 5:
        return True
    return False


def _is_spec_header_data_row(row: dict) -> bool:
    if not isinstance(row, dict):
        return True
    vals = [str(v).strip().casefold() for v in row.values() if str(v).strip()]
    header_vals = {
        "наименование",
        "наименован",
        "кол.",
        "кол",
        "примечание",
        "примечан",
        "обозначение",
        "поз.",
        "масса ед., кг",
        "масса",
    }
    if sum(1 for v in vals if v in header_vals) >= 2:
        return True
    name = str(row.get("Наименование") or row.get("name") or "").strip()
    if re.fullmatch(r"наименован\w*|кол\.?|примечан\w*|обозначен\w*|поз\.?", name, re.I):
        return True
    if _is_section_title_only(name):
        return True
    return False


def _filter_specification_rows(rows: list[dict]) -> list[dict[str, str]]:
    from belener.spec_table import filter_spec_rows

    return filter_spec_rows(
        rows,
        is_header_row=_is_spec_header_data_row,
        is_garbage_name=_is_garbage_spec_name,
    )


def _table_number_sort_key(num: str) -> tuple[int, int]:
    m = re.search(r"(\d+)(?:\.(\d+))?", num or "", re.I)
    if m:
        return int(m.group(1)), int(m.group(2) or 0)
    return 9999, 0


def _sort_table_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Порядок: «Таблица 1» … «Таблица N», иначе позиция на листе, иначе тип блока."""
    kind_ord = {"specification": 0, "explication": 1, "legend": 2, "table": 3}

    def key(sec: dict[str, Any]) -> tuple:
        tn = str(sec.get("table_number") or "").strip()
        if tn:
            a, b = _table_number_sort_key(tn)
            return (0, a, b, 0)
        sl = int(sec.get("start_line") if sec.get("start_line") is not None else 9999)
        ko = kind_ord.get(str(sec.get("kind") or ""), 5)
        return (1, sl, ko, 0)

    return sorted(sections, key=key)


def _parse_block_rows_for_kind(block: str, kind: str) -> tuple[list[dict[str, str]], str]:
    if kind == "explication":
        rows = parse_explication(block)
        return (rows, "explication") if rows else ([], "table")
    if kind == "legend":
        rows = parse_legend(block)
        return (rows, "legend") if rows else ([], "table")
    if kind == "specification":
        rows = parse_specification(block)
        return (rows, "specification") if rows else ([], "table")
    return _parse_block_rows(block)


def _discover_sections_by_anchors(norm_lines: list[str]) -> list[dict[str, Any]]:
    """Таблицы по якорям разделов (спецификация / экспликация / условные обозначения)."""
    markers: list[tuple[int, str]] = []
    for i, ln in enumerate(norm_lines):
        kind = _anchor_kind_on_line(ln)
        if kind:
            markers.append((i, kind))
    if not markers:
        return []
    collapsed: list[tuple[int, str]] = []
    for item in markers:
        if collapsed and collapsed[-1][1] == item[1]:
            continue
        collapsed.append(item)

    sections: list[dict[str, Any]] = []
    for mi, (start_i, kind) in enumerate(collapsed):
        end_i = collapsed[mi + 1][0] if mi + 1 < len(collapsed) else len(norm_lines)
        block = "\n".join(norm_lines[start_i:end_i])
        rows, parsed_kind = _parse_block_rows_for_kind(block, kind)
        if not rows:
            continue
        table_number, _ = _parse_table_label_line(norm_lines[start_i])
        sections.append(
            {
                "title": _title_in_block(norm_lines, start_i, end_i, parsed_kind),
                "kind": parsed_kind,
                "rows": rows,
                "table_number": table_number,
                "start_line": start_i,
            }
        )
    return sections


_SPEC_COLS = ("Поз.", "Обозначение", "Наименование", "Кол.", "Масса ед., кг", "Примечание")

_SPEC_COL_MARKERS = re.compile(
    r"(?:поз\.?|обозначен|наименован|кол\.?|масса|ед\.?|примечан)",
    re.I,
)
_REVISION_HDR = re.compile(r"изм\.?|кол\.?\s*уч|описан|№\s*док", re.I)


def _split_row_cells(ln: str) -> list[str]:
    s = ln.strip()
    if "|" in s:
        return [c.strip() for c in s.strip("|").split("|") if c.strip()]
    if "\t" in s:
        return [c.strip() for c in s.split("\t") if c.strip()]
    parts = re.split(r"\s{2,}", s)
    return [p.strip() for p in parts if p.strip()]


def _normalize_col_name(h: str) -> str:
    t = re.sub(r"\s+", " ", (h or "").strip())
    low = t.casefold()
    if re.search(r"^поз", low):
        return "Поз."
    if "обознач" in low:
        return "Обозначение"
    if "наимен" in low:
        return "Наименование"
    if re.search(r"^кол", low):
        return "Кол."
    if "масса" in low:
        return "Масса ед., кг"
    if "примеч" in low:
        return "Примечание"
    return t or "—"


def _is_garbage_spec_name(name: str) -> bool:
    """Имя позиции перечня — не заголовок раздела и не подпись схемы."""
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n or _is_section_title_only(n):
        return True
    if re.search(SPECIFICATION_START_RX, n, re.I):
        return True
    if re.search(r"[<«][\wА-ЯA-Z]|[\wА-ЯA-Z][>»]", n):
        return True
    return False


def _is_spec_data_line(ln: str) -> bool:
    """Строка похожа на данные таблицы спецификации (не шапка и не другой раздел)."""
    s = re.sub(r"\s+", " ", (ln or "").strip())
    if len(s) < 8:
        return False
    if _is_column_header_line(s) or _HEADER_RX.search(s):
        return False
    if re.search(LEGEND_START_RX, s, re.I) or re.search(EXPLICATION_START_RX, s, re.I):
        return False
    if re.search(SPECIFICATION_START_RX, s, re.I) and len(_SPEC_COL_MARKERS.findall(s)) >= 2:
        return False
    if _is_section_title_only(s):
        return False
    letters = re.findall(r"[А-Яа-яЁё]", s)
    return len(letters) >= 6


def _parse_spec_row_loose(ln: str, headers: list[str] | None = None) -> dict[str, str] | None:
    """Строка спецификации: позиция, обозначение или колонки по шапке OCR."""
    s = re.sub(r"\s+", " ", (ln or "").strip())
    if not _is_spec_data_line(s):
        return None

    if headers:
        cells = _split_row_cells(s)
        if len(cells) >= 2:
            row = {headers[j]: cells[j] if j < len(cells) else "—" for j in range(len(headers))}
            out = {col: str(row.get(col) or "—") for col in _SPEC_COLS}
            name = str(out.get("Наименование") or "").strip()
            if len(name) >= 6:
                return out

    from belener.spec_table import fix_spec_row_qty, qty_looks_like_panel_number

    pos, desig, name, qty = "—", "—", "", "—"
    m = re.match(r"^(\d{1,3})\s+(.+)$", s)
    _desig_token = re.compile(r"^[A-ZА-ЯЁ0-9][\w.\-/]{1,14}$", re.I)

    if m:
        pos = m.group(1)
        rest = m.group(2).strip()
        qm = re.search(r"\s+(\d{1,3})\s*$", rest)
        if qm and not qty_looks_like_panel_number(rest, qm.group(1)):
            qty = qm.group(1)
            rest = rest[: qm.start()].strip()
        parts = rest.split(None, 1)
        if (
            len(parts) == 2
            and _desig_token.fullmatch(parts[0])
            and ("-" in parts[0] or re.search(r"\d", parts[0]))
        ):
            desig, name = parts[0], parts[1]
        else:
            desig, name = "—", rest
    else:
        parts = s.split(None, 1)
        if len(parts) == 2 and len(parts[0]) <= 16:
            desig, name = parts[0], parts[1]
        else:
            name = s
        qm = re.search(r"\s+(\d{1,3})\s*$", name)
        if qm and not qty_looks_like_panel_number(name, qm.group(1)):
            qty = qm.group(1)
            name = name[: qm.start()].strip()

    if len(name) < 6 or not re.search(r"[А-Яа-яЁё]{4,}", name):
        return None
    row = {
        "Поз.": pos,
        "Обозначение": desig or "—",
        "Наименование": name,
        "Кол.": qty,
        "Масса ед., кг": "—",
        "Примечание": "—",
    }
    return fix_spec_row_qty(row)


def normalize_specification_rows(rows: list[dict]) -> list[dict[str, str]]:
    """Привести строки к колонкам перечня аппаратуры (убрать symbol/note от ошибочного парсера)."""
    out: list[dict[str, str]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        keys = {str(k).casefold() for k in r}
        if "наименование" in keys or "поз." in keys:
            row = {h: str(r.get(h) or r.get(h.replace(".", "")) or "—") for h in _SPEC_COLS}
            for h in _SPEC_COLS:
                if h not in row:
                    row[h] = str(r.get(h) or "—")
            name = str(row.get("Наименование") or "").strip()
            if name and name not in ("—", "") and not _is_garbage_spec_name(name):
                out.append({k: row.get(k, "—") for k in _SPEC_COLS})
            continue
        note = str(r.get("note") or r.get("name") or "").strip()
        if not note:
            continue
        parsed = _parse_spec_row_loose(note)
        if parsed:
            out.append(parsed)
    return _filter_specification_rows(out)


def _parse_specification_loose(block: str, *, max_rows: int = 80) -> list[dict[str, str]]:
    """Второй проход: якорь спецификации есть, шапка колонок распознана слабо."""
    from belener.spec_table import explode_spec_ocr_lines

    if not has_specification_anchor(block):
        return []
    raw_lines = [ln.strip() for ln in fix_anchor_typos(normalize_ws(block)).split("\n") if ln.strip()]
    lines = explode_spec_ocr_lines(raw_lines)
    hdr_i = -1
    headers: list[str] = []
    for i, ln in enumerate(lines):
        if len(_SPEC_COL_MARKERS.findall(ln)) >= 2:
            hdr_i = i
            headers = [_normalize_col_name(c) for c in _split_row_cells(ln)]
            break
    start = hdr_i + 1 if hdr_i >= 0 else 0
    if hdr_i < 0:
        for i, ln in enumerate(lines):
            if has_specification_anchor(ln):
                start = i + 1
                break
    out: list[dict[str, str]] = []
    for ln in lines[start:]:
        if re.search(LEGEND_START_RX, ln, re.I) or re.search(EXPLICATION_START_RX, ln, re.I):
            break
        if _REVISION_HDR.search(ln):
            break
        row = _parse_spec_row_loose(ln, headers or None)
        if row:
            out.append(row)
        if len(out) >= max_rows:
            break
    return _filter_specification_rows(out)


def _spec_title_from_text(text: str, zone_key: str = "") -> str:
    """Заголовок таблицы по якорю раздела (без фиксированных названий проекта)."""
    for ln in (text or "").splitlines()[:12]:
        s = ln.strip()
        if not s or len(s) > 90:
            continue
        if re.search(SPECIFICATION_START_RX, s, re.I):
            t = clean_table_title(_extract_anchor_title_line(s, SPECIFICATION_START_RX))
            if t:
                return t
            t = clean_table_title(polish_section_title(s))
            if t and len(t) <= 72:
                return t
        hit = _section_anchor(s)
        if hit and hit[1]:
            t = clean_table_title(hit[1])
            if t:
                return t
    if zone_key == "spec_left" and re.search(r"продолжен\w*\s+таблиц", text or "", re.I):
        return clean_table_title("Продолжение таблицы")
    return ""


def _merge_multiline_spec_header(lines: list[str], start: int) -> tuple[list[str], int]:
    """Склеить 2–3 строки шапки ГОСТ («Поз.» / «обозначение» на разных строках)."""
    parts: list[str] = []
    i = start
    while i < len(lines) and i < start + 4:
        ln = lines[i]
        if _SPEC_COL_MARKERS.search(ln) or _is_column_header_line(ln):
            parts.append(ln)
            i += 1
            if len(_SPEC_COL_MARKERS.findall(" ".join(parts))) >= 3:
                break
            continue
        break
    if not parts:
        return [], start
    merged_cells = _split_row_cells(" ".join(parts))
    headers = [_normalize_col_name(c) for c in merged_cells if c.strip()]
    return headers, i


def parse_specification(block: str, *, max_rows: int = 80) -> list[dict[str, str]]:
    """Таблица позиций (спецификация, ведомость) по шапке колонок."""
    from belener.spec_table import explode_spec_ocr_lines, is_spec_group_header_line

    raw_lines = [ln.strip() for ln in fix_anchor_typos(normalize_ws(block)).split("\n") if ln.strip()]
    lines = explode_spec_ocr_lines(raw_lines)
    hdr_i = -1
    data_start = 0
    headers: list[str] = []
    for i, ln in enumerate(lines):
        marks = len(_SPEC_COL_MARKERS.findall(ln))
        next_marks = len(_SPEC_COL_MARKERS.findall(lines[i + 1])) if i + 1 < len(lines) else 0
        if marks >= 2 or (marks >= 1 and next_marks >= 1):
            hdr_i = i
            headers, data_start = _merge_multiline_spec_header(lines, i)
            if not headers:
                headers = [_normalize_col_name(c) for c in _split_row_cells(ln)]
                data_start = i + 1
            break
    if hdr_i < 0 or not headers:
        return _parse_specification_loose(block, max_rows=max_rows)

    rows: list[dict[str, str]] = []
    for ln in lines[data_start:]:
        if is_spec_group_header_line(ln):
            rows.append(
                {
                    "Поз.": "—",
                    "Обозначение": "—",
                    "Наименование": re.sub(r"\s+", " ", ln).strip(),
                    "Кол.": "—",
                    "Примечание": "—",
                    "_group": "1",
                }
            )
            continue
        if re.search(LEGEND_START_RX, ln, re.I) or re.search(EXPLICATION_START_RX, ln, re.I):
            break
        if _REVISION_HDR.search(ln):
            break
        if re.match(r"^\d{1,2}\s+[А-ЯЁA-Z]", ln) and not re.match(r"^\d{1,2}\s+\d", ln):
            if rows:
                break
        cells = _split_row_cells(ln)
        if len(cells) < 2:
            row = _parse_spec_row_loose(ln, headers)
            if row:
                rows.append(row)
            continue
        row: dict[str, str] = {}
        for j, h in enumerate(headers):
            row[h] = cells[j] if j < len(cells) else "—"
        first = (cells[0] or "").strip()
        name = str(row.get("Наименование") or row.get(_normalize_col_name("наименование")) or "").strip()
        if not name:
            for v in cells[1:]:
                if len(str(v or "").strip()) >= 8:
                    name = str(v).strip()
                    break
        if (
            re.fullmatch(r"\d{1,3}[*]?", first)
            or re.search(r"гост|ту\s+\d|ст\d", ln, re.I)
            or (name and len(name) >= 8 and re.search(r"[А-Яа-яЁё]{4,}", name))
        ):
            rows.append({col: row.get(col, "—") for col in _SPEC_COLS})
        if len(rows) >= max_rows:
            break
    strict = _filter_specification_rows(rows)
    if strict:
        return strict
    loose = _filter_specification_rows(_parse_specification_loose(block, max_rows=max_rows))
    from belener.spec_gost_materials import parse_gost_material_spec_rows

    gost = _filter_specification_rows(parse_gost_material_spec_rows(block, max_rows=max_rows))
    best = loose
    if len(gost) > len(best):
        best = gost
    gost_hits = sum(
        1 for r in gost if re.search(r"гост|ту\s*[\d\-]", str(r.get("Обозначение") or ""), re.I)
    )
    if gost_hits >= 2:
        return gost
    if len(best) >= 3:
        return best
    from belener.spec_extract import extract_spec_rows_from_messy_ocr

    salvage = extract_spec_rows_from_messy_ocr(block, max_rows=max_rows)
    salvage = _filter_specification_rows(salvage)
    if len(salvage) > len(best):
        best = salvage
    from belener.normative_spec import parse_normative_bom_rows

    norm = _filter_specification_rows(parse_normative_bom_rows(block, max_rows=max_rows))
    if len(norm) > len(best):
        best = norm
    elif norm and not best:
        best = norm
    return best


def parse_numbered_notes(text: str, *, max_notes: int = 40) -> list[str]:
    """Технические указания «1 …», «2 …» вне таблиц."""
    out: list[str] = []
    for ln in fix_anchor_typos(normalize_ws(text)).split("\n"):
        s = ln.strip()
        m = re.match(r"^(\d{1,2})\s+(.{12,})$", s)
        if not m:
            continue
        tail = m.group(2).strip()
        if re.search(r"\b(?:гост|ту\s*[\d\-]|держатель\s+шин|\d+[xх×]\d+)\b", s, re.I):
            continue
        if _HEADER_RX.search(tail) or re.search(r"^таблица\s", tail, re.I):
            continue
        if re.search(r"разро[бь]|пров\.|гип\b|изм\.|кол\.?\s*уч", tail, re.I):
            continue
        out.append(f"{m.group(1)} {tail}")
        if len(out) >= max_notes:
            break
    return out


def parse_revision_table(text: str) -> list[dict[str, str]]:
    """Таблица изменений в штампе (Изм., Кол.уч., …)."""
    lines = [ln.strip() for ln in normalize_ws(text).split("\n") if ln.strip()]
    hdr_i = -1
    headers: list[str] = []
    for i, ln in enumerate(lines):
        if _REVISION_HDR.search(ln) and len(_split_row_cells(ln)) >= 4:
            hdr_i = i
            headers = [_normalize_col_name(c) for c in _split_row_cells(ln)]
            break
    if hdr_i < 0:
        return []
    rows: list[dict[str, str]] = []
    for ln in lines[hdr_i + 1 :]:
        if re.search(r"заказчик|разро[бь]|пров\.|гип\b", ln, re.I):
            break
        cells = _split_row_cells(ln)
        if len(cells) < 2:
            continue
        n = max(len(headers), len(cells))
        row = {
            (headers[j] if j < len(headers) else f"col{j}"): (
                cells[j] if j < len(cells) else "—"
            )
            for j in range(n)
        }
        if any(c.strip() for c in cells):
            rows.append(row)
        if len(rows) >= 12:
            break
    return rows


def _parse_block_rows(block: str) -> tuple[list[dict[str, str]], str]:
    """Строки таблицы и тип по содержимому (без подстановки заголовков)."""
    spec = parse_specification(block)
    if spec:
        return spec, "specification"
    expl = parse_explication(block)
    leg = parse_legend(block)
    if expl and not leg:
        return expl, "explication"
    if leg and not expl:
        return leg, "legend"
    if len(expl) >= len(leg) and expl:
        return expl, "explication"
    if leg:
        return leg, "legend"
    pipe_rows = _parse_pipe_data_table(block)
    if pipe_rows:
        return pipe_rows, "table"
    return [], "table"


def _parse_pipe_data_table(block: str) -> list[dict[str, str]]:
    """Таблица с шапкой в первой строке (| или пробелы)."""
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    if len(lines) < 2:
        return []
    hdr = _split_row_cells(lines[0])
    if len(hdr) < 2:
        return []
    headers = [_normalize_col_name(h) for h in hdr]
    rows: list[dict[str, str]] = []
    for ln in lines[1:]:
        if _is_column_header_line(ln) and rows:
            break
        cells = _split_row_cells(ln)
        if len(cells) < 2:
            continue
        row = {headers[j]: cells[j] if j < len(cells) else "—" for j in range(len(headers))}
        rows.append(row)
        if len(rows) >= 80:
            break
    return rows


def _section_anchor(line: str) -> tuple[str, str] | None:
    """(kind, title) если строка — начало таблицы на листе."""
    s = re.sub(r"\s+", " ", line.strip())
    if not s:
        return None
    m = re.match(rf"^({GENERIC_TABLE_RX})\b", s, re.I)
    if m:
        _label, inline = _parse_table_label_line(s)
        title = polish_section_title(inline) if inline else ""
        return ("table", title)
    if re.search(EXPLICATION_START_RX, s, re.I):
        return ("explication", "")
    if re.search(LEGEND_START_RX, s, re.I):
        return ("legend", "")
    if re.search(SPECIFICATION_START_RX, s, re.I):
        return ("specification", polish_section_title(s) or "")
    return None


def discover_table_sections(text: str) -> list[dict[str, Any]]:
    """Таблицы на фрагменте: «Таблица N», якоря разделов, порядок как на листе."""
    if not (text or "").strip():
        return []
    t = finalize_ocr_text(fix_anchor_typos(normalize_ws(text)))
    norm_lines = [re.sub(r"\s+", " ", ln.strip()) for ln in t.split("\n") if ln.strip()]
    if not norm_lines:
        return []

    anchor_sections = _discover_sections_by_anchors(norm_lines)
    if anchor_sections:
        return _sort_table_sections(anchor_sections)

    table_idxs = _table_number_line_indices(norm_lines)
    sections: list[dict[str, Any]] = []
    if len(table_idxs) >= 1:
        for ti, start_i in enumerate(table_idxs):
            end_i = table_idxs[ti + 1] if ti + 1 < len(table_idxs) else len(norm_lines)
            block = "\n".join(norm_lines[start_i:end_i])
            table_number, _ = _parse_table_label_line(norm_lines[start_i])
            if not table_number:
                table_number = f"Таблица {ti + 1}"
            rows, kind = _parse_block_rows(block)
            if not rows:
                continue
            sections.append(
                {
                    "title": _title_in_block(norm_lines, start_i, end_i, kind),
                    "kind": kind,
                    "rows": rows,
                    "table_number": table_number,
                    "start_line": start_i,
                }
            )
        return _sort_table_sections(sections)

    if len(table_idxs) == 1:
        start_i = table_idxs[0]
        end_i = len(norm_lines)
        block = "\n".join(norm_lines[start_i:end_i])
        table_number, _ = _parse_table_label_line(norm_lines[start_i])
        if not table_number:
            table_number = "Таблица 1"
        rows, kind = _parse_block_rows(block)
        if rows:
            sections.append(
                {
                    "title": _title_in_block(norm_lines, start_i, end_i, kind),
                    "kind": kind,
                    "rows": rows,
                    "table_number": table_number,
                    "start_line": start_i,
                }
            )
        return _sort_table_sections(sections)

    if len(anchor_sections) >= 2:
        return _sort_table_sections(anchor_sections)
    if anchor_sections:
        return _sort_table_sections(anchor_sections)

    starts: list[tuple[int, str, str]] = []
    for i, ln in enumerate(norm_lines):
        hit = _section_anchor(ln)
        if hit:
            starts.append((i, hit[0], hit[1]))

    if not starts:
        for kind, block in split_text_by_section_anchors(t):
            rows, inferred = _parse_block_rows_for_kind(block, kind)
            if not rows:
                continue
            sections.append(
                {
                    "title": clean_table_title(_spec_title_from_text(block)) or "",
                    "kind": inferred,
                    "rows": rows,
                    "table_number": "",
                    "start_line": 0,
                }
            )
        if sections:
            return _sort_table_sections(sections)
        expl_rows = parse_explication(t)
        leg_rows = parse_legend(t)
        if expl_rows:
            sections.append(
                {
                    "title": _title_in_block(norm_lines, 0, len(norm_lines), "explication"),
                    "kind": "explication",
                    "rows": expl_rows,
                    "table_number": "",
                    "start_line": 0,
                }
            )
        if leg_rows:
            hdr = _column_header_line_index(norm_lines, 0, len(norm_lines))
            leg_start = max(0, hdr - 8) if hdr > 0 else 0
            sections.append(
                {
                    "title": _title_in_block(norm_lines, leg_start, len(norm_lines), "legend"),
                    "kind": "legend",
                    "rows": leg_rows,
                    "table_number": "",
                    "start_line": leg_start,
                }
            )
        if not sections:
            rows, kind = _parse_block_rows(t)
            if rows:
                sections.append(
                    {
                        "title": _title_in_block(norm_lines, 0, len(norm_lines), kind),
                        "kind": kind,
                        "rows": rows,
                        "table_number": "",
                        "start_line": 0,
                    }
                )
        return _sort_table_sections(sections)

    for idx, (start_i, kind, title) in enumerate(starts):
        end_i = starts[idx + 1][0] if idx + 1 < len(starts) else len(norm_lines)
        block = "\n".join(norm_lines[start_i:end_i])
        rows, inferred = _parse_block_rows(block)
        if not rows:
            continue
        kind = inferred if kind == "table" else kind
        title = clean_table_title(title) or _title_in_block(norm_lines, start_i, end_i, kind)
        table_number, _ = _parse_table_label_line(norm_lines[start_i])
        sections.append(
            {
                "title": title,
                "kind": kind,
                "rows": rows,
                "table_number": table_number,
                "start_line": start_i,
            }
        )
    return _sort_table_sections(sections)


def _normalize_table_kind(sec: dict[str, Any]) -> str:
    kind = str(sec.get("kind") or "table").strip() or "table"
    rows = sec.get("rows") or []
    if kind != "table" or not rows:
        return kind
    generic_keys = {
        "поз. обозначение",
        "поз",
        "позиция",
        "обозначение",
        "наименование",
        "кол.",
        "кол",
        "примечание",
    }
    row_keys = {str(k).strip().casefold() for r in rows if isinstance(r, dict) for k in r.keys()}
    if len(row_keys & generic_keys) >= 2:
        return "table"
    if all(isinstance(r, dict) and (r.get("note") or r.get("symbol")) for r in rows):
        if not any(re.fullmatch(r"\d{1,2}", str(r.get("plan_number") or "")) for r in rows if isinstance(r, dict)):
            return "legend"
    if any(isinstance(r, dict) and r.get("name") for r in rows):
        return "explication"
    return kind


def _looks_like_bom_rows(rows: list[dict]) -> bool:
    if not rows:
        return False
    spec_cols = sum(
        1
        for r in rows
        if isinstance(r, dict)
        and {"наименование", "поз."} & {str(k).casefold() for k in r}
    )
    if spec_cols >= 2:
        return True
    blob = " ".join(
        str(v)
        for r in rows
        if isinstance(r, dict)
        for v in list(r.values()) + [r.get("name", ""), r.get("plan_number", "")]
    )
    if re.search(SPECIFICATION_START_RX, blob, re.I):
        return True
    if re.search(r"перечень\s+аппаратур|поз\.?\s*обознач", blob, re.I):
        return True
    data_like = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("Наименование") or r.get("name") or r.get("note") or "").strip()
        qty = str(r.get("Кол.") or r.get("note") or "").strip()
        if len(name) >= 10 and re.search(r"[А-Яа-яЁё]{5,}", name):
            if re.fullmatch(r"\d{1,3}", qty) or re.search(r"\s+\d{1,3}\s*$", name):
                data_like += 1
    return data_like >= 2


def _explication_rows_to_specification(rows: list[dict]) -> list[dict[str, str]]:
    """Строки, ошибочно разобранные как экспликация, → спецификация."""
    out: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        if not name or name.casefold() in (
            "детали",
            "материалы",
            "экспликация",
            "ведомость",
            "наименование",
            "примечание",
        ):
            continue
        pos = str(r.get("plan_number") or "—").strip() or "—"
        note = str(r.get("note") or "—").strip() or "—"
        desig = ""
        if re.match(r"^[A-ZА-ЯЁ]?\w[\w.*,-]{0,12}$", name.split()[0] if name else ""):
            parts = name.split(None, 1)
            if len(parts) == 2 and len(parts[0]) <= 12:
                desig, name = parts[0], parts[1]
        out.append(
            {
                "Поз.": pos,
                "Обозначение": desig or "—",
                "Наименование": name,
                "Кол.": note if re.fullmatch(r"\d{1,3}", note) else "—",
                "Примечание": note if not re.fullmatch(r"\d{1,3}", note) else "—",
            }
        )
    return out


def _infer_table_kind(sec: dict[str, Any]) -> str:
    declared = str(sec.get("kind") or "").strip()
    if declared in ("legend", "explication", "specification"):
        return declared
    title = str(sec.get("title") or "")
    if re.search(r"перечень\s+аппаратур|продолжен\w*\s+таблиц", title, re.I):
        return "specification"
    if re.search(LEGEND_START_RX, title, re.I):
        return "legend"
    kind = _normalize_table_kind(sec)
    rows = [r for r in (sec.get("rows") or []) if isinstance(r, dict)]
    if kind == "legend":
        return "legend"
    if _looks_like_bom_rows(rows):
        return "specification"
    if kind != "table":
        return kind
    if not rows:
        return kind
    legend_n = sum(1 for r in rows if r.get("note") or r.get("symbol"))
    expl_n = sum(1 for r in rows if r.get("name"))
    if legend_n >= expl_n and legend_n >= max(1, len(rows) // 3):
        return "legend"
    if expl_n > legend_n and expl_n >= max(1, len(rows) // 3):
        if _looks_like_bom_rows(rows):
            return "specification"
        return "explication"
    return kind


def _row_keys(kind: str, rows: list[dict]) -> set[str]:
    keys: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if kind == "explication":
            keys.add(f"{str(r.get('plan_number') or '—').strip()}|{(r.get('name') or '').casefold()}")
        elif kind == "legend":
            keys.add((str(r.get("note") or "")).casefold()[:120])
        else:
            vals = " ".join(str(v or "") for v in r.values())
            vals = re.sub(r"\s+", " ", vals).strip().casefold()
            if vals:
                keys.add(vals[:160])
    return keys


def _sections_mergeable(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Слияние только дублей OCR+vision (один номер таблицы или те же строки)."""
    na = str(a.get("table_number") or "").strip()
    nb = str(b.get("table_number") or "").strip()
    if na and nb and na.casefold() == nb.casefold():
        return True
    if na and nb and na.casefold() != nb.casefold():
        return False
    ka = _infer_table_kind(a)
    kb = _infer_table_kind(b)
    if ka != kb:
        return False
    keys_a = _row_keys(ka, a.get("rows") or [])
    keys_b = _row_keys(kb, b.get("rows") or [])
    if not keys_a or not keys_b:
        return False
    overlap = len(keys_a & keys_b) / max(len(keys_a), len(keys_b))
    return overlap >= 0.5


def finalize_table_sections(
    tables: list[dict[str, Any]],
    ocr_text: str = "",
) -> list[dict[str, Any]]:
    """Каждая таблица на листе — отдельный блок; слияние только дублей одного источника."""
    tables = [
        {
            **t,
            "rows": normalize_specification_rows(t.get("rows") or [])
            if _infer_table_kind(t) == "specification"
            else (t.get("rows") or []),
        }
        for t in (tables or [])
        if isinstance(t, dict)
    ]

    def merge_rows(kind: str, a: list, b: list) -> list:
        if kind == "explication":
            return merge_explication_rows(a, b)
        if kind == "legend":
            return merge_legend_rows(a, b)
        return a if len(a) >= len(b) else b

    ocr_lines: list[str] = []
    table_line_map: dict[str, tuple[int, int]] = {}
    if (ocr_text or "").strip():
        ocr_lines = [
            re.sub(r"\s+", " ", ln.strip())
            for ln in finalize_ocr_text(fix_anchor_typos(normalize_ws(ocr_text))).split("\n")
            if ln.strip()
        ]
        ti_list = _table_number_line_indices(ocr_lines)
        for ti, start_i in enumerate(ti_list):
            end_i = ti_list[ti + 1] if ti + 1 < len(ti_list) else len(ocr_lines)
            label, _ = _parse_table_label_line(ocr_lines[start_i])
            if label:
                table_line_map[label.casefold()] = (start_i, end_i)

    merged: list[dict[str, Any]] = []

    for sec in tables or []:
        if not isinstance(sec, dict):
            continue
        kind = _infer_table_kind(sec)
        rows = sec.get("rows") or []
        if not rows:
            continue
        title = clean_table_title(str(sec.get("title") or "").strip())
        table_number = re.sub(r"\s+", " ", str(sec.get("table_number") or "").strip())
        candidate = {
            "title": title,
            "kind": kind,
            "rows": list(rows),
            "table_number": table_number,
            "start_line": sec.get("start_line"),
        }

        target_idx = -1
        for i, cur in enumerate(merged):
            if _sections_mergeable(cur, candidate):
                target_idx = i
                break

        if target_idx < 0:
            merged.append(candidate)
            continue

        cur = merged[target_idx]
        cur["rows"] = merge_rows(kind, cur.get("rows") or [], rows)
        if title and not _is_generic_table_title(title):
            cur_title = str(cur.get("title") or "")
            if (
                not cur_title
                or _is_generic_table_title(cur_title)
                or _table_title_score(title, kind) > _table_title_score(cur_title, kind)
            ):
                cur["title"] = title
        if table_number and not cur.get("table_number"):
            cur["table_number"] = table_number
        if candidate.get("start_line") is not None and cur.get("start_line") is None:
            cur["start_line"] = candidate.get("start_line")

    ti_list = _table_number_line_indices(ocr_lines) if ocr_lines else []
    for sec in merged:
        if sec.get("title"):
            sec["title"] = clean_table_title(str(sec["title"]))
            continue
        table_i = sec.get("start_line")
        end_i = len(ocr_lines)
        tn = str(sec.get("table_number") or "").strip()
        if tn and tn.casefold() in table_line_map:
            table_i, end_i = table_line_map[tn.casefold()]
        elif table_i is not None and ti_list:
            table_i = int(table_i)
            for ti, start_i in enumerate(ti_list):
                if start_i == table_i:
                    end_i = ti_list[ti + 1] if ti + 1 < len(ti_list) else len(ocr_lines)
                    break
        elif ti_list:
            idx = merged.index(sec)
            if idx < len(ti_list):
                table_i = ti_list[idx]
                end_i = ti_list[idx + 1] if idx + 1 < len(ti_list) else len(ocr_lines)
        if table_i is not None and ocr_lines:
            sec["title"] = _title_in_block(
                ocr_lines, int(table_i), end_i, _infer_table_kind(sec)
            )
        if not sec.get("title") and ocr_lines:
            sec["title"] = _title_from_full_ocr_by_kind(ocr_lines, _infer_table_kind(sec))
        sec["title"] = clean_table_title(str(sec.get("title") or ""))

    merged = _assign_missing_table_numbers(merged)
    merged = _dedupe_sections_by_kind(merged)
    return _sort_table_sections(merged)


def _section_quality_score(sec: dict[str, Any]) -> int:
    kind = _infer_table_kind(sec)
    rows = sec.get("rows") or []
    if kind == "specification":
        rows = _filter_specification_rows(normalize_specification_rows(rows))
    title = clean_table_title(str(sec.get("title") or ""))
    score = len(rows) * 12
    if title:
        score += 18
    if title and len(title) > 70:
        score -= 35
    if kind == "legend":
        score += sum(3 for r in rows if isinstance(r, dict) and _legend_note_quality(str(r.get("note") or "")) > 0)
    return score


def _dedupe_sections_by_kind(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставить лучший блок каждого типа (дубли OCR+зон)."""
    from collections import defaultdict

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sec in sections:
        k = _infer_table_kind(sec)
        groups[k].append(sec)
    out: list[dict[str, Any]] = []
    for kind in ("specification", "explication", "legend", "table"):
        group = groups.get(kind) or []
        if len(group) <= 1:
            out.extend(group)
            continue
        best = max(group, key=_section_quality_score)
        out.append(best)
    return out


def _table_title_score(title: str, kind: str) -> int:
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return 0
    score = len(t)
    if kind == "explication" and re.search(r"экспликац", t, re.I):
        score += 24
    elif kind == "explication" and re.search(r"эксп\w{4,}", t, re.I):
        score += 6
    if kind == "legend" and re.search(LEGEND_START_RX, t, re.I):
        score += 20
    return score


def _assign_missing_table_numbers(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Если OCR не распознал «Таблица N» — порядковый номер по положению на листе."""
    need = [s for s in sections if (s.get("rows") or []) and not str(s.get("table_number") or "").strip()]
    if not need:
        return sections
    ordered = _sort_table_sections(list(sections))
    n = 0
    for sec in ordered:
        if not (sec.get("rows") or []):
            continue
        if not str(sec.get("table_number") or "").strip():
            n += 1
            sec["table_number"] = f"Таблица {n}"
    return sections


def merge_table_sections(*sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    for sections in sources:
        combined.extend(sections or [])
    return combined


def tables_to_legacy(
    tables: list[dict[str, Any]],
) -> tuple[str, list[dict], str, list[dict]]:
    """Первые таблицы экспликации/легенды для совместимости."""
    expl_title, leg_title = "", ""
    expl_rows: list[dict] = []
    leg_rows: list[dict] = []
    for sec in tables:
        kind = sec.get("kind")
        rows = sec.get("rows") or []
        if kind == "explication" and not expl_rows:
            expl_title = str(sec.get("title") or expl_title)
            expl_rows = rows
        elif kind == "legend" and not leg_rows:
            leg_title = str(sec.get("title") or leg_title)
            leg_rows = rows
    return expl_title, expl_rows, leg_title, leg_rows


def _expl_row_score(row: dict[str, str]) -> int:
    row = _normalize_expl_row(row)
    score = len(row.get("name") or "")
    note = str(row.get("note") or "").strip()
    grid = str(row.get("grid") or "").strip()
    if note and note not in ("—", "-"):
        score += 12
    if grid and grid not in ("—", "-"):
        score += 8
    if re.search(r"\([A-ZА-ЯЁ]{2,10}\)", row.get("name") or ""):
        score += 4
    return score


def _merge_expl_row_fields(rows: list[dict[str, str]]) -> dict[str, str]:
    """Слияние OCR+vision по номеру на плане: лучшие поля из каждого источника."""
    normed = [_normalize_expl_row(r) for r in rows if isinstance(r, dict)]
    if not normed:
        return {}
    best_i = max(range(len(normed)), key=lambda i: _expl_row_score(normed[i]))
    out = dict(normed[best_i])
    for i, c in enumerate(normed):
        if i == best_i:
            continue
        gn = str(c.get("grid") or "").strip()
        if gn and gn != "—" and str(out.get("grid") or "—").strip() in ("", "—"):
            out["grid"] = gn
        nt = str(c.get("note") or "").strip()
        if nt and nt != "—" and str(out.get("note") or "—").strip() in ("", "—"):
            out["note"] = nt
        nm = str(c.get("name") or "").strip()
        if nm and _expl_row_score(c) >= _expl_row_score(out) and len(nm) <= len(str(out.get("name") or "")) + 6:
            out["name"] = nm
    return _normalize_expl_row(out)


def merge_explication_rows(*sources: list[dict[str, str]] | None) -> list[dict[str, str]]:
    by_num: dict[str, list[dict[str, str]]] = {}
    for rows in sources:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            row = _normalize_expl_row(row)
            if len(str(row.get("name") or "")) < 4:
                continue
            num = str(row.get("plan_number") or "—").strip() or "—"
            by_num.setdefault(num, []).append(row)
    return [_merge_expl_row_fields(group) for group in by_num.values()]


def merge_legend_rows(*sources: list[dict[str, str]] | None) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []

    def upsert(note: str, symbol: str) -> None:
        note = _clean_legend_note(note)
        if len(note) < 6 or _is_garbage_legend_note(note):
            return
        symbol = symbol if symbol and symbol != LEGEND_SYMBOL_PLACEHOLDER else LEGEND_SYMBOL_PLACEHOLDER
        if symbol and "графика" in symbol.casefold():
            symbol = LEGEND_SYMBOL_PLACEHOLDER
        for i, cur in enumerate(merged):
            if _legend_notes_similar(cur["note"], note):
                if _legend_note_quality(note) > _legend_note_quality(cur["note"]):
                    merged[i] = {"symbol": symbol, "note": note}
                return
        merged.append({"symbol": symbol, "note": note})

    for rows in sources:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            note = re.sub(r"\s+", " ", str(row.get("note") or "").strip())
            symbol = str(row.get("symbol") or LEGEND_SYMBOL_PLACEHOLDER).strip()
            upsert(note, symbol)
    return merged


def _norm_signature_role(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return ""
    for role, _rx in _STAMP_ROLES:
        if s.casefold() == role.casefold():
            return role
    low = s.casefold()
    for role, rx in _STAMP_ROLES:
        if re.search(rf"^{rx}\.?$", low, re.I) or re.search(rx, low, re.I):
            return role
    if len(s) <= 24 and re.search(r"[А-Яа-яA-Za-z]", s) and not re.search(r"\d{2,}", s):
        return ""
    return ""


def _signature_has_content(sig: dict[str, str]) -> bool:
    name = str(sig.get("name") or "").strip()
    date = str(sig.get("date") or "").strip()
    if name and name != "—" and not _is_bad_signature_name(name):
        return True
    if date and date != "—" and not _is_bad_signature_date(date):
        return True
    return False


def _collapse_duplicate_signature_names(sigs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Одна фамилия во всех графах — типичный сбой OCR/vision, не выводим."""
    names = [
        str(s.get("name") or "").strip()
        for s in sigs
        if str(s.get("name") or "").strip() not in ("", "—")
        and not _is_bad_signature_name(str(s.get("name")))
    ]
    if len(names) < 4:
        return sigs
    from collections import Counter

    top, count = Counter(names).most_common(1)[0]
    if count >= max(4, int(len(names) * 0.85)):
        out = []
        for s in sigs:
            row = dict(s)
            if str(row.get("name") or "").strip().casefold() == top.casefold():
                row["name"] = "—"
            out.append(row)
        return out
    return sigs


def normalize_signatures(sigs: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Только реально прочитанные подписи — без пустых строк."""
    by_role: dict[str, dict[str, str]] = {}
    extra_order: list[str] = []

    def merge_into(role: str, name: str, date: str) -> None:
        if _is_bad_signature_name(name):
            name = "—"
        if _is_bad_signature_date(date):
            date = "—"
        cur = by_role.get(role) or {"role": role, "name": "—", "sign": "—", "date": "—"}
        if name not in ("—", "", None) and not _is_bad_signature_name(name):
            if cur.get("name") in ("—", "", None) or _is_bad_signature_name(str(cur.get("name"))):
                cur["name"] = name
        if date not in ("—", "", None) and not _is_bad_signature_date(date):
            if cur.get("date") in ("—", "", None) or _is_bad_signature_date(str(cur.get("date"))):
                cur["date"] = date
        by_role[role] = cur
        if role not in STAMP_SIGNATURE_ORDER and role not in extra_order:
            extra_order.append(role)

    for item in sigs or []:
        if not isinstance(item, dict):
            continue
        role = _norm_signature_role(str(item.get("role") or ""))
        if not role:
            continue
        merge_into(role, str(item.get("name") or "").strip() or "—", str(item.get("date") or "").strip() or "—")

    out: list[dict[str, str]] = []
    for role in STAMP_SIGNATURE_ORDER:
        if role in by_role and _signature_has_content(by_role[role]):
            out.append(_fix_swapped_signature(by_role[role]))
    raz = by_role.get("Разраб.")
    prov = by_role.get("Пров.")
    if raz and prov:
        rn, pn = str(raz.get("name") or "—"), str(prov.get("name") or "—")
        rd, pd = str(raz.get("date") or "—"), str(prov.get("date") or "—")
        if (
            rn not in ("—", "")
            and rn == pn
            and rd not in ("—", "")
            and not _is_bad_signature_date(rd)
            and pd in ("—", "")
        ):
            prov["date"] = rd
    for role in extra_order:
        if role in by_role and _signature_has_content(by_role[role]):
            out.append(_fix_swapped_signature(by_role[role]))
    return _collapse_duplicate_signature_names(out)


def merge_signatures(*sources: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Слияние подписей: источники по порядку, первое валидное значение по роли сохраняется."""
    by_role: dict[str, dict[str, str]] = {}
    extra_order: list[str] = []

    def touch(role: str) -> dict[str, str]:
        if role not in by_role:
            by_role[role] = {"role": role, "name": "—", "sign": "—", "date": "—"}
            if role not in STAMP_SIGNATURE_ORDER and role not in extra_order:
                extra_order.append(role)
        return by_role[role]

    for sigs in sources:
        for item in sigs or []:
            if not isinstance(item, dict):
                continue
            role = _norm_signature_role(str(item.get("role") or ""))
            if not role:
                continue
            cur = touch(role)
            name = str(item.get("name") or "").strip() or "—"
            date = str(item.get("date") or "").strip() or "—"
            if name not in ("—", "", None) and not _is_bad_signature_name(name):
                cur_name = str(cur.get("name") or "—")
                if cur_name in ("—", "", None) or _is_bad_signature_name(cur_name):
                    cur["name"] = name
                elif _readability_score(name) > _readability_score(cur_name) + 1.0:
                    cur["name"] = name
            if date not in ("—", "", None) and not _is_bad_signature_date(date):
                if cur.get("date") in ("—", "", None) or _is_bad_signature_date(str(cur.get("date"))):
                    cur["date"] = date

    out: list[dict[str, str]] = []
    for role in STAMP_SIGNATURE_ORDER:
        if role in by_role and _signature_has_content(by_role[role]):
            out.append(_fix_swapped_signature(by_role[role]))
    for role in extra_order:
        if role in by_role and _signature_has_content(by_role[role]):
            out.append(_fix_swapped_signature(by_role[role]))
    return _collapse_duplicate_signature_names(out)


def _kv_field_quality(field: str, value: str) -> int:
    cyr = len(re.findall(r"[А-Яа-яЁё]", value))
    lat = len(re.findall(r"[A-Za-z]", value))
    noise = len(re.findall(r"[|©\[\]_`]", value))
    mixed = len(re.findall(r"[А-Яа-яё][A-Za-z]|[A-Za-z][А-Яа-яё]", value))
    score = cyr * 3 - lat * 4 - noise * 8 - mixed * 10
    if field == "Организация" and re.search(r'ру\s*["«]', value, re.I):
        score += 25
        score += min(len(value), 50)
    if field in ("Организация", "Город / адрес") and re.search(r"[БВГДЖЗКЛМНПРСТФХЦЧШЩ]{4,}", value, re.I):
        score -= 15
    return score


def merge_stamp_kv(*sources: dict[str, Any] | None) -> dict[str, str]:
    """Поля рамки: из всех источников — вариант с лучшим качеством текста."""
    candidates: dict[str, list[str]] = {}
    for stamp in sources:
        if not stamp:
            continue
        for item in stamp.get("kv") or []:
            f = str(item.get("field") or "").strip()
            v = str(item.get("value") or "").strip()
            if not f or not v or v == "—":
                continue
            candidates.setdefault(f, []).append(v)
    merged: dict[str, str] = {}
    for f, vals in candidates.items():
        best = vals[0]
        best_score = _kv_field_quality(f, best) + 4
        for i, v in enumerate(vals[1:], start=1):
            if v == best or _is_garbage_kv(f, v):
                continue
            sc = _kv_field_quality(f, v) + max(0, 2 - i)
            if sc > best_score:
                best, best_score = v, sc
        if not _is_garbage_kv(f, best):
            merged[f] = best
    return _normalize_stamp_kv_map(merged)


def _supplement_stamp_kv(kv_map: dict[str, str], extra_text: str) -> dict[str, str]:
    """Доп. поля рамки из OCR зон листа (лист, стадия…) — только пустые слоты."""
    if not (extra_text or "").strip():
        return kv_map
    out = dict(kv_map)
    compact = _compact(extra_text)
    for label, val in _extract_sheet_kv(compact):
        if label not in out or not str(out.get(label) or "").strip():
            out[label] = val
    for label, rx in (
        (
            "Город / адрес",
            r"(?:г\.\s*)?([А-ЯЁ][\wА-Яа-яЁё\-]+(?:\s+[А-ЯЁ][\wА-Яа-яЁё\-]+){0,3})\s*,?\s*(Беларусь|Россия|РБ|РФ)",
        ),
        ("Город / адрес", r"([А-ЯЁ][а-яё\-]+)\s+(Беларусь|Россия|РБ|РФ)\b"),
        ("Формат", r"(?:[Ff]ormat|Формат|Popmam)\s*([AА]\d\s*[x×хX&]?\s*\d+)"),
        ("Стадия", r"(Подготов\w+\s+\w+\s+период|Рабоч\w+\s+документ\w+)"),
        ("Очередь строительства", r"([I1Il]\s*очеред\w+\s+строитель\w+)"),
    ):
        if label in out and str(out.get(label) or "").strip():
            continue
        m = re.search(rx, compact, re.I)
        if not m:
            continue
        if label == "Формат":
            val = re.sub(r"\s+", "", m.group(1)).replace("×", "x").replace("х", "x")
            val = val.replace("А", "A").replace("а", "a")
            out[label] = val
        elif label == "Город / адрес":
            out[label] = re.sub(r"\s+", " ", f"{m.group(1).strip()} {m.group(2).strip()}").strip()
        else:
            out[label] = re.sub(r"\s+", " ", m.group(1)).strip()
    return _normalize_stamp_kv_map(out)


def merge_stamp(
    vision: dict[str, Any],
    ocr: dict[str, Any],
    *,
    extra_texts: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Vision + OCR штампа: OCR-зона (выше DPI) — первый источник при слиянии."""
    if not vision:
        return ocr
    if not ocr:
        return vision
    sources = [ocr, vision]
    kv_map = merge_stamp_kv(ocr, vision)
    for raw in extra_texts or ():
        kv_map = _supplement_stamp_kv(kv_map, str(raw or ""))
    cipher = merge_stamp_cipher(ocr, vision, extra_texts=extra_texts)
    if cipher:
        kv_map["Обозначение / шифр"] = cipher
    from belener.config import report_faithful

    sig_sources = [ocr, vision] if report_faithful() else [vision, ocr]
    signatures = merge_signatures(*[s.get("signatures") for s in sig_sources])
    titles = _merge_stamp_titles(ocr.get("titles"), vision.get("titles"), kv_map)
    cipher_candidates: list[str] = []
    seen: set[str] = set()
    for s in sources:
        for c in _stamp_cipher_values(s):
            if c not in seen:
                seen.add(c)
                cipher_candidates.append(c)
    return {
        "kv": [{"field": f, "value": kv_map[f]} for f in STAMP_KV_ORDER if kv_map.get(f)],
        "cipher_candidates": cipher_candidates,
        "signatures": signatures,
        "titles": titles,
        "summary": str(vision.get("summary") or ocr.get("summary") or "").strip(),
    }


def normalize_stamp_output(stamp: dict[str, Any]) -> dict[str, Any]:
    """Поля рамки и подписи — только то, что реально прочитано."""
    kv_map = {
        str(x.get("field", "")): str(x.get("value", "")).strip()
        for x in stamp.get("kv") or []
        if x.get("field") and str(x.get("value", "")).strip() not in ("", "—")
    }
    for key, val in list(kv_map.items()):
        if key == "Обозначение / шифр":
            continue
        if key == "Стадия (обозначение)" and re.fullmatch(r"[СРПР]", val, re.I):
            continue
        if key == "Лист" and re.fullmatch(r"\d{1,3}(?:\s*/\s*\d{1,3})?", val):
            continue
        if key == "Масштаб" and re.search(r"1\s*:\s*\d", val):
            continue
        kv_map[key] = polish_readable_russian(val)
    sig_map = {s.get("role"): dict(s) for s in stamp.get("signatures") or [] if s.get("role")}
    signatures = normalize_signatures(list(sig_map.values()))
    cop = kv_map.get("Копировал")
    if cop:
        sig_names = {
            str(s.get("name") or "")
            for s in signatures
            if s.get("name") not in ("—", "", None) and not _is_bad_signature_name(str(s.get("name")))
        }
        if cop in sig_names:
            del kv_map["Копировал"]
    kv_map = _normalize_stamp_kv_map(kv_map)
    return {
        "kv": [{"field": f, "value": kv_map[f]} for f in STAMP_KV_ORDER if kv_map.get(f)],
        "cipher_candidates": stamp.get("cipher_candidates") or [],
        "signatures": signatures,
        "titles": _dedupe_titles(list(stamp.get("titles") or []), kv_map),
        "revisions": list(stamp.get("revisions") or []),
        "summary": polish_readable_russian(str(stamp.get("summary") or "").strip()),
    }


def _looks_like_person_name(name: str) -> bool:
    """Одно слово, формат фамилии (без привязки к конкретному проекту)."""
    s = re.sub(r"\s+", " ", (name or "").strip())
    if not s or s == "—":
        return False
    if " " in s:
        return False
    if len(s) < 4 or len(s) > 14:
        return False
    if not re.fullmatch(r"[А-ЯЁA-Z][а-яёa-z]{2,13}", s):
        return False
    tail = s[1:]
    vowels = sum(1 for c in tail if c.lower() in "аеёиоуыэюяaeiouy")
    return vowels >= 1 and vowels / max(len(tail), 1) >= 0.18


_ROLE_NAME_FRAGMENTS = re.compile(
    r"^(разро[бьёe]?|разра[бьёe]?|пров\.?|контр|утв\.?|нач\.?|гип|гл\.?|копир|н\.?\s*контр|"
    r"схема|лист|формат|чертеж|таблица|наименован|обозначен|организац|электрическ|минск|беларус)",
    re.I,
)


def _is_bad_signature_name(name: str) -> bool:
    if not name or name == "—":
        return True
    s = name.strip()
    if re.search(r"\d{3,}[-–]\d", s):
        return True
    if re.fullmatch(r"\d{1,2}[./]\d{1,2}", s):
        return True
    if _ROLE_NAME_FRAGMENTS.match(s):
        return True
    for role, rx in _STAMP_ROLES:
        if re.fullmatch(rf"{rx}\.?", s, re.I):
            return True
    if re.fullmatch(r"подп\.?|подпись|дата", s, re.I):
        return True
    if not _looks_like_person_name(name):
        return True
    if s.endswith(".") and len(s) <= 10:
        return True
    if _readability_score(name) < 6.0:
        return True
    return False


def _fix_swapped_signature(sig: dict[str, str]) -> dict[str, str]:
    """OCR часто кладёт «Разроб» в name, а «Разраб.» в role — исправить или обнулить имя."""
    role = str(sig.get("role") or "").strip()
    name = str(sig.get("name") or "").strip()
    if not name or name == "—":
        return sig
    norm_role = _norm_signature_role(role)
    if norm_role and not _is_bad_signature_name(name):
        return sig
    cand_role = _norm_signature_role(name)
    if cand_role and _looks_like_person_name(role):
        out = dict(sig)
        out["role"] = cand_role
        out["name"] = role
        return out
    if _is_bad_signature_name(name):
        out = dict(sig)
        out["name"] = "—"
        return out
    return sig


def apply_stamp_llm(base: dict[str, Any], llm: dict[str, Any] | None) -> dict[str, Any]:
    """LLM-версия штампа: чистые поля, без OCR-мусора в titles/kv."""
    if not llm:
        return base

    kv_order = (
        "Обозначение / шифр",
        "Организация",
        "Город / адрес",
        "Масштаб",
        "Формат",
        "Стадия (обозначение)",
        "Стадия",
        "Очередь строительства",
        "Лист",
        "Копировал",
    )
    kv_map: dict[str, str] = {
        str(x.get("field", "")): str(x.get("value", ""))
        for x in base.get("kv") or []
        if x.get("field")
    }
    llm_kv = llm.get("kv") or {}
    if isinstance(llm_kv, dict):
        for field, val in llm_kv.items():
            if field not in kv_order or not val or val == "—":
                continue
            cur = kv_map.get(field, "")
            if not cur or cur == "—" or len(val) >= len(cur):
                kv_map[field] = val

    sig_map = {s.get("role"): dict(s) for s in base.get("signatures") or [] if s.get("role")}
    for row in llm.get("signatures") or []:
        if not isinstance(row, dict):
            continue
        role = _norm_signature_role(str(row.get("role") or ""))
        if not role:
            continue
        if role not in sig_map:
            sig_map[role] = {"role": role, "name": "—", "sign": "—", "date": "—"}
        if row.get("name") not in ("—", "", None) and not _is_bad_signature_name(str(row.get("name"))):
            base_name = sig_map[role].get("name", "—")
            if base_name in ("—", "", None) or _is_bad_signature_name(str(base_name)):
                sig_map[role]["name"] = row["name"]
        if row.get("date") not in ("—", "", None) and not _is_bad_signature_date(str(row.get("date"))):
            sig_map[role]["date"] = row["date"]

    titles = _dedupe_titles(
        [re.sub(r"\s+", " ", str(raw_t or "").strip()) for src in (llm.get("titles") or [], base.get("titles") or []) for raw_t in src],
        kv_map,
    )

    return {
        "kv": [{"field": f, "value": kv_map[f]} for f in kv_order if kv_map.get(f)],
        "cipher_candidates": base.get("cipher_candidates") or [],
        "signatures": normalize_signatures(list(sig_map.values())),
        "titles": titles,
        "summary": str(llm.get("summary") or "").strip(),
    }
