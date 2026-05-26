"""Vision по вырезанным зонам листа — локально через Ollama."""

from __future__ import annotations

import json
import re
from typing import Any

import fitz
import ollama

from belener.config import (
    layout_vision_enabled,
    ollama_host,
    stamp_universal_enabled,
    vision_combined,
    vision_num_predict,
    vision_stamp_dpi,
    vision_stamp_max_side,
    vision_table_dpi,
    vision_table_max_side,
    vision_timeout_sec,
    vision_layout_max_blocks,
    vision_body_dpi,
    vision_body_max_side,
    vision_zone_dpi,
    vision_zones_model,
)
from belener.layout import LayoutBlock
from belener.ocr import zone_to_base64_png
from belener.parse import STAMP_KV_ORDER, _dedupe_titles, clean_table_title, normalize_signatures
from belener.stamp_llm import sanitize_llm_stamp

_STAMP_SYSTEM = """На изображении — основная надпись (штамп) чертежа ГОСТ.
Перепиши видимый текст дословно. Ответ — один JSON-объект, без пояснений.

{
  "kv": [{"field": "Обозначение / шифр", "value": "..."}, ...],
  "signatures": [{"role": "Разраб.", "name": "...", "date": "..."}, ...],
  "titles": ["..."],
  "summary": ""
}

Обязательно прочитай все поля штампа: организация, город/адрес, масштаб, формат, стадия (буква),
лист, стадия документации, очередь строительства, обозначение/шифр.
Таблица подписей — все видимые строки с ролями, фамилиями и датами (сколько есть на листе).
Не путай «Копировал» с подписью «Разраб.». Не путай номер листа с датой.

"Стадия (обозначение)" = одна буква (С, Р, П). Не путай с очередью и разделами.
"Очередь строительства" = например "I очередь строительства".
"Стадия" = этап документации (Подготовительный период, Рабочая документация).
"titles" = все наименования разделов/листов в рамке штампа (сколько есть на изображении — столько и в массиве).
Масштаб — только как на листе (1:500, 1:1000). Формат — A4x4, A3.
Фамилия — одно слово из графы подписей. Дата — ММ.ГГ (например 11.25). Нет значения → "—".

Прочитай ВСЮ видимую рамку на изображении целиком: таблица изменений (Изм., Кол., Лист, Подп., Дата),
организация, город, все графы подписей с фамилиями, шифр/обозначение документа, масштаб, формат, стадия, лист.
Не пропускай строки подписей (Разраб., Н.контр., Нач. отд., Гл. спец., Утв., Пров.)."""

_TABLES_SYSTEM = """На изображении — одна или несколько таблиц инженерного чертежа (обычно справа на листе).
Названия, количество и тип таблиц могут быть любыми — перепиши каждую полностью с заголовком как на листе.

Ответ — JSON:
{
  "tables": [
    {
      "title": "заголовок таблицы как на листе",
      "kind": "explication",
      "rows": [{"plan_number": "3", "name": "...", "grid": "—", "note": "..."}]
    },
    {
      "title": "заголовок второй таблицы",
      "kind": "legend",
      "rows": [{"note": "..."}]
    }
  ]
}

kind:
- explication — экспликация, ведомость, перечень объектов (колонки: №, наименование, координаты, примечание)
- legend — условные обозначения, легенда (колонка note = текст примечания)
- table — прочая таблица; rows как list объектов с полями ячеек

Символы/линии в legend не описывай. Нет таблиц → "tables": []."""

_EXPL_SYSTEM = """На изображении — таблица экспликации/ведомости объектов инженерного чертежа.
Заголовок может называться по-разному (экспликация, ведомость, перечень).
Перепиши заголовок таблицы и все строки. Ответ — JSON:

{"explication_title": "...", "explication": [{"plan_number": "3", "name": "...", "grid": "—", "note": "..."}]}

Колонки: номер на плане, наименование, координаты, примечание. Нет данных → []."""

