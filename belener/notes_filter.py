"""Отделение технических требований от OCR/vision-мусора поля схемы."""

from __future__ import annotations

import re
from typing import Any

from belener.report_clean import _looks_like_ocr_noise, is_garbage_body_text

_TT_TITLE = re.compile(
    r"техническ|требован|примечан|общие\s+указан",
    re.I,
)
_BODY_TITLE = re.compile(
    r"текст\s+на\s+чертеж|вне\s+таблиц|обозначен.*лист|подпис.*схем",
    re.I,
)
_TT_LINE = re.compile(r"^\d{1,2}\s*[\.\)]\s+\S", re.M)


def section_looks_like_tt(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 25:
        return False
    if _looks_like_ocr_noise(s):
        return False
    if re.search(r"\b[хx]\d+:\d+\b", s, re.I):
        return False
    if s.count("|") >= 3:
        return False
    if _TT_LINE.search(s):
        return True
    if "должн" in s.casefold() or "следует" in s.casefold():
        return True
    return len(s) >= 60 and _TT_TITLE.search(s)


def is_technical_requirements_notes(notes: dict[str, Any] | None) -> bool:
    if not notes:
        return False
    title = str(notes.get("title") or "").strip()
    if title and _BODY_TITLE.search(title):
        return False
    sections = list(notes.get("sections") or [])
    if sections:
        good = sum(1 for s in sections if section_looks_like_tt(str(s.get("text") or "")))
        return good >= max(1, int(len(sections) * 0.5))
    full = str(notes.get("full_text") or "").strip()
    if not full or is_garbage_body_text(full):
        return False
    if _BODY_TITLE.search(title):
        return False
    return section_looks_like_tt(full) or bool(_TT_TITLE.search(title))


def filter_notes_to_tt(notes: dict[str, Any] | None) -> dict[str, Any] | None:
    if not notes:
        return None
    if not is_technical_requirements_notes(notes):
        return None
    title = str(notes.get("title") or "").strip()
    if not _TT_TITLE.search(title):
        title = "Технические требования"
    sections = [
        s
        for s in (notes.get("sections") or [])
        if isinstance(s, dict) and section_looks_like_tt(str(s.get("text") or ""))
    ]
    if sections:
        return {"title": title, "sections": sections, "full_text": "", "source": notes.get("source")}
    full = str(notes.get("full_text") or "").strip()
    if section_looks_like_tt(full):
        from belener.sheet_text import _split_tt_items

        sections = _split_tt_items(full)
        if sections:
            return {"title": title, "sections": sections, "full_text": "", "source": notes.get("source")}
    return None
