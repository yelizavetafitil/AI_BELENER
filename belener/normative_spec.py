"""Строки перечня из OCR с ОСТ/ГОСТ (разорванная шапка таблицы)."""

from __future__ import annotations

import re

from belener.spec_table import dedupe_spec_rows, fix_spec_row_qty


def _row(pos: str, desig: str, name: str, qty: str = "—", mass: str = "—") -> dict[str, str]:
    p = (pos or "").strip()
    if p and not re.fullmatch(r"\d{1,3}", p):
        p = "—"
    return fix_spec_row_qty(
        {
            "Поз.": p or "—",
            "Обозначение": (desig or "—").strip(),
            "Наименование": re.sub(r"\s+", " ", (name or "").strip()) or "—",
            "Кол.": qty if re.fullmatch(r"\d{1,3}", (qty or "").strip()) else "—",
            "Масса ед., кг": mass if re.search(r"\d", mass or "") else "—",
            "Примечание": "—",
        }
    )


def _clean_bom_name(raw: str) -> str:
    from belener.parse import _readability_score

    s = re.sub(r"\s+", " ", (raw or "").strip(" -|.,;"))
    if len(s) < 4 or len(s) > 90:
        return "—"
    if _readability_score(s) < 7.5:
        return "—"
    if re.search(r"^[a-zA-Z]{3,}$", s):
        return "—"
    if re.search(r"поз\.?|обознач|наименован|масса\s+ед|примеч", s, re.I):
        return "—"
    return s


def _name_from_context(ctx: str, ref: str) -> str:
    s = (ctx or "")
    for token in ref.split():
        s = re.sub(re.escape(token), " ", s, flags=re.I)
    s = re.sub(r"(?i)\b(?:ост|oct|ost|gost|гост)\b", " ", s)
    s = re.sub(r"[\d\+|\-–—]{2,}", " ", s)
    chunks = re.findall(r"[А-Яа-яЁё][А-Яа-яЁё\-]{2,40}", s)
    for chunk in chunks:
        name = _clean_bom_name(chunk)
        if name != "—":
            return name
    joined = _clean_bom_name(" ".join(chunks[:3]))
    return joined


def _scan_name_in_text(text: str, ref: str) -> str:
    """Имя на соседних строках после обозначения (Электроды, Комплекта …)."""
    body = ref
    m = re.match(r"^\d{1,3}\s+(.+)$", ref)
    if m:
        body = m.group(1)
    num_m = re.search(r"[\d]{4}[\d\-]+", body)
    needle = num_m.group(0) if num_m else body.split()[-1] if body.split() else ""
    if not needle:
        return "—"
    for i, ln in enumerate((text or "").splitlines()):
        if needle not in ln and body.split()[-1] not in ln:
            continue
        for j in range(i, min(i + 4, len((text or "").splitlines()))):
            cand = (text or "").splitlines()[j].strip()
            if re.search(r"(?i)(?:ост|гост|gost|oct)", cand):
                continue
            nm = _clean_bom_name(cand)
            if nm != "—":
                return nm
            m2 = re.search(r"[А-Яа-яЁё]{5,}", cand)
            if m2:
                nm = _clean_bom_name(m2.group(0))
                if nm != "—":
                    return nm
    return "—"


def _scan_structural_name(text: str) -> str:
    for pat in (
        r"[Оо]пор[\wа-яёА-ЯЁ]{0,18}",
        r"[Кк]омплект[\wа-яёА-ЯЁ]{0,18}",
        r"[Ээ]лектрод[\wа-яёА-ЯЁ]{0,12}",
        r"[Дд]ержател[\wа-яёА-ЯЁ]{0,18}",
    ):
        m = re.search(pat, text or "")
        if m:
            nm = _clean_bom_name(m.group(0))
            if nm != "—":
                return nm
    return "—"


def parse_normative_bom_rows(text: str, *, max_rows: int = 40) -> list[dict[str, str]]:
    """Позиции перечня по ОСТ/ГОСТ в OCR-тексте зоны таблицы."""
    if not (text or "").strip():
        return []
    if not re.search(r"(?i)(?:гост|gost|ост|oct|ost)\s*[\d\+]", text):
        return []

    from belener.normative_refs import extract_normative_refs

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in extract_normative_refs(text):
        ref = str(item.get("ref") or "").strip()
        if not ref:
            continue
        pos = "—"
        body = ref
        pm = re.match(r"^(\d{1,3})\s+(.+)$", ref)
        if pm:
            pos = pm.group(1)
            body = pm.group(2).strip()
        ctx = str(item.get("context") or "")
        name = _name_from_context(ctx, ref)
        if name == "—":
            name = _scan_name_in_text(text, ref)
        if name == "—":
            name = _scan_structural_name(ctx) or _scan_structural_name(text)
        key = (pos, body.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(_row(pos, body, name))
        if len(rows) >= max_rows:
            break

    for ln in (text or "").splitlines():
        m = re.match(r"^(\d{1,3})\s+([А-Яа-яЁё][А-Яа-яЁё\-]{4,50})$", ln.strip())
        if not m or int(m.group(1)) > 99:
            continue
        pos, name = m.group(1), _clean_bom_name(m.group(2))
        if name == "—":
            continue
        key = (pos, name.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(_row(pos, "—", name))
        if len(rows) >= max_rows:
            break

    return dedupe_spec_rows(rows)


def normative_bom_plausible(rows: list[dict] | None) -> bool:
    rs = [r for r in (rows or []) if isinstance(r, dict)]
    if not rs:
        return False
    std = sum(
        1
        for r in rs
        if re.search(r"(?i)(?:гост|ост|ту\s*\d)", str(r.get("Обозначение") or ""))
    )
    named = sum(1 for r in rs if str(r.get("Наименование") or "—").strip() not in ("", "—"))
    return std >= 1 and (named >= 1 or std >= 2)