_LEGEND_SYSTEM = """На изображении — таблица условных обозначений / легенды.
Заголовок может называться по-разному. Перепиши заголовок и текст колонки «Примечание» для каждой строки.
Символы/линии не описывай. Ответ — JSON:

{"legend_title": "...", "legend": [{"note": "..."}]}

Только строки таблицы. Нет данных → []."""

_COMBINED_SYSTEM = """Два фрагмента одного инженерного чертежа.
Изображение 1 — основная надпись (штамп). Изображение 2 — таблицы справа (экспликация, ведомости, материалы, легенда).
Перепиши всё дословно. Ответ — один JSON без пояснений:

{
  "kv": [{"field": "Обозначение / шифр", "value": "..."}],
  "signatures": [{"role": "Разраб.", "name": "...", "date": "..."}],
  "titles": ["наименования разделов из штампа"],
  "tables": [
    {"title": "заголовок как на листе", "kind": "explication", "table_number": "Таблица 1",
     "rows": [{"plan_number": "1", "name": "...", "grid": "—", "note": "..."}]},
    {"title": "Материалы", "kind": "table", "rows": [{"note": "..."}]}
  ]
}

Штамп: организация, масштаб, формат, стадия, лист, шифр/обозначение полностью, как на листе.
Таблицы: каждая с title и table_number если есть; kind: explication | legend | table.
Не включай в tables текст технических требований (нумерованные пункты ТТ) — только строки таблиц."""

_BODY_SYSTEM = """На изображении — текст инженерного чертежа вне таблиц: технические требования (ТТ),
примечания, нумерованные пункты, подписи к узлам, размерные пояснения.
Перепиши весь читаемый текст, сохраняя нумерацию и абзацы. Ответ — JSON:

{"title": "Технические требования", "sections": [{"number": "1", "text": "..."}], "full_text": "полный текст блока"}

Если заголовок виден (Технические требования, Примечания) — укажи в title. full_text — сплошной текст всех пунктов."""

_TABLE_BLOCK_SYSTEM = """На изображении — отдельная таблица или фрагмент таблицы инженерного чертежа.
Прочитай ВСЕ видимые строки и ВСЕ текстовые колонки. Не обобщай и не пропускай пустые на вид строки с текстом.

Ответ — JSON:
{
  "title": "заголовок над таблицей или внутри фрагмента",
  "table_number": "Таблица 1 / Продолжение таблицы 1 / ...",
  "kind": "table",
  "headers": ["Поз. обозначение", "Наименование", "Кол.", "Примечание"],
  "rows": [
    {"Поз. обозначение": "...", "Наименование": "...", "Кол.": "...", "Примечание": "..."}
  ]
}

Правила:
- Только текст, который реально виден на изображении. Не угадывай типовые строки перечня.
- Если таблицы нет или текст нечитаем — rows: [].
- Если видишь «Условные обозначения», kind = "legend", строки: {"Обозначение": "...", "Наименование": "..."}.
- Если это продолжение таблицы, укажи table_number = "Продолжение таблицы N" только если заголовок виден.
- Если ячейка пустая — "—".
- Не добавляй то, чего не видно."""


def _installed_models(client: ollama.Client) -> list[str]:
    try:
        resp = client.list()
        models = getattr(resp, "models", None) or []
        out: list[str] = []
        for m in models:
            name = getattr(m, "model", None) or (m.get("name") if isinstance(m, dict) else None)
            if name:
                out.append(str(name))
        return out
    except Exception:
        return []


def _is_vision_model(name: str) -> bool:
    low = name.lower()
    if "gemma" in low and "vl" not in low and "vision" not in low:
        return False
    return any(
        x in low
        for x in ("llava", "moondream", "minicpm-v", "bakllava", "qwen2.5vl", "qwen-vl", "vision", ":vl")
    )


