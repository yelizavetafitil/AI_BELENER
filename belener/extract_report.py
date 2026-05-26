"""Markdown-отчёт: быстрый OCR → фильтр Python → финальная полировка gemma."""

from __future__ import annotations

from typing import Any

from belener.config import report_faithful, report_llm_enabled
from belener.report import facts_to_markdown, full_text_pages_to_markdown
from belener.report_clean import clean_drawing_facts
from belener.report_llm import format_report_llm


def _drawing_has_reportable_content(drawing: dict[str, Any]) -> bool:
    """Есть ли в фактах данные для отчёта (не вызывать gemma на пустом JSON)."""
    stamp = drawing.get("stamp") or {}
    if stamp.get("kv") or stamp.get("signatures") or stamp.get("revisions"):
        return True
    if stamp.get("titles") or stamp.get("other_lines") or stamp.get("raw_frame"):
        return True
    for t in drawing.get("tables") or []:
        if t.get("rows"):
            return True
    notes = drawing.get("sheet_notes") or {}
    if notes.get("sections") or notes.get("full_text"):
        return True
    return False


def extraction_to_markdown(facts: dict[str, Any]) -> str:
    if not facts.get("ok"):
        return f"**Ошибка:** {facts.get('error', 'Не удалось извлечь текст')}\n"

    drawing = facts.get("drawing")
    if drawing and drawing.get("ok"):
        drawing = clean_drawing_facts(drawing)
        base_md = facts_to_markdown(drawing)
        stamp_src = (drawing.get("stamp") or {}).get("source")

        if (
            report_llm_enabled()
            and not report_faithful()
            and stamp_src != "stamp_universal"
            and _drawing_has_reportable_content(drawing)
        ):
            polished = format_report_llm(drawing, base_markdown=base_md)
            if polished:
                full_text = full_text_pages_to_markdown(drawing.get("full_text_pages") or [])
                if full_text and "Полный текст листа" not in polished:
                    return polished.rstrip() + "\n\n" + full_text
                return polished

        if not _drawing_has_reportable_content(drawing):
            warns = [str(w) for w in (drawing.get("warnings") or []) if str(w).strip()]
            if warns:
                base_md = (
                    base_md.rstrip()
                    + "\n\n**Извлечение неполное — сверьте с PDF:**\n"
                    + "\n".join(f"- {w}" for w in warns)
                    + "\n"
                )

        return base_md

    lines = ["# Извлечённый текст", ""]
    lines.append(f"**Файл:** {facts.get('filename', 'document.pdf')}")
    lines.append(f"**Страниц:** {facts.get('page_count', 0)}")
    lines.append("")

    for page in facts.get("pages") or []:
        idx = page.get("index", "?")
        text = str(page.get("text") or "").strip()
        lines.append(f"## Страница {idx}")
        lines.append("")
        if text:
            lines.append("```text")
            lines.append(text)
            lines.append("```")
        else:
            lines.append("*(текст не распознан)*")
        lines.append("")

    for w in facts.get("warnings") or []:
        lines.append(f"- {w}")

    return "\n".join(lines)
