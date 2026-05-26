"""Текст чертежа вне таблиц и штампа: OCR + vision на постобработке."""

from __future__ import annotations

import re
from typing import Any

import fitz

from belener.config import body_dpi, body_min_chars, body_ocr_enabled
from belener.ocr import ocr_clip_tiled, ocr_region
from belener.zones import SheetZones


def _is_garbage_body_text(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 40:
        return True
    letters = re.findall(r"[А-Яа-яЁёA-Za-z]", s)
    if not letters:
        return True
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    if cyr / len(letters) < 0.45:
        return True
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if len(lines) > 8 and sum(1 for ln in lines if len(ln) <= 12) / len(lines) > 0.55:
        return True
    return False


def non_table_rects(page_rect: fitz.Rect, zones: SheetZones) -> list[tuple[str, fitz.Rect]]:
    """Зоны вне таблиц/штампа: колонка ТТ и (опционально) поле чертежа."""
    out: list[tuple[str, fitz.Rect]] = []
    notes = zones.rects.get("sheet_notes")
    if notes is not None and notes.width >= page_rect.width * 0.12:
        out.append(("sheet_notes", notes))
    body = zones.rects.get("body")
    if body_ocr_enabled() and body is not None and body.width >= page_rect.width * 0.2:
        out.append(("body", body))
    if out:
        return out
    r = page_rect
    sf = 0.30
    rw = 0.44
    y0 = r.y0 + r.height * 0.04
    y1 = r.y1 - r.height * sf
    x1 = r.x1 - r.width * rw
    if x1 - r.x0 >= r.width * 0.25 and y1 - y0 >= r.height * 0.2:
        out.append(("drawing_area", fitz.Rect(r.x0, y0, x1, y1)))
    return out


def _clean_text_layer(text: str) -> str:
    lines: list[str] = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def text_layer_non_table_text(
    page: fitz.Page,
    zones: SheetZones,
) -> tuple[str, dict[str, str]]:
    """Текстовый слой в зонах вне таблиц/штампа для PDF-экспортов NanoCAD."""
    parts: list[str] = []
    by_zone: dict[str, str] = {}
    for name, rect in non_table_rects(page.rect, zones):
        text = _clean_text_layer(page.get_text("text", clip=rect) or "")
        if text:
            by_zone[name] = text
            parts.append(text)
    return "\n\n".join(parts).strip(), by_zone


def extract_text_layer_pages(
    doc: fitz.Document,
    *,
    max_chars_per_page: int = 30000,
) -> list[dict[str, Any]]:
    """Полный текстовый слой по страницам в порядке блоков PDF."""
    pages: list[dict[str, Any]] = []
    for page_index in range(doc.page_count):
        page = doc[page_index]
        blocks = []
        for block in page.get_text("blocks") or []:
            if len(block) < 5:
                continue
            x0, y0, x1, y1, text = block[:5]
            cleaned = _clean_text_layer(str(text or ""))
            if not cleaned:
                continue
            blocks.append((round(float(y0), 1), round(float(x0), 1), round(float(y1), 1), round(float(x1), 1), cleaned))
        blocks.sort(key=lambda b: (b[0], b[1], b[2], b[3]))
        text = "\n\n".join(b[4] for b in blocks).strip()
        if len(text) > max_chars_per_page:
            text = text[:max_chars_per_page].rstrip() + "\n…"
        pages.append(
            {
                "index": page_index + 1,
                "source": "text_layer" if text else "",
                "text": text,
                "char_count": len(text),
            }
        )
    return pages


def ocr_non_table_text(
    doc: fitz.Document,
    page_rect: fitz.Rect,
    zones: SheetZones,
    *,
    page_index: int = 0,
) -> tuple[str, dict[str, str]]:
    """OCR всех зон вне таблиц. Возвращает (общий текст, текст по зонам)."""
    dpi = body_dpi()
    parts: list[str] = []
    by_zone: dict[str, str] = {}
    for name, rect in non_table_rects(page_rect, zones):
        area = rect.width * rect.height
        page_area = page_rect.width * page_rect.height
        if area > page_area * 0.55:
            text = ocr_clip_tiled(
                doc,
                page_index,
                rect,
                dpi=dpi,
                tile_px=2000,
                overlap=180,
                zone=name,
                psm=6,
            )
        else:
            text = ocr_region(doc, page_index, rect, dpi=dpi, zone=name, psm=6)
        text = (text or "").strip()
        if text:
            by_zone[name] = text
            parts.append(text)
    combined = "\n\n".join(parts).strip()
    return combined, by_zone


def sheet_text_quality(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 80:
        return False
    return not _is_garbage_body_text(s)


def needs_sheet_text_vision(text: str) -> bool:
    return len((text or "").strip()) < body_min_chars() or not sheet_text_quality(text)


def _split_tt_items(text: str) -> list[dict[str, str]]:
    s = re.sub(r"\s+", " ", text.strip())
    if not s:
        return []
    parts = re.split(r"(?<=\.)\s+(?=\d{1,2}\s)", s)
    if len(parts) <= 1:
        parts = re.split(r"\s+(?=\d{1,2}\s+[А-ЯA-Z])", s)
    out: list[dict[str, str]] = []
    for chunk in parts:
        chunk = chunk.strip()
        if len(chunk) < 15:
            continue
        m = re.match(r"^(\d{1,2})\s*[\.\)]?\s*(.+)", chunk)
        if m:
            out.append({"number": m.group(1), "text": m.group(2).strip()})
        else:
            out.append({"number": "", "text": chunk})
    return out


def build_sheet_notes_payload(
    ocr_text: str,
    vision_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Только технические требования (без текста электросхемы)."""
    from belener.config import report_include_body_text
    from belener.notes_filter import filter_notes_to_tt

    if vision_data:
        filtered = filter_notes_to_tt(
            {
                "title": vision_data.get("title"),
                "sections": vision_data.get("sections"),
                "full_text": vision_data.get("full_text"),
                "source": "vision",
            }
        )
        if filtered:
            return filtered

    if not report_include_body_text():
        return None

    from belener.body_filter import body_text_usable, filter_body_text

    text = filter_body_text((ocr_text or "").strip())
    if not text or _is_garbage_body_text(text) or not body_text_usable(text):
        return None

    sections = _split_tt_items(text)
    if not sections:
        return None
    from belener.notes_filter import section_looks_like_tt

    sections = [s for s in sections if section_looks_like_tt(str(s.get("text") or ""))]
    if not sections:
        return None
    return {
        "title": "Технические требования",
        "sections": sections,
        "full_text": "",
        "source": "ocr",
    }