def pick_vision_model(client: ollama.Client) -> str | None:
    preferred = vision_zones_model()
    installed = _installed_models(client)
    if not installed:
        return None
    if preferred:
        if preferred in installed:
            return preferred
        for m in installed:
            if preferred.split(":")[0] in m and _is_vision_model(m):
                return m
    for m in installed:
        if _is_vision_model(m):
            return m
    return None


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _vision_json(
    client: ollama.Client,
    model: str,
    system: str,
    user: str,
    images_b64: str | list[str],
) -> dict[str, Any] | None:
    imgs = [images_b64] if isinstance(images_b64, str) else [x for x in images_b64 if x]
    if not imgs:
        return None
    try:
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user, "images": imgs},
            ],
            options={"temperature": 0, "num_predict": vision_num_predict()},
            format="json",
        )
        raw = (resp.get("message") or {}).get("content") or ""
        data = _parse_json_response(raw)
        if data:
            return data
    except Exception:
        return None
    return None


def _sanitize_block_table(data: dict[str, Any], *, fallback_title: str = "") -> dict[str, Any] | None:
    title = clean_table_title(str(data.get("title") or fallback_title or "").strip())
    table_number = re.sub(r"\s+", " ", str(data.get("table_number") or "").strip())
    kind = str(data.get("kind") or "table").strip() or "table"
    headers = [re.sub(r"\s+", " ", str(h or "").strip()) for h in data.get("headers") or []]
    rows: list[dict[str, str]] = []
    for raw in data.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        row: dict[str, str] = {}
        keys = headers or [str(k) for k in raw.keys()]
        for key in keys:
            val = raw.get(key)
            if val is None:
                # tolerate models that normalized header spelling differently
                val = next((v for k, v in raw.items() if str(k).casefold() == str(key).casefold()), None)
            row[str(key or "—")] = re.sub(r"\s+", " ", str(val or "—").strip()) or "—"
        if any(v != "—" for v in row.values()):
            rows.append(row)
    if not rows:
        return None
    if kind == "legend":
        normalized = []
        for r in rows:
            normalized.append(
                {
                    "symbol": r.get("Обозначение") or r.get("обозначение") or "—",
                    "note": r.get("Наименование") or r.get("наименование") or next(iter(r.values()), "—"),
                }
            )
        return {"title": title or "Условные обозначения", "kind": "legend", "rows": normalized, "table_number": table_number}
    return {"title": title, "kind": kind, "rows": rows, "table_number": table_number}


_SECTION_ROW_NAMES = frozenset(
    {"детали", "материалы", "экспликация", "ведомость", "наименование", "примечание"}
)


def _sanitize_expl(rows: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        name = re.sub(r"\s+", " ", str(item.get("name") or "").strip())
        if len(name) < 4:
            continue
        if name.casefold() in _SECTION_ROW_NAMES and not re.search(r"\d", name):
            continue
        out.append(
            {
                "plan_number": str(item.get("plan_number") or "—").strip() or "—",
                "name": name,
                "grid": str(item.get("grid") or "—").strip() or "—",
                "note": str(item.get("note") or "—").strip() or "—",
            }
        )
    return out


def _sanitize_legend(rows: list[Any]) -> list[dict[str, str]]:
    from belener.parse import LEGEND_SYMBOL_PLACEHOLDER, _clean_legend_note, _is_garbage_legend_note

    out: list[dict[str, str]] = []
    from belener.parse import split_merged_legend_note

    seen: set[str] = set()
    for item in rows or []:
        raw = str((item or {}).get("note") if isinstance(item, dict) else item or "")
        for note in split_merged_legend_note(raw):
            note = _clean_legend_note(note)
            if not note or _is_garbage_legend_note(note):
                continue
            key = note.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append({"symbol": LEGEND_SYMBOL_PLACEHOLDER, "note": note})
    return out


def _sanitize_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in data.get("tables") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "table").strip() or "table"
        title = clean_table_title(str(item.get("title") or "").strip())
        rows_raw = item.get("rows") or []
        if kind == "explication":
            rows = _sanitize_expl(rows_raw)
        elif kind == "legend":
            rows = _sanitize_legend(rows_raw)
        else:
            expl = _sanitize_expl(rows_raw)
            leg = _sanitize_legend(rows_raw)
            if len(expl) >= len(leg) and expl:
                kind, rows = "explication", expl
            elif leg:
                kind, rows = "legend", leg
            else:
                rows = []
        if not rows:
            continue
        table_number = re.sub(r"\s+", " ", str(item.get("table_number") or "").strip())
        out.append({"title": title, "kind": kind, "rows": rows, "table_number": table_number})
    return out


