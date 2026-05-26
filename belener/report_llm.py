"""Финальная фильтрация и оформление отчёта через gemma (без vision)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import ollama

from belener.config import (
    model_drawing,
    ollama_host,
    report_llm_enabled,
    report_llm_mode,
    report_llm_model,
    report_llm_num_predict,
    report_llm_timeout_sec,
)

log = logging.getLogger("belener.report_llm")

_SYSTEM_JSON = """Ты редактор отчёта по инженерному чертежу (САПР). На входе — JSON после OCR.
Сформируй Markdown в порядке: таблицы и ведомости → условные обозначения → технические требования → основная надпись (рамка).
Не выдумывай строки. Исправь только явные опечатки OCR. Пустые разделы пропускай.
Штамп: сохрани подписи полей и таблицы как в JSON (не переименовывай колонки).
Не включай сырой OCR-текст поля электросхемы (Х1:10, обрывки проводов)."""

_SYSTEM_POLISH = """Ты редактор отчёта по инженерному чертежу после OCR.
На входе — черновик Markdown. Твоя задача:
- убрать явный OCR-мусор и обрывки;
- удалить таблицы изменений штампа (Изм., Кол., Лист, Подп., Дата) и сырой текст электросхемы;
- исправить опечатки в словах;
- сохранить все реальные строки таблиц аппаратуры/экспликации/легенды и пункты ТТ;
- не добавлять данных, которых нет в черновике;
- оставить порядок разделов: таблицы → условные обозначения → ТТ → штамп.

Верни только готовый Markdown, без пояснений."""


def _compact_payload(facts: dict[str, Any]) -> str:
    stamp = facts.get("stamp") or {}
    tables = facts.get("tables") or []
    from belener.notes_filter import filter_notes_to_tt

    notes = filter_notes_to_tt(facts.get("sheet_notes")) or {}
    if stamp.get("source") == "stamp_universal":
        stamp_out: Any = stamp.get("raw_frame") or {
            "field_rows": stamp.get("kv"),
            "revision_table": {"rows": stamp.get("revisions")},
            "signature_table": stamp.get("signatures"),
            "section_titles": stamp.get("titles"),
        }
    else:
        stamp_out = {
            "kv": stamp.get("kv"),
            "signatures": stamp.get("signatures"),
            "titles": stamp.get("titles"),
            "revisions": stamp.get("revisions"),
        }
    payload = {
        "filename": facts.get("filename"),
        "stamp": stamp_out,
        "technical_requirements": notes.get("sections") or [],
        "tables": [
            {
                "title": t.get("title"),
                "kind": t.get("kind"),
                "table_number": t.get("table_number"),
                "rows": (t.get("rows") or [])[:40],
            }
            for t in tables
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=0)


def _payload_has_content(facts: dict[str, Any]) -> bool:
    stamp = facts.get("stamp") or {}
    if stamp.get("kv") or stamp.get("signatures") or stamp.get("revisions") or stamp.get("raw_frame"):
        return True
    if any((t.get("rows") or []) for t in facts.get("tables") or []):
        return True
    notes = facts.get("sheet_notes") or {}
    return bool(notes.get("sections") or notes.get("full_text"))


def format_report_llm(
    facts: dict[str, Any],
    *,
    model: str | None = None,
    base_markdown: str | None = None,
) -> str | None:
    """Gemma: только финальный отчёт. При ошибке/таймауте — None (fallback на facts_to_markdown)."""
    if not report_llm_enabled():
        return None
    if not _payload_has_content(facts):
        return None
    model = (model or report_llm_model() or model_drawing() or "gemma3:4b").strip()
    if not model:
        return None

    mode = report_llm_mode()
    if mode == "polish" and (base_markdown or "").strip():
        system = _SYSTEM_POLISH
        user = "Черновик отчёта:\n\n" + base_markdown.strip()[:14000]
    else:
        system = _SYSTEM_JSON
        user = "Данные OCR:\n" + _compact_payload(facts)[:12000]

    try:
        client = ollama.Client(host=ollama_host(), timeout=report_llm_timeout_sec())
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={"temperature": 0.05, "num_predict": report_llm_num_predict()},
        )
        text = (resp.get("message") or {}).get("content") or ""
        text = text.strip()
        if len(text) < 80:
            return None
        return re.sub(r"\n{3,}", "\n\n", text) + "\n"
    except Exception:
        log.exception("report LLM failed model=%s mode=%s", model, mode)
        return None
