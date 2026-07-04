"""Извлечение нормативов (ГОСТ/ОСТ/ТУ/…) из PDF и изображений — OCR по тайлам листа."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import fitz

from belener.normative_crops import extract_normatives_document_crops

log = logging.getLogger("belener.normative_extract")


def extract_normatives_from_document(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    source_path: str | None = None,
    allow_drawing_fallback: bool | None = None,
) -> dict[str, Any]:
    """Нормативы: сетка тайлов по листу → OCR (основной путь)."""
    return extract_normatives_document_crops(doc, filename)


def extract_normatives_from_image_path(path: str, filename: str | None = None) -> dict[str, Any]:
    """Изображение как одностраничный PDF → те же тайлы и OCR."""
    p = Path(path)
    doc = fitz.open(str(p))
    try:
        return extract_normatives_document_crops(doc, filename or p.name)
    finally:
        doc.close()


def extract_normatives_pdf_path(path: str, filename: str | None = None) -> dict[str, Any]:
    p = Path(path)
    path_str = str(p.resolve())
    doc = fitz.open(path_str)
    try:
        return extract_normatives_from_document(doc, filename or p.name, source_path=path_str)
    finally:
        doc.close()


def normative_refs_to_markdown(
    refs: list[dict[str, str]],
    *,
    filename: str = "",
    pipeline: str = "",
    include_context: bool = False,
    stn_checks: list | None = None,
    check_date: date | None = None,
    stn_error: str = "",
    page_count: int = 0,
    pages_processed: int = 0,
    budget_exhausted: bool = False,
) -> str:
    lines = ["## Нормативные документы (ГОСТ, ОСТ, СТП, ТУ и др.)", ""]
    if filename:
        lines.append(f"**Файл:** {filename}")
    if page_count > 1:
        if filename:
            lines.append("")
        proc = pages_processed or page_count
        note = f"**Листов в файле:** {page_count}"
        if proc < page_count:
            note += f" · **обработано:** {proc}"
        if budget_exhausted:
            note += " · *не все листы успели прочитаться*"
        lines.append(note)
    elif budget_exhausted:
        if filename:
            lines.append("")
        lines.append("*Не все участки листа успели прочитаться — список может быть неполным.*")
    if check_date:
        if filename or page_count > 1 or budget_exhausted:
            lines.append("")
        lines.append(f"**Дата проверки актуальности:** {check_date.strftime('%d.%m.%Y')}")
    lines.append("")

    if not refs:
        lines.append(
            "*Нормативные ссылки не найдены. Проверьте качество скана или "
            "используйте полный разбор чертежа.*"
        )
        lines.append("")
    elif include_context:
        lines.extend(["| Тип | Обозначение | Контекст на листе |", "| --- | --- | --- |"])
        for n in refs:
            lines.append(
                f"| {n.get('kind') or '—'} | {n.get('ref') or '—'} | {n.get('context') or '—'} |"
            )
        lines.append("")
        lines.append(f"*Найдено: {len(refs)}*")
        lines.append("")
    else:
        lines.extend(["| Тип | Обозначение |", "| --- | --- |"])
        for n in refs:
            lines.append(f"| {n.get('kind') or '—'} | {n.get('ref') or '—'} |")
        lines.append("")
        lines.append(f"*Найдено: {len(refs)}*")
        lines.append("")

    if stn_checks:
        from belener.stn_lookup import stn_checks_to_markdown

        lines.extend(stn_checks_to_markdown(stn_checks, check_date=check_date))

    stn_error = (stn_error or "").strip()
    if stn_error:
        lines.extend(["", f"*⚠ {stn_error}*", ""])

    return "\n".join(lines)


def normative_result_to_markdown(
    result: dict[str, Any],
    *,
    include_context: bool = False,
    stn_checks: list | None = None,
    check_date: date | None = None,
) -> str:
    checks = stn_checks
    if checks is None:
        checks = result.get("stn_checks")
    return normative_refs_to_markdown(
        list(result.get("normative_refs") or []),
        filename=str(result.get("filename") or ""),
        pipeline=str(result.get("pipeline") or ""),
        include_context=include_context,
        stn_checks=checks,
        check_date=check_date,
        stn_error=str(result.get("stn_error") or ""),
        page_count=int(result.get("page_count") or 0),
        pages_processed=int(result.get("pages_processed") or 0),
        budget_exhausted=bool(result.get("budget_exhausted")),
    )
