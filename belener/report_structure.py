"""Структурированный отчёт из tile OCR — эвристики, без мусорного parse.py."""

from __future__ import annotations

from typing import Any

from belener.config import report_markdown_tables
from belener.report_heuristics import extract_spec_rows, extract_stamp_fields, extract_tt_lines
from belener.report_llm import dedupe_ocr_text


def _esc(val: str) -> str:
    import re

    return re.sub(r"\s+", " ", str(val or "—").strip() or "—").replace("|", "\\|")


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not headers or not rows:
        return []
    h = " | ".join(_esc(c) for c in headers)
    sep = " | ".join("---" for _ in headers)
    body = [" | ".join(_esc(c) for c in row) for row in rows]
    return [h, sep, *body, ""]


def _ocr_blob(drawing: dict[str, Any]) -> str:
    pages = drawing.get("full_text_pages") or []
    raw = "\n\n".join(str(p.get("text") or "").strip() for p in pages if str(p.get("text") or "").strip())
    return dedupe_ocr_text(raw)


def _render_stamp(fields: list) -> list[str]:
    if not fields:
        return []
    lines = ["## Основная надпись", ""]
    sig_data = None
    for label, val in fields:
        if label == "__signatures__":
            sig_data = val
            continue
        lines.append(f"**{label}:** {val}")
    if sig_data and report_markdown_tables():
        rows = [[r, n, d] for r, n, d in sig_data if str(n).strip()]
        if rows:
            lines.append("")
            lines.extend(_md_table(["Роль", "Фамилия", "Дата"], rows))
    lines.append("")
    return lines


def _render_spec(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return []
    cols = ("Поз.", "Обозначение", "Наименование", "Кол.", "Масса ед., кг", "Примечание")
    body = [[str(r.get(c) or "—") for c in cols] for r in rows]
    lines = ["## Спецификация", ""]
    lines.extend(_md_table(list(cols), body))
    return lines


def _render_notes(notes: list[str]) -> list[str]:
    if not notes:
        return []
    lines = ["## Технические требования", ""]
    for note in notes:
        lines.append(note.strip())
        lines.append("")
    return lines


def _render_normatives(refs: list[dict[str, str]]) -> list[str]:
    if not refs:
        return []
    lines = ["## Нормативные документы", ""]
    rows = [[str(n.get("kind") or "—"), str(n.get("ref") or "—")] for n in refs]
    lines.extend(_md_table(["Тип", "Обозначение"], rows))
    lines.append(f"*Найдено: {len(refs)}*")
    lines.append("")
    return lines


def structured_report_from_drawing(
    drawing: dict[str, Any],
    *,
    mode: str = "full",
    filename: str = "",
) -> str:
    text = _ocr_blob(drawing)
    if len(text.strip()) < 40:
        return "*Текст не распознан — проверьте качество скана.*\n"

    refs = list(drawing.get("normative_refs") or [])
    include_normatives = mode in ("full", "analysis")

    parts: list[str] = []
    stamp = extract_stamp_fields(text)
    spec = extract_spec_rows(text)
    tt = extract_tt_lines(text)

    if stamp:
        parts.extend(_render_stamp(stamp))
    if spec:
        parts.extend(_render_spec(spec))
    if tt:
        parts.extend(_render_notes(tt))
    if include_normatives and refs:
        parts.extend(_render_normatives(refs))

    if not parts:
        return "*Не удалось разобрать лист. Запустите Ollama и модель для форматирования.*\n"

    if not stamp and not spec and not tt and refs:
        parts.insert(0, "*Структура листа не распознана — показаны только нормативы.*\n")

    return "\n".join(parts).strip() + "\n"
