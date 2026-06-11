"""Спецификация материалов ГОСТ (полоса 4x40, ТУ, тяга) при разорванном OCR."""

from __future__ import annotations

import re

from belener.anchors import LEGEND_START_RX, SPECIFICATION_START_RX
from belener.spec_table import dedupe_spec_rows, fix_spec_row_qty

_GOST_BAR_RX = re.compile(r"(\d+[xх×]\d+\s*ГОСТ\s*[\d\-\.]+)", re.I)
_TU_RX = re.compile(r"(ТУ\s*[\d\-]+(?:-\d+)?)", re.I)
_PIPE_RX = re.compile(r"((?:тяга|труба)\s+водогазопровод\w*)", re.I)
_POS_PREFIX_RX = re.compile(r"^(\d{1,3})\s+")
_NAME_HINT_RX = re.compile(r"(полос\w*|ст\s*3|сталь|держатель\s+шин|водогаз)", re.I)
_QTY_MASS_RX = re.compile(
    r"(\d{1,3})\s*[\]|\|]\s*([\d]+[,.][\d]+)|"
    r"(\d{1,3})\s+([\d]+[,.][\d]+)\s*$",
    re.I,
)
_NOTE_START_RX = re.compile(
    r"^\d{1,2}\s+(?:в соответствии|с целью|магистраль|защитное|проход|все соедин|отпа)",
    re.I,
)
_CAPTION_RX = re.compile(r"^к\s+контуру\b", re.I)


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


def _scan_qty_mass(chunk: str) -> tuple[str, str]:
    m = _QTY_MASS_RX.search(chunk)
    if not m:
        return "—", "—"
    g = [x for x in m.groups() if x]
    if len(g) >= 2:
        return g[0], g[1].replace(",", ".")
    return "—", "—"


def _name_from_chunk(chunk: str, desig: str) -> str:
    s = chunk
    for rx in (_GOST_BAR_RX, _TU_RX):
        s = rx.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -|")
    if _CAPTION_RX.search(s) or len(s) < 8:
        if _NAME_HINT_RX.search(chunk):
            m = _NAME_HINT_RX.search(chunk)
            if m:
                return re.sub(r"\s+", " ", chunk[m.start() :].strip())
        return ""
    if _NAME_HINT_RX.search(s):
        return s
    if re.search(r"[А-Яа-яЁё]{10,}", s) and not re.search(r"контур\w*\s+внутрен", s, re.I):
        return s
    return ""


def _spec_lines(text: str) -> list[str]:
    lines: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if _NOTE_START_RX.search(s):
            break
        if re.search(LEGEND_START_RX, s, re.I) and lines:
            break
        if re.match(r"^таблица\s*\d", s, re.I) and lines:
            continue
        lines.append(s)
    return lines


def parse_gost_material_spec_rows(text: str, *, max_rows: int = 40) -> list[dict[str, str]]:
    """Позиции перечня материалов, когда шапка таблицы разбита OCR (4x40 ГОСТ + полоса)."""
    if not (text or "").strip():
        return []
    if not re.search(SPECIFICATION_START_RX, text, re.I) and not re.search(
        r"гост\s*[\d\-]|ту\s*[\d\-]", text, re.I
    ):
        return []

    lines = _spec_lines(text)
    hits: list[tuple[int, str, str, str]] = []
    for i, ln in enumerate(lines):
        pos_prefix = ""
        pm = _POS_PREFIX_RX.match(ln)
        if pm and int(pm.group(1)) <= 15:
            pos_prefix = pm.group(1)
        for m in _GOST_BAR_RX.finditer(ln):
            hits.append((i, pos_prefix, "gost", m.group(1)))
        if _TU_RX.search(ln):
            tu = _TU_RX.search(ln)
            if tu:
                p = pos_prefix
                if pm and int(pm.group(1)) > 15:
                    p = ""
                hits.append((i, p, "tu", tu.group(1)))
        if _PIPE_RX.search(ln) and not _GOST_BAR_RX.search(ln):
            hits.append((i, pos_prefix, "pipe", _PIPE_RX.search(ln).group(1)))  # type: ignore[union-attr]

    if not hits:
        return []

    rows: list[dict[str, str]] = []
    auto_pos = 0
    for hi, (i, pos_hint, kind, token) in enumerate(hits):
        end_i = hits[hi + 1][0] if hi + 1 < len(hits) else min(i + 4, len(lines))
        chunk = " ".join(lines[i:end_i])
        qty, mass = _scan_qty_mass(chunk)
        if kind == "gost":
            desig = token
            name = _name_from_chunk(chunk, desig)
            if not name and i + 1 < len(lines):
                name = _name_from_chunk(lines[i + 1], desig)
        elif kind == "tu":
            desig = token
            name = chunk.split(token, 1)[-1].strip(" -|") if token in chunk else ""
            name = re.sub(r"^\d{1,3}\s+", "", name).strip()
        else:
            desig = "—"
            name = token

        pos = pos_hint
        if not pos or (pos.isdigit() and int(pos) > 15):
            auto_pos += 1
            pos = str(auto_pos)
        if kind == "gost" and not name:
            name = "Полоса стальная" if "40" in desig or "25" in desig else "—"
        row = _row(pos, desig, name, qty, mass)
        if row.get("Наименование", "—") != "—" or kind in ("gost", "tu"):
            rows.append(row)
        if len(rows) >= max_rows:
            break

    return dedupe_spec_rows(rows)
