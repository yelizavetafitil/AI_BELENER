"""
Разбор перечня аппаратуры из OCR: без подгонки под проект.

Подходы из open-source (зонирование таблиц, поячеечный OCR):
- engineering-drawing-extractor — изоляция таблицы от поля чертежа
- CV-сетка в belener.cv_tables — ячейки → колонки через tab
"""

from __future__ import annotations

import re
from typing import Any

from belener.anchors import EXPLICATION_START_RX, LEGEND_START_RX, SPECIFICATION_START_RX

_SPEC_COLS = ("Поз.", "Обозначение", "Наименование", "Кол.", "Масса ед., кг", "Примечание")

_SPEC_COL_MARKERS = re.compile(
    r"(?:поз\.?|обозначен|наименован|кол\.?|масса|ед\.?|примечан)",
    re.I,
)

# Начало позиции перечня: «161 Блок …» (2–3 цифры, не «1=6»)
_POS_START_RX = re.compile(r"(?:^|(?<=\s))(\d{2,3})\s+(?=[А-Яа-яЁё])", re.I)


def _compact(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _duplicate_phrase(text: str, min_len: int = 14) -> bool:
    """Повтор фразы в одной строке — типичный склей схемы/подписей."""
    t = _compact(text).casefold()
    if len(t) < min_len * 2:
        return False
    for size in range(min(len(t) // 2, 40), min_len - 1, -1):
        chunk = t[:size]
        if t.count(chunk) >= 2:
            return True
    return False


def qty_looks_like_panel_number(name: str, qty: str) -> bool:
    """Кол. не путать с «панель 82», «л. 2» и т.п."""
    q = (qty or "").strip()
    if not q or q == "—" or not re.fullmatch(r"\d{1,3}", q):
        return False
    n = name.casefold()
    if re.search(rf"(?:панел\w*|№\s*|л\.?\s*|зал\w*)\s*{q}\b", n):
        return True
    if re.search(rf"\b{q}\s*(?:панел|опу|зал)", n):
        return True
    return False


def fix_spec_row_qty(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    name = str(out.get("Наименование") or "").strip()
    qty = str(out.get("Кол.") or "").strip()
    if qty_looks_like_panel_number(name, qty):
        out["Кол."] = "—"
    elif qty and re.fullmatch(r"\d+[,.]\d+\s*м", qty.strip(), re.I):
        pass
    elif qty and not re.fullmatch(r"\d{1,3}", qty):
        out["Кол."] = "—"
    return out


def is_schematic_caption_row(row: dict[str, str]) -> bool:
    """Подпись на схеме, ошибочно попавшая в перечень (универсальные признаки)."""
    pos = str(row.get("Поз.") or "").strip()
    desig = str(row.get("Обозначение") or "").strip()
    name = str(row.get("Наименование") or "").strip()
    if not name:
        return True
    if re.fullmatch(r"\d{1,3}", pos):
        return False
    if _duplicate_phrase(name):
        return True
    if qty_looks_like_panel_number(name, str(row.get("Кол.") or "")):
        return True
    if pos in ("—", "") and re.fullmatch(r"[А-ЯA-ZЁ]{1,2}\.?", desig):
        return True
    if pos in ("—", "") and re.search(r"\bI\s*=\s*\d", name, re.I):
        return True
    _desig_norm = re.sub(r"\s+", "", desig)
    if pos in ("—", "") and re.fullmatch(
        r"(?:[A-Za-zА-ЯЁ]{1,4}\d{1,3})(?:,\s*[A-Za-zА-ЯЁ]{1,4}\d{1,3})*",
        _desig_norm,
        re.I,
    ):
        return True
    if pos in ("—", "") and re.fullmatch(r"[A-Za-zА-ЯЁ]{1,4}\d{1,3}", _desig_norm, re.I):
        if len(name) < 55 or re.search(
            r":\s*\d|X\d|измерен|переключ|отключ|резер|выключ|капрон|напряж",
            name,
            re.I,
        ):
            return True
    try:
        from belener.table_quality import mixed_script_ocr_glitch, ocr_line_implausible_for_legend

        if pos in ("—", "") and (
            mixed_script_ocr_glitch(desig)
            or mixed_script_ocr_glitch(name)
            or ocr_line_implausible_for_legend(desig)
        ):
            return True
    except ImportError:
        pass
    if (
        pos in ("—", "")
        and len(desig) <= 8
        and not re.search(r"[\d\-]", desig)
        and len(name) < 50
        and not re.search(r"\b(?:гост|ту\s*\d|ст\d)\b", name, re.I)
    ):
        return True
    if len(name) > 100 and name.count(",") >= 2 and pos in ("—", ""):
        return True
    if re.search(r"[<«][\wА-ЯA-Z]|[\wА-ЯA-Z][>»]", name):
        return True
    return False


def split_glued_spec_line(ln: str) -> list[str]:
    """Разбить длинную OCR-строку на несколько позиций по номеру в начале сегмента."""
    s = _compact(ln)
    if len(s) < 40:
        return [s]
    starts = [m.start() for m in _POS_START_RX.finditer(s)]
    if not starts:
        return [s]
    parts: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(s)
        parts.append(_compact(s[start:end]))
    if parts and all(re.match(r"^\d{2,3}\s+", p) for p in parts):
        return parts
    return [s]


def is_bom_like_legend_note(note: str) -> bool:
    """Текст похож на перечень/подписи схемы, а не на строку условных обозначений."""
    n = _compact(note)
    if len(n) < 28:
        return False
    if list(_POS_START_RX.finditer(n)):
        return True
    if _duplicate_phrase(n):
        return True
    if re.search(r"\bI\s*=\s*\d|1\s*=\s*\d\s*[АA]", n, re.I):
        return True
    if re.match(r"^[А-ЯA-ZЁ]{1,2}\.\s", n) and re.search(
        r"панел\w*|зал\s*№|релейн\w*\s+зал|опу\b",
        n,
        re.I,
    ):
        return True
    if re.match(r"^[А-ЯA-ZЁ]{1,2}\.?\s", n) and len(n) > 40:
        return True
    if len(n) > 70 and re.search(r"\d{1,3}\s+[А-Яа-яЁё]{4,}", n):
        return True
    return False


def is_spec_group_header_line(ln: str) -> bool:
    """Строка-заголовок группы («ОПУ, релейный зал…, панель N») — не позиция перечня."""
    s = _compact(ln)
    if len(s) < 12:
        return False
    if re.match(r"^\d{1,3}\s+", s):
        return False
    if re.search(r"^[A-ZА-ЯЁ]{1,4}\d|[\d]{1,3}[ТT]{1,2}\b|QF\d|TL\d|UG\d|SF\d", s, re.I):
        return False
    if len(_SPEC_COL_MARKERS.findall(s)) >= 2:
        return False
    letters = re.findall(r"[А-Яа-яЁё]", s)
    if len(letters) < 8:
        return False
    if re.search(
        r"панел\w*|зал\s*№|релейн\w*\s+зал|опу\b|участ\w*|блок\s+\d",
        s,
        re.I,
    ):
        return True
    if "," in s and len(s) >= 20 and not re.search(r"\bгост\b|\bту\s*\d", s, re.I):
        if not re.search(r"\d{1,3}\s+[А-Яа-яЁё]{5,}", s):
            return True
    return False


def explode_spec_ocr_lines(lines: list[str]) -> list[str]:
    from belener.parse import _is_column_header_line

    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if (
            re.search(SPECIFICATION_START_RX, s, re.I)
            and len(_SPEC_COL_MARKERS.findall(s)) >= 2
            and not _is_column_header_line(s)
        ):
            continue
        if re.search(LEGEND_START_RX, s, re.I) or re.search(EXPLICATION_START_RX, s, re.I):
            continue
        for part in split_glued_spec_line(s):
            if part:
                out.append(part)
    return out


def spec_row_fingerprint(row: dict[str, str]) -> str:
    name = _compact(str(row.get("Наименование") or "")).casefold()[:160]
    pos = str(row.get("Поз.") or "—").strip()
    desig = _compact(str(row.get("Обозначение") or "")).casefold()[:24]
    return f"{pos}|{desig}|{name}"


def dedupe_spec_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    best: dict[str, dict[str, str]] = {}
    for r in rows:
        pos = str(r.get("Поз.") or "—").strip()
        key = pos if re.fullmatch(r"\d{1,3}", pos) else spec_row_fingerprint(r)
        prev = best.get(key)
        name = str(r.get("Наименование") or "")
        if not prev or len(name) > len(str(prev.get("Наименование") or "")):
            best[key] = r
    return list(best.values())


_DESIG_TOKEN_RX = re.compile(
    r"^[A-ZА-ЯЁ][A-ZА-ЯЁ0-9]{0,5}(?:[.,]\s*[A-ZА-ЯЁ0-9]{0,5})*"
    r"|[\d]{1,2}ТТ|ТТ[\d]|QF\d|SF\d|UG\d|TL\d|UCT|РПВ|SG\d|TR\d|WA\d",
    re.I,
)

# Слова-наименования, ошибочно попавшие в колонку «Обозначение» (OCR/парсер)
_GENERIC_DESIG_RX = re.compile(
    r"^(?:выключател\w*|трансформат\w*|реле\w*|ампермет\w*|контактор\w*|"
    r"блок\w*|антенн\w*|дроссел\w*|предохран\w*|разъедин\w*|"
    r"автомат\w*|панел\w*|шин\w*|клемм\w*|счетчик\w*|изолятор\w*|"
    r"разъем\w*|соединител\w*|коробк\w*|щит\w*)$",
    re.I,
)


def is_generic_equipment_word(text: str) -> bool:
    """Типовое наименование аппарата — не код SF31 / UCT3.1."""
    s = _compact(text)
    if not s or len(s) < 5:
        return False
    return bool(_GENERIC_DESIG_RX.fullmatch(s))


def position_matches_panel_in_name(pos: str, name: str) -> bool:
    """Позиция совпала с номером панели в заголовке группы — не строка перечня."""
    p = (pos or "").strip()
    n = (name or "").casefold()
    if not p.isdigit() or not n:
        return False
    m = re.search(r"панел\w*\s*(\d{1,3})", n, re.I)
    if m and p == m.group(1):
        return True
    if re.search(rf"\bпанел\w*\s*{p}\b", n, re.I):
        return True
    return False


def is_valid_bom_data_row(row: dict[str, str]) -> bool:
    """Строка перечня аппаратуры, а не заголовок группы / мусор OCR."""
    if row.get("_group"):
        return True
    pos = str(row.get("Поз.") or "").strip()
    desig = str(row.get("Обозначение") or "").strip()
    name = str(row.get("Наименование") or "").strip()
    if not name or name == "—":
        return False
    if is_spec_group_header_line(name) or is_spec_group_header_line(f"{pos} {name}"):
        return False
    if position_matches_panel_in_name(pos, name):
        return False
    if re.match(r"^0\d{2}$", pos):
        return False
    if desig not in ("—", "") and is_generic_equipment_word(desig):
        return False
    if desig not in ("—", "") and _DESIG_TOKEN_RX.search(desig.replace(" ", "")):
        return True
    if re.search(r"гост\s*[\d\-]|ту\s*[\d\-]", desig, re.I):
        if re.search(r"полос|ст\s*3|сталь|тяга|держатель|водогаз", name, re.I):
            return True
        if re.search(r"гост", desig, re.I) and len(name) >= 4:
            return True
    if re.fullmatch(r"\d{1,3}", pos) and int(pos) >= 40 and desig in ("—", ""):
        return False
    if re.fullmatch(r"\d{1,3}", pos) and len(name) >= 8:
        if re.search(
            r"выключ|трансформ|реле|блок|антенн|радиомод|гост|ту\s*\d|ампер|контактор"
            r"|тяга|водогаз|полос|держатель|котельн|труб",
            name,
            re.I,
        ):
            return True
    return False


def filter_spec_rows(
    rows: list[dict],
    *,
    is_header_row: Any = None,
    is_garbage_name: Any = None,
) -> list[dict[str, str]]:
    """Финальная фильтрация строк перечня."""
    out: list[dict[str, str]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        row = {h: str(r.get(h) or "—") for h in _SPEC_COLS}
        if is_header_row and is_header_row(row):
            continue
        name = str(row.get("Наименование") or "").strip()
        if not name or name in ("—", ""):
            continue
        if is_garbage_name and is_garbage_name(name):
            continue
        if r.get("_group"):
            row["_group"] = "1"
            out.append(row)
            continue
        row = fix_spec_row_qty(row)
        if is_schematic_caption_row(row):
            continue
        if not is_valid_bom_data_row(row):
            continue
        out.append(row)
    return dedupe_spec_rows(out)


def salvage_spec_rows_from_texts(
    texts: list[str],
    *,
    parse_row: Any,
    is_caption: Any,
) -> list[dict[str, str]]:
    """Извлечь строки перечня из склеенного OCR (часто ошибочно попавшего в легенду)."""
    from belener.spec_extract import extract_spec_rows_from_messy_ocr

    out: list[dict[str, str]] = []
    for raw in texts:
        t = _compact(raw)
        if not t:
            continue
        for row in extract_spec_rows_from_messy_ocr(t, max_rows=25):
            if row.get("_group"):
                out.append(row)
                continue
            if is_caption and is_caption(row):
                continue
            out.append(row)
        chunks = split_glued_spec_line(t) if len(t) > 35 else [t]
        for part in chunks:
            part = _compact(part)
            if not re.match(r"^\d{1,3}\s+", part):
                continue
            row = parse_row(part)
            if row and not is_caption(row):
                pos = str(row.get("Поз.") or "").strip()
                name = str(row.get("Наименование") or "").strip()
                if not re.fullmatch(r"\d{1,3}", pos):
                    continue
                if re.match(r"^\d{1,3}\s", name):
                    continue
                if len(name) < 12 or (len(name) < 18 and "," not in name and ";" not in name):
                    continue
                out.append(row)
    return dedupe_spec_rows(out)


def legend_note_matches_spec(note: str, spec_rows: list[dict]) -> bool:
    """Легенда не должна дублировать строки перечня."""
    nk = re.sub(r"\W+", " ", _compact(note).casefold())[:140]
    if len(nk) < 10:
        return False
    for r in spec_rows:
        sk = re.sub(r"\W+", " ", _compact(str(r.get("Наименование") or "")).casefold())[:140]
        if not sk:
            continue
        if nk == sk or (len(nk) >= 20 and (nk in sk or sk in nk)):
            return True
    return False
