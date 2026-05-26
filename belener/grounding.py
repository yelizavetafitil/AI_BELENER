"""Проверка строк таблиц по OCR-тексту листа — без «додумывания» vision/LLM."""

from __future__ import annotations

import re
from typing import Any


def _norm_blob(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold())


def _tokens(text: str, min_len: int = 4) -> set[str]:
    return {w for w in re.findall(r"[\wА-Яа-яЁё]{2,}", (text or "").casefold()) if len(w) >= min_len}


def row_grounded_in_ocr(row: dict, blob: str, *, min_hits: int = 1) -> bool:
    """Строка подтверждается OCR: обозначение или фрагмент наименования есть в тексте листа."""
    if not blob.strip():
        return False
    parts: list[str] = []
    if isinstance(row, dict):
        for key in ("Поз.", "Обозначение", "Наименование", "name", "note", "plan_number"):
            v = str(row.get(key) or "").strip()
            if v and v not in ("—", "-"):
                parts.append(v)
        parts.extend(str(v) for v in row.values() if str(v).strip() not in ("—", "-", ""))
    text = " ".join(parts)
    if len(text) < 4:
        return False
    blob_n = _norm_blob(blob)
    if _norm_blob(text) in blob_n:
        return True
    desig = str(row.get("Обозначение") or "").strip()
    if len(desig) >= 2 and desig.casefold() in blob_n:
        return True
    name = str(row.get("Наименование") or row.get("name") or row.get("note") or "").strip()
    hits = 0
    for tok in _tokens(name, min_len=4):
        if tok in blob_n:
            hits += 1
    if hits >= min_hits:
        return True
    if len(name) >= 10:
        sub = _norm_blob(name[: min(24, len(name))])
        if sub and sub in blob_n:
            return True
    return False


def _looks_like_template_hallucination(rows: list[dict]) -> bool:
    """Типичный «шаблон» vision: QF1, TL1, SF1… с позициями 1..N без OCR-подтверждения."""
    if len(rows) < 4:
        return False
    desig_hits = 0
    pos_nums: list[int] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = str(r.get("Обозначение") or "").strip()
        if re.fullmatch(r"[A-Z]{1,4}\d{1,3}[*]?", d):
            desig_hits += 1
        p = str(r.get("Поз.") or "").strip()
        if p.isdigit():
            pos_nums.append(int(p))
    if desig_hits < max(3, int(len(rows) * 0.55)):
        return False
    if pos_nums and pos_nums == list(range(1, len(pos_nums) + 1)):
        names = " ".join(str(r.get("Наименование") or "") for r in rows if isinstance(r, dict))
        if re.search(
            r"распределительн\w*\s+выключ|трансформатор\s+напряжен|реле\s+напряжен|"
            r"амперметр|контактор|вентил",
            names,
            re.I,
        ):
            return True
    return False


def filter_table_rows_by_ocr(rows: list[dict], blob: str) -> list[dict]:
    if not blob.strip() or not rows:
        return list(rows or [])
    kept = [r for r in rows if isinstance(r, dict) and row_grounded_in_ocr(r, blob)]
    if kept:
        return kept
    if _looks_like_template_hallucination(rows):
        return []
    return kept


def filter_tables_by_ocr_grounding(
    tables: list[dict[str, Any]],
    ocr_blob: str,
    *,
    min_grounded_ratio: float = 0.34,
) -> list[dict[str, Any]]:
    """Убрать таблицы/строки, которых нет в OCR листа."""
    if not (ocr_blob or "").strip():
        return tables
    out: list[dict[str, Any]] = []
    for tbl in tables or []:
        if not isinstance(tbl, dict):
            continue
        src = str(tbl.get("source") or "")
        rows = list(tbl.get("rows") or [])
        if not rows:
            continue
        if src == "vision" and str(tbl.get("kind") or "") in ("specification", "table"):
            filtered = filter_table_rows_by_ocr(rows, ocr_blob)
            if not filtered:
                continue
            rows = filtered
        elif str(tbl.get("kind") or "") in ("specification", "explication", "legend", "table"):
            filtered = filter_table_rows_by_ocr(rows, ocr_blob)
            if _looks_like_template_hallucination(rows) and not filtered:
                continue
            if filtered:
                ratio = len(filtered) / max(len(rows), 1)
                if ratio < min_grounded_ratio and len(rows) >= 4:
                    continue
                rows = filtered
        if not rows:
            continue
        out.append({**tbl, "rows": rows})
    return out