def _tables_from_legacy(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    expl = _sanitize_expl(data.get("explication") or [])
    if expl:
        title = clean_table_title(str(data.get("explication_title") or "").strip())
        out.append({"title": title, "kind": "explication", "rows": expl})
    leg = _sanitize_legend(data.get("legend") or [])
    if leg:
        title = clean_table_title(str(data.get("legend_title") or "").strip())
        out.append({"title": title, "kind": "legend", "rows": leg})
    return out


def _build_stamp(data: dict[str, Any]) -> dict[str, Any] | None:
    clean = sanitize_llm_stamp(data)
    if not any((clean.get("kv"), clean.get("signatures"), clean.get("titles"))):
        return None
    kv_order = (
        "Обозначение / шифр",
        "Организация",
        "Город / адрес",
        "Масштаб",
        "Формат",
        "Стадия (обозначение)",
        "Стадия",
        "Очередь строительства",
        "Лист",
        "Копировал",
    )
    kv_map = clean.get("kv") or {}
    if isinstance(kv_map, list):
        kv_map = {str(x.get("field", "")): str(x.get("value", "")) for x in kv_map if x.get("field")}
    return {
        "kv": [{"field": f, "value": kv_map[f]} for f in kv_order if kv_map.get(f)],
        "cipher_candidates": [kv_map["Обозначение / шифр"]] if kv_map.get("Обозначение / шифр") else [],
        "signatures": normalize_signatures(list(clean.get("signatures") or [])),
        "titles": _dedupe_titles(clean.get("titles") or [], kv_map if isinstance(kv_map, dict) else {}),
        "summary": "",
    }


def _vision_stamp(
    doc: fitz.Document,
    rect: fitz.Rect,
    dpi: int,
    client: ollama.Client,
    model: str,
    *,
    max_side: int | None = None,
) -> dict[str, Any] | None:
    b64 = zone_to_base64_png(doc, 0, rect, dpi, max_side=max_side or vision_stamp_max_side())
    data = _vision_json(client, model, _STAMP_SYSTEM, "Прочитай штамп.", b64)
    return _build_stamp(data) if data else None


def _vision_combined_stamp_tables(
    doc: fitz.Document,
    stamp_rect: fitz.Rect,
    table_rect: fitz.Rect,
    client: ollama.Client,
    model: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    stamp_b64 = zone_to_base64_png(
        doc, 0, stamp_rect, vision_stamp_dpi(), max_side=vision_stamp_max_side()
    )
    table_b64 = zone_to_base64_png(
        doc, 0, table_rect, vision_table_dpi(), max_side=vision_table_max_side()
    )
    if not stamp_b64 or not table_b64:
        return None, None
    data = _vision_json(
        client,
        model,
        _COMBINED_SYSTEM,
        "Изображение 1 — штамп. Изображение 2 — таблицы. Верни JSON.",
        [stamp_b64, table_b64],
    )
    if not data:
        return None, None
    stamp = _build_stamp(data)
    tables = _sanitize_tables(data) or _tables_from_legacy(data)
    return stamp, tables or None


def _sanitize_body(data: dict[str, Any]) -> dict[str, Any]:
    title = clean_table_title(str(data.get("title") or "Текст на листе").strip()) or "Текст на листе"
    sections: list[dict[str, str]] = []
    for item in data.get("sections") or []:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "").strip())
        if len(text) < 8:
            continue
        sections.append(
            {
                "number": str(item.get("number") or "").strip(),
                "text": text,
            }
        )
    full_text = re.sub(r"\n{3,}", "\n\n", str(data.get("full_text") or "").strip())
    if not full_text and sections:
        parts = []
        for s in sections:
            num = s.get("number") or ""
            parts.append(f"{num}. {s['text']}" if num else s["text"])
        full_text = "\n\n".join(parts)
    return {"title": title, "sections": sections, "full_text": full_text}


