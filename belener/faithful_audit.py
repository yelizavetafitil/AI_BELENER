"""Проверка «честности» отчёта: строки BOM должны опираться на OCR зон spec_*."""

from __future__ import annotations

from typing import Any

from belener.grounding import row_grounded_in_ocr


def _spec_blob(zone_ocr_texts: dict[str, str], table_text: str = "") -> str:
    parts: list[str] = []
    for key in sorted(zone_ocr_texts or {}):
        if key.startswith("spec_"):
            t = str(zone_ocr_texts.get(key) or "").strip()
            if t:
                parts.append(t)
    if table_text.strip():
        parts.append(table_text.strip())
    return "\n\n".join(parts)


def audit_drawing_faithful(drawing: dict[str, Any]) -> dict[str, Any]:
    """
    Возвращает метрики для benchmark/CI:
    - ungrounded_rows: строки спецификации без подтверждения в spec OCR
    - spec_ocr_chars
    """
    zone_ocr = dict(drawing.get("zone_ocr_texts") or {})
    blob = _spec_blob(zone_ocr)
    ungrounded: list[dict[str, str]] = []
    spec_rows = 0
    for table in drawing.get("tables") or []:
        if str(table.get("kind") or "") != "specification":
            continue
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            spec_rows += 1
            if not row_grounded_in_ocr(row, blob):
                ungrounded.append(
                    {
                        "Поз.": str(row.get("Поз.") or ""),
                        "Обозначение": str(row.get("Обозначение") or ""),
                        "Наименование": str(row.get("Наименование") or "")[:80],
                    }
                )
    return {
        "spec_ocr_chars": len(blob),
        "spec_rows": spec_rows,
        "ungrounded_count": len(ungrounded),
        "ungrounded_sample": ungrounded[:12],
        "faithful_ok": len(ungrounded) == 0 or spec_rows == 0,
    }
