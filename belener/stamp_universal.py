"""Универсальное чтение рамки (правый нижний угол): только то, что видно на изображении."""

from __future__ import annotations

import logging
import re
from typing import Any

import fitz
import ollama

from belener.config import (
    ollama_host,
    vision_num_predict,
    vision_stamp_dpi,
    vision_stamp_max_side,
    vision_timeout_sec,
    vision_zones_model,
)
from belener.ocr import zone_to_base64_png
from belener.vision_zones import _parse_json_response, pick_vision_model

log = logging.getLogger("belener.stamp_universal")

_SYSTEM = """На изображении — основная надпись (рамка) инженерного чертежа.
Перепиши всё читаемое содержимое этой рамки. Не выдумывай и не дополняй.

Ответ — один JSON без пояснений:
{
  "field_rows": [{"label": "подпись поля как на чертеже", "value": "значение в ячейке"}],
  "signature_table": {
    "headers": ["заголовки колонок таблицы подписей, как на чертеже"],
    "rows": [["текст ячейки", "..."]]
  },
  "revision_table": {
    "headers": ["заголовки таблицы изменений"],
    "rows": [["...", "..."]]
  },
  "section_titles": ["наименования разделов/листов внутри рамки"],
  "other_lines": ["остальной текст рамки по порядку сверху вниз"]
}

Правила:
- label, headers — дословно с чертежа (любой язык).
- Пустые ячейки не включай.
- Если таблицы нет — не добавляй signature_table / revision_table.
- Не переноси текст с поля чертежа вне рамки."""


def _vision_json(client: ollama.Client, model: str, b64: str) -> dict[str, Any] | None:
    try:
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": "Прочитай рамку полностью.", "images": [b64]},
            ],
            format="json",
            options={"temperature": 0, "num_predict": max(vision_num_predict(), 2400)},
        )
        raw = (resp.get("message") or {}).get("content") or ""
        data = _parse_json_response(raw)
        if data:
            return data
        log.warning("stamp universal: no JSON in vision response (%s chars)", len(raw))
        return None
    except Exception as exc:
        log.exception("stamp universal vision JSON failed: %s", exc)
        return None


def _table_to_rows(table: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(table, dict):
        return []
    headers = [str(h).strip() for h in (table.get("headers") or []) if str(h).strip()]
    rows_out: list[dict[str, str]] = []
    for row in table.get("rows") or []:
        if not isinstance(row, (list, tuple)):
            continue
        cells = [str(c).strip() for c in row if str(c).strip()]
        if not cells:
            continue
        if headers and len(headers) == len(cells):
            rows_out.append({headers[i]: cells[i] for i in range(len(cells))})
        else:
            rows_out.append({f"col{i + 1}": v for i, v in enumerate(cells)})
    return rows_out


def _signature_table_to_sigs(table: dict[str, Any] | None) -> list[dict[str, str]]:
    rows = _table_to_rows(table)
    sigs: list[dict[str, str]] = []
    for r in rows:
        vals = list(r.values())
        if len(vals) >= 2:
            sigs.append(
                {
                    "role": vals[0] if len(vals) > 2 else "—",
                    "name": vals[1] if len(vals) > 2 else vals[0],
                    "date": vals[2] if len(vals) > 2 else (vals[1] if len(vals) == 2 else "—"),
                    "sign": "—",
                }
            )
        elif len(vals) == 1:
            sigs.append({"role": "—", "name": vals[0], "sign": "—", "date": "—"})
    return sigs


def structure_to_stamp(data: dict[str, Any]) -> dict[str, Any]:
    """Факты штампа без нормализации к шаблону ГОСТ."""
    kv: list[dict[str, str]] = []
    for item in data.get("field_rows") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if label and value:
            kv.append({"field": label, "value": value})

    sigs = _signature_table_to_sigs(data.get("signature_table"))
    revisions = _table_to_rows(data.get("revision_table"))
    titles = [str(t).strip() for t in (data.get("section_titles") or []) if str(t).strip()]
    other = [str(ln).strip() for ln in (data.get("other_lines") or []) if str(ln).strip()]

    return {
        "kv": kv,
        "signatures": sigs,
        "revisions": revisions,
        "titles": titles,
        "other_lines": other,
        "raw_frame": data,
        "source": "stamp_universal",
    }


def extract_stamp_universal(
    doc: fitz.Document,
    stamp_rect: fitz.Rect,
    *,
    page_index: int = 0,
) -> dict[str, Any] | None:
    """Vision: вся рамка → структура как на чертеже."""
    if stamp_rect.is_empty:
        return None
    try:
        client = ollama.Client(host=ollama_host(), timeout=vision_timeout_sec())
        model = vision_zones_model() or pick_vision_model(client)
        if not model:
            log.warning("stamp universal: no vision model")
            return None
        b64 = zone_to_base64_png(
            doc,
            page_index,
            stamp_rect,
            vision_stamp_dpi(),
            max_side=vision_stamp_max_side(),
        )
        if not b64:
            return None
        data = _vision_json(client, model, b64)
        if not data:
            return None
        stamp = structure_to_stamp(data)
        if not (stamp.get("kv") or stamp.get("signatures") or stamp.get("revisions") or stamp.get("titles")):
            if not stamp.get("other_lines"):
                return None
        log.info(
            "stamp universal kv=%s sigs=%s rev=%s titles=%s",
            len(stamp.get("kv") or []),
            len(stamp.get("signatures") or []),
            len(stamp.get("revisions") or []),
            len(stamp.get("titles") or []),
        )
        return stamp
    except Exception:
        log.exception("extract_stamp_universal failed")
        return None
