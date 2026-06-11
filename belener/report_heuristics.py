"""Универсальные фильтры OCR перед отчётом (без привязки к проекту/номеру листа)."""

from __future__ import annotations

import re


def _tail(text: str, n: int = 5000) -> str:
    t = (text or "").strip()
    return t[-n:] if len(t) > n else t


def _cyrillic_words(s: str, min_len: int = 3) -> int:
    return len(re.findall(rf"[А-Яа-яЁё]{{{min_len},}}", s))


def _mostly_junk(s: str) -> bool:
    """Строка из OCR-схемы: цифры, символы, короткие фрагменты."""
    t = (s or "").strip()
    if len(t) < 8:
        return True
    letters = len(re.findall(r"[А-Яа-яЁёA-Za-z]", t))
    digits = len(re.findall(r"\d", t))
    symbols = len(re.findall(r"[^\w\sА-Яа-яЁё]", t, re.UNICODE))
    if letters < 6 and digits >= 3:
        return True
    if digits > letters * 2 and _cyrillic_words(t, 4) < 2:
        return True
    if symbols > letters and _cyrillic_words(t, 4) < 2:
        return True
    return False


def is_plausible_tt_line(line: str) -> bool:
    """Нумерованный пункт ТТ/примечаний — не координаты схемы."""
    s = (line or "").strip()
    m = re.match(r"^(\d{1,2})\s+[\.\)]?\s*(.+)$", s)
    if not m:
        return False
    tail = m.group(2).strip()
    if len(tail) < 28:
        return False
    if _mostly_junk(tail):
        return False
    if _cyrillic_words(tail, 4) < 2:
        return False
    return True


def extract_tt_lines(text: str, *, max_items: int = 30) -> list[str]:
    from belener.parse import parse_numbered_notes

    out: list[str] = []
    seen: set[str] = set()
    for note in parse_numbered_notes(text or "", max_notes=max_items * 2):
        s = str(note or "").strip()
        if not is_plausible_tt_line(s):
            continue
        key = re.sub(r"\s+", " ", s.casefold())[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _extract_spec_region(text: str) -> str:
    """Фрагмент вокруг таблицы спецификации — по якорям, не по номеру документа."""
    t = text or ""
    for pat in (
        r"(?is)(?:\|\s*)?спецификация\b.*?(?=рабочие\s+парамет|технич|условн|основная\s+надпис|штамп|$)",
        r"(?is)\bпоз\.?\s*обозначение\b.*?(?=рабочие\s+парамет|технич|итого\s*:|условн|$)",
        r"(?is)\bнаименование\b.*?\bкол\.?\b.*?(?=рабочие\s+парамет|технич|итого\s*:|$)",
    ):
        m = re.search(pat, t)
        if m and len(m.group(0)) > 60:
            return m.group(0)
    return ""


def extract_spec_rows(text: str, *, max_rows: int = 50) -> list[dict[str, str]]:
    from belener.parse import parse_specification

    region = _extract_spec_region(text)
    if not region:
        return []
    rows = parse_specification(region, max_rows=max_rows)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Наименование") or row.get("Обозначение") or "").strip()
        if len(name) < 4 or _mostly_junk(name):
            continue
        key = "|".join(str(row.get(c) or "") for c in ("Поз.", "Обозначение", "Наименование"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def extract_stamp_fields(text: str) -> list:
    from belener.parse import STAMP_SIGNATURE_ORDER, _is_bad_signature_name, parse_stamp

    stamp = parse_stamp(_tail(text))
    fields: list = []
    seen: set[str] = set()

    def add(label: str, val: str) -> None:
        v = re.sub(r"\s+", " ", str(val or "").strip())
        if len(v) < 3 or v in seen or v == "—":
            return
        seen.add(v)
        fields.append((label, v))

    for item in stamp.get("kv") or []:
        if not isinstance(item, dict):
            continue
        add(str(item.get("field") or ""), str(item.get("value") or ""))

    for title in stamp.get("titles") or []:
        t = str(title or "").strip()
        if len(t) > 18 and _cyrillic_words(t, 4) >= 2:
            add("Наименование", t[:240])

    sig_rows: list[tuple[str, str, str]] = []
    for s in stamp.get("signatures") or []:
        if not isinstance(s, dict):
            continue
        role = str(s.get("role") or "").strip()
        name = str(s.get("name") or "").strip()
        date = str(s.get("date") or "—").strip() or "—"
        if not role or _is_bad_signature_name(name):
            continue
        if name in ("—", "") or re.fullmatch(r"[\d.,]+", name):
            continue
        sig_rows.append((role, name, date))

    if not sig_rows:
        by_role = {str(s.get("role") or ""): s for s in stamp.get("signatures") or [] if isinstance(s, dict)}
        for role in STAMP_SIGNATURE_ORDER:
            s = by_role.get(role)
            if not s:
                continue
            name = str(s.get("name") or "").strip()
            if name and not _is_bad_signature_name(name):
                sig_rows.append((role, name, str(s.get("date") or "—")))

    out: list = list(fields)
    if sig_rows:
        out.append(("__signatures__", sig_rows))
    return out