def _vision_body(
    doc: fitz.Document,
    rect: fitz.Rect,
    client: ollama.Client,
    model: str,
) -> dict[str, Any] | None:
    b64 = zone_to_base64_png(
        doc, 0, rect, vision_body_dpi(), max_side=vision_body_max_side()
    )
    data = _vision_json(
        client,
        model,
        _BODY_SYSTEM,
        "Прочитай весь текст вне таблиц (ТТ, примечания, пункты).",
        b64,
    )
    return _sanitize_body(data) if data else None


def _vision_table_block(
    doc: fitz.Document,
    block: LayoutBlock,
    client: ollama.Client,
    model: str,
) -> dict[str, Any] | None:
    b64 = zone_to_base64_png(
        doc,
        0,
        block.rect,
        vision_table_dpi(),
        max_side=vision_table_max_side(),
    )
    data = _vision_json(
        client,
        model,
        _TABLE_BLOCK_SYSTEM,
        "Прочитай эту таблицу полностью и верни JSON.",
        b64,
    )
    return _sanitize_block_table(data or {}, fallback_title=block.label)


def extract_layout_blocks_vision(
    doc: fitz.Document,
    blocks: list[LayoutBlock],
) -> dict[str, Any]:
    """Read detected layout blocks independently to preserve small table text."""
    result: dict[str, Any] = {
        "stamp": None,
        "tables": [],
        "sheet_notes": None,
        "text_blocks": [],
        "vision_model": None,
        "ok": False,
        "errors": [],
    }
    if not layout_vision_enabled():
        return result
    try:
        client = ollama.Client(host=ollama_host(), timeout=vision_timeout_sec())
        model = pick_vision_model(client)
        if not model:
            return result
        result["vision_model"] = model
        stamp_blocks = [b for b in blocks if b.kind == "stamp"][:1]
        table_blocks = [b for b in blocks if b.kind == "table"][:vision_layout_max_blocks()]
        for idx, block in enumerate([*stamp_blocks, *table_blocks]):
            try:
                if block.kind == "stamp":
                    stamp = _vision_stamp(
                        doc,
                        block.rect,
                        vision_stamp_dpi(),
                        client,
                        model,
                        max_side=vision_stamp_max_side(),
                    )
                    if stamp:
                        result["stamp"] = stamp
                elif block.kind == "table":
                    table = _vision_table_block(doc, block, client, model)
                    if table:
                        table["bbox"] = [round(block.rect.x0, 2), round(block.rect.y0, 2), round(block.rect.x1, 2), round(block.rect.y1, 2)]
                        result["tables"].append(table)
            except Exception as exc:
                result["errors"].append(f"{block.kind}:{idx}:{exc.__class__.__name__}")
                continue
        result["ok"] = bool(result["stamp"] or result["tables"])
        return result
    except Exception as exc:
        result["errors"].append(exc.__class__.__name__)
        return result


def _vision_expl(doc: fitz.Document, rect: fitz.Rect, dpi: int, client: ollama.Client, model: str) -> list[dict[str, str]]:
    b64 = zone_to_base64_png(doc, 0, rect, dpi)
    data = _vision_json(client, model, _EXPL_SYSTEM, "Прочитай экспликацию.", b64)
    if not data:
        return []
    return _sanitize_expl(data.get("explication") or [])


