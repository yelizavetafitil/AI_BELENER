"""Markdown-отчёт из tile OCR — только структурированный вывод."""

from __future__ import annotations

from typing import Any

from belener.config import report_llm_enabled, report_markdown_tables, report_normative_compact


def _report_mode_for_question(question: str) -> str:
    s = (question or "").strip().casefold().replace("ё", "е")
    if any(k in s for k in ("разбор", "анализ")):
        return "analysis"
    if any(
        k in s
        for k in (
            "извлеки текст",
            "извлечь текст",
            "весь текст",
            "прочитай лист",
            "прочитай скан",
            "текст с листа",
        )
    ):
        return "text"
    return "full"


def _report_intro(mode: str, *, polished: bool = False) -> str:
    if polished:
        if mode == "text":
            return "**Извлечённый текст листа**\n"
        if mode == "analysis":
            return "**Разбор документа**\n"
        return "**Содержимое листа**\n"
    if mode == "text":
        return "**Извлечённый текст листа**\n"
    if mode == "analysis":
        return "**Разбор документа**\n"
    return "**Содержимое листа**\n"


def _esc_md_cell(val: str) -> str:
    import re

    return re.sub(r"\s+", " ", str(val or "—").strip() or "—").replace("|", "\\|")


def _normative_table_md(refs: list[dict[str, str]]) -> list[str]:
    if not refs:
        return []
    lines = ["## Нормативные документы", ""]
    compact = report_normative_compact()
    if report_markdown_tables():
        if compact:
            lines.append("| Тип | Обозначение |")
            lines.append("| --- | --- |")
            for n in refs:
                lines.append(f"| {_esc_md_cell(n.get('kind'))} | {_esc_md_cell(n.get('ref'))} |")
        else:
            lines.append("| Тип | Обозначение | Контекст |")
            lines.append("| --- | --- | --- |")
            for n in refs:
                lines.append(
                    f"| {_esc_md_cell(n.get('kind'))} | {_esc_md_cell(n.get('ref'))} | "
                    f"{_esc_md_cell(n.get('context') or '—')} |"
                )
    else:
        for n in refs:
            lines.append(f"- **{n.get('kind') or '—'}** {n.get('ref') or '—'}")
    lines.append("")
    lines.append(f"*Найдено: {len(refs)}*")
    lines.append("")
    return lines


def _structured_fallback(drawing: dict[str, Any], *, mode: str, filename: str) -> str:
    from belener.report_structure import structured_report_from_drawing

    body = structured_report_from_drawing(drawing, mode=mode, filename=filename)
    note = ""
    if report_llm_enabled():
        note = (
            "\n\n*Форматирование ИИ недоступно — показан автоматический разбор. "
            "Проверьте Ollama и модель (`PDF_REPORT_LLM_MODEL`, напр. gemma3:4b).*"
        )
    return body.rstrip() + note + "\n"


def _tile_drawing_markdown(
    drawing: dict[str, Any],
    *,
    mode: str = "full",
    filename: str = "",
    polish_llm: bool = False,
) -> str:
    use_llm = polish_llm and report_llm_enabled()

    if use_llm:
        from belener.report_llm import format_ocr_report_llm

        polished = format_ocr_report_llm(drawing, mode=mode, filename=filename)
        if polished:
            intro = _report_intro(mode, polished=True).rstrip()
            return intro + "\n\n" + polished.strip() + "\n"
        return _report_intro(mode).rstrip() + "\n\n" + _structured_fallback(drawing, mode=mode, filename=filename)

    return _report_intro(mode).rstrip() + "\n\n" + _structured_fallback(drawing, mode=mode, filename=filename)


def extraction_to_markdown(
    facts: dict[str, Any],
    *,
    question: str = "",
    polish_llm: bool = True,
) -> str:
    if not facts.get("ok"):
        return f"**Ошибка:** {facts.get('error', 'Не удалось извлечь текст')}\n"

    drawing = facts.get("drawing")
    if drawing and drawing.get("ok"):
        mode = _report_mode_for_question(question)
        return _tile_drawing_markdown(
            drawing,
            mode=mode,
            filename=str(facts.get("filename") or ""),
            polish_llm=polish_llm,
        )

    return "*Не удалось извлечь текст.*\n"
