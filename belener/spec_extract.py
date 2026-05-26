"""Извлечение строк перечня из «грязного» OCR (универсальные шаблоны ГОСТ)."""

from __future__ import annotations

import re

from belener.spec_table import (
    _SPEC_COLS,
    dedupe_spec_rows,
    fix_spec_row_qty,
    is_spec_group_header_line,
    is_valid_bom_data_row,
)

_DESIG_RX = re.compile(
    r"\b([A-ZА-ЯЁ]{1,4}[\d]{1,3}(?:[.,]\s*[A-ZА-ЯЁ\d]{1,4})*"
    r"|[\d]{1,2}ТТ|ТТ[\d]|QF\d|SF\d+|UG\d|TL\d|UCT[\d.]|РПВ\d|SG\d|TR\d|WA\d)\b",
    re.I,
)
_QTY_END_RX = re.compile(r"\s+(\d{1,3})\s*$")


def _row_from_match(desig: str, name: str, qty: str = "—") -> dict[str, str]:
    return fix_spec_row_qty(
        {
            "Поз.": "—",
            "Обозначение": desig.strip(),
            "Наименование": re.sub(r"\s+", " ", name).strip(),
            "Кол.": qty if re.fullmatch(r"\d{1,3}", (qty or "").strip()) else "—",
            "Масса ед., кг": "—",
            "Примечание": "—",
        }
    )


def _ocr_desig_fix(ln: str) -> list[tuple[str, str]]:
    """Типовые OCR-искажения обозначений (T.1 → контекст трансформатора)."""
    out: list[tuple[str, str]] = []
    if re.search(r"^[TТ]\.?\s*1\b", ln) and re.search(r"рансформ", ln, re.I):
        name = re.sub(r"^[TТ]\.?\s*1\s*", "", ln).strip()
        out.append(("TL1", name))
    return out


def extract_spec_rows_from_messy_ocr(text: str, *, max_rows: int = 80) -> list[dict[str, str]]:
    """Второй проход: вытащить строки перечня по обозначениям и типовым наименованиям."""
    if not (text or "").strip():
        return []
    rows: list[dict[str, str]] = []
    for raw_ln in text.splitlines():
        ln = re.sub(r"\s+", " ", raw_ln).strip()
        if len(ln) < 10:
            continue
        if is_spec_group_header_line(ln):
            rows.append(
                {
                    "Поз.": "—",
                    "Обозначение": "—",
                    "Наименование": ln,
                    "Кол.": "—",
                    "Масса ед., кг": "—",
                    "Примечание": "—",
                    "_group": "1",
                }
            )
            continue
        for desig, name in _ocr_desig_fix(ln):
            row = _row_from_match(desig, name)
            if is_valid_bom_data_row(row):
                rows.append(row)
        qty = "—"
        qm = _QTY_END_RX.search(ln)
        body = ln
        if qm:
            qty = qm.group(1)
            body = ln[: qm.start()].strip()
        for m in _DESIG_RX.finditer(body):
            desig = m.group(1)
            name = body[m.end() :].strip(" ,;|-")
            if len(name) < 6:
                continue
            if not re.search(r"[А-Яа-яЁё]{4,}", name):
                continue
            row = _row_from_match(desig, name, qty)
            if is_valid_bom_data_row(row):
                rows.append(row)
            if len(rows) >= max_rows:
                break
        if len(rows) >= max_rows:
            break
    return dedupe_spec_rows(rows)