def _vision_legend(doc: fitz.Document, rect: fitz.Rect, dpi: int, client: ollama.Client, model: str) -> list[dict[str, str]]:
    b64 = zone_to_base64_png(doc, 0, rect, dpi)
    data = _vision_json(client, model, _LEGEND_SYSTEM, "Прочитай легенду.", b64)
    if not data:
        return []
    return _sanitize_legend(data.get("legend") or [])


def _vision_table_for_zone(
    doc: fitz.Document,
    zone_key: str,
    rect: fitz.Rect,
    client: ollama.Client,
    model: str,
    t_dpi: int,
) -> dict[str, Any] | None:
    """Vision по типу зоны: BOM / легенда / прочее — без подмены колонок экспликацией."""
    b64 = zone_to_base64_png(doc, 0, rect, t_dpi, max_side=vision_table_max_side())
    if not b64:
        return None

    if zone_key in ("spec_right", "spec_left", "tables_block"):
        from belener.config import vision_tables_enabled

        if not vision_tables_enabled():
            return None
        data = _vision_json(
            client,
            model,
            _TABLE_BLOCK_SYSTEM,
            "Прочитай только видимую таблицу на фрагменте. "
            "Если это не таблица или текст неразборчив — верни rows: [].",
            b64,
        )
        if not data:
            return None
        tbl = _sanitize_block_table(data)
        if tbl:
            tbl["kind"] = "specification"
        return tbl

    if zone_key == "legend_table":
        data = _vision_json(
            client,
            model,
            _LEGEND_SYSTEM,
            "Только таблица условных обозначений (2–6 строк). Символы не описывай.",
            b64,
        )
        if not data:
            return None
        leg = _sanitize_legend(data.get("legend") or [])
        if leg:
            title = clean_table_title(str(data.get("legend_title") or "").strip())
            return {
                "title": title or "Условные обозначения",
                "kind": "legend",
                "rows": leg,
                "table_number": "Таблица 2",
            }
        return _sanitize_block_table(data, fallback_title="Условные обозначения")

    data = _vision_json(
        client,
        model,
        _TABLE_BLOCK_SYSTEM,
        "Прочитай все таблицы на изображении с заголовками.",
        b64,
    )
    if not data:
        return None
    if data.get("headers") or data.get("rows"):
        return _sanitize_block_table(data)
    tables = _sanitize_tables(data)
    return tables[0] if tables else None


def _table_zone_rects(zones) -> list[tuple[str, fitz.Rect]]:
    """Зоны перечня аппаратуры / легенды — без правой колонки подписей схемы."""
    out: list[tuple[str, fitz.Rect]] = []
    seen: set[str] = set()
    has_bom = bool(zones.rects.get("spec_right") or zones.rects.get("spec_left"))
    for key in (
        "spec_right",
        "spec_left",
        "legend_table",
        "tables_block",
        "explication",
        "legend",
    ):
        rect = zones.rects.get(key)
        if rect is None:
            continue
        sig = f"{round(rect.x0)}:{round(rect.y0)}:{round(rect.x1)}:{round(rect.y1)}"
        if sig in seen:
            continue
        seen.add(sig)
        out.append((key, rect))
    if not has_bom:
        rect = zones.rects.get("right_column")
        if rect is not None:
            out.append(("right_column", rect))
    return out


def extract_zones_vision(
    doc: fitz.Document,
    zones,
    *,
    stamp_dpi: int | None = None,
    table_dpi: int | None = None,
    include_stamp: bool = True,
    include_tables: bool = True,
    include_body: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "stamp": None,
        "explication": None,
        "legend": None,
        "tables": None,
        "sheet_notes": None,
        "explication_title": None,
        "legend_title": None,
        "vision_model": None,
        "ok": False,
    }
    if not include_stamp and not include_tables and not include_body:
        return result
    try:
        client = ollama.Client(host=ollama_host(), timeout=vision_timeout_sec())
        model = pick_vision_model(client)
        if not model:
            return result
        result["vision_model"] = model
        v_dpi = stamp_dpi or vision_stamp_dpi() or vision_zone_dpi()
        t_dpi = table_dpi or vision_table_dpi() or vision_zone_dpi()

        from belener.zones import stamp_ocr_rect

        stamp_rect = stamp_ocr_rect(zones, doc[0].rect)
        table_rects = _table_zone_rects(zones)
        table_rect = table_rects[0][1] if len(table_rects) == 1 else None

        use_combined = (
            vision_combined()
            and not stamp_universal_enabled()
            and include_stamp
            and include_tables
            and stamp_rect is not None
            and table_rect is not None
            and len(table_rects) <= 1
        )

        if use_combined:
            stamp_data, tables_data = _vision_combined_stamp_tables(
                doc, stamp_rect, table_rect, client, model
            )
            if stamp_data:
                result["stamp"] = stamp_data
            if tables_data:
                result["tables"] = tables_data
        else:
            if include_stamp and stamp_rect is not None:
                if stamp_universal_enabled():
                    from belener.stamp_universal import extract_stamp_universal

                    result["stamp"] = extract_stamp_universal(doc, stamp_rect, page_index=0)
                else:
                    result["stamp"] = _vision_stamp(doc, stamp_rect, v_dpi, client, model)

            if include_tables and table_rects:
                from belener.parse import merge_table_sections

                merged_tables: list[dict[str, Any]] = []
                for zone_key, zone_rect in table_rects:
                    part = _vision_table_for_zone(
                        doc, zone_key, zone_rect, client, model, t_dpi
                    )
                    if part:
                        merged_tables = merge_table_sections(merged_tables, [part])
                if merged_tables:
                    result["tables"] = merged_tables

        if result["tables"]:
            for sec in result["tables"]:
                if sec.get("kind") == "explication" and not result["explication"]:
                    result["explication"] = sec.get("rows")
                    result["explication_title"] = sec.get("title")
                if sec.get("kind") == "legend" and not result["legend"]:
                    result["legend"] = sec.get("rows")
                    result["legend_title"] = sec.get("title")

        if include_body:
            body_rect = zones.rects.get("sheet_notes") or zones.rects.get("body")
            if body_rect is not None:
                result["sheet_notes"] = _vision_body(doc, body_rect, client, model)

        if not result["tables"] and (result["explication"] or result["legend"]):
            result["tables"] = _tables_from_legacy(
                {
                    "explication": result["explication"] or [],
                    "legend": result["legend"] or [],
                    "explication_title": result.get("explication_title"),
                    "legend_title": result.get("legend_title"),
                }
            )

        result["ok"] = bool(
            result["stamp"]
            or result["tables"]
            or result["explication"]
            or result["legend"]
            or (result.get("sheet_notes") or {}).get("full_text")
            or (result.get("sheet_notes") or {}).get("sections")
        )
        if not result["ok"]:
            import logging

            logging.getLogger("belener.vision_zones").info(
                "vision zones empty stamp=%s tables=%s body=%s model=%s",
                bool(result.get("stamp")),
                bool(result.get("tables")),
                bool(result.get("sheet_notes")),
                result.get("vision_model"),
            )
        return result
    except Exception:
        import logging

        logging.getLogger("belener.vision_zones").exception("extract_zones_vision failed")
        return result


def vision_postprocess_sheet(
    doc: fitz.Document,
    zones,
    *,
    include_stamp: bool = False,
    include_tables: bool = False,
    include_sheet_text: bool = False,
) -> dict[str, Any]:
    """Постобработка vision после OCR: дозаполнение штампа, таблиц, текста вне таблиц."""
    return extract_zones_vision(
        doc,
        zones,
        stamp_dpi=vision_stamp_dpi(),
        table_dpi=vision_table_dpi(),
        include_stamp=include_stamp,
        include_tables=include_tables,
        include_body=include_sheet_text,
    )
