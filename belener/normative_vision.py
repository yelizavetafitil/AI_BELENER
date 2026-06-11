"""Vision (Ollama qwen2.5vl / …) для извлечения нормативов — точность как у PNG в чате."""

from __future__ import annotations

import logging
import time
from typing import Any

import fitz
import ollama

from belener.config import (
    normative_vision_dpi,
    normative_vision_max_side,
    ollama_host,
    vision_num_predict,
    vision_timeout_sec,
    vision_zones_model,
)
from belener.normative_refs import extract_normative_refs, merge_normative_refs
from belener.ocr import zone_to_base64_png
from belener.vision_zones import _parse_json_response, pick_vision_model

log = logging.getLogger("belener.normative_vision")

_SYSTEM = """На изображении — страница PDF или скан (текст, ТТ, чертёж, таблицы).
Найди ВСЕ упоминания нормативных документов и технических условий.

Типы (kind) определяй по контексту строки, не только по явной аббревиатуре:
- ГОСТ, ГОСТ Р, GOST — национальные стандарты (5264-80, 9.602-2016, 19903-2015)
- ОСТ, OCT, OST — отраслевые (34-10-615-93)
- СТБ, STB — белорусские стандарты
- ТУ — технические условия (длинный номер с годом)
- СТП, СНиП, СП, ISO, IEC, DIN, EN, API, ASTM, РД, НПБ, ВСН и др. — если явно указаны как норматив

НЕ включай: размеры (4x300), координаты (190,464), обозначения швов (T1-Δ6), массу, формат листа.

Ответ — один JSON без пояснений:
{
  "norms": [
    {"kind": "ГОСТ", "ref": "ГОСТ 9.602-2016", "context": "защита от коррозии"},
    {"kind": "ОСТ", "ref": "ОСТ 34-10-615-93", "context": "..."},
    {"kind": "СТБ", "ref": "СТБ 1544-2005", "context": "..."},
    {"kind": "ТУ", "ref": "ТУ 1461-063-90910065-2013", "context": "..."}
  ]
}

Правила:
- ref — **как на листе**: тип, пробелы, точки, дефисы, ведущие цифры (02 ОСТ 34 10.754-97).
- kind — по аббревиатуре в ref (ГОСТ, ОСТ, СТП, ТУ, РД, СО, …).
- Не выдумывай документы, которых нет на изображении.
- Если ничего не найдено: {"norms": []}."""


def vision_available() -> bool:
    try:
        client = ollama.Client(host=ollama_host(), timeout=8)
        return pick_vision_model(client) is not None
    except Exception:
        return False


def _vision_norms_from_b64(client: ollama.Client, model: str, b64: str) -> list[dict[str, str]]:
    if not b64:
        return []
    try:
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": "Перечисли все ГОСТ/ОСТ/СТП/ТУ на изображении.",
                    "images": [b64],
                },
            ],
            options={"temperature": 0, "num_predict": max(vision_num_predict(), 1200)},
        )
        raw = str(resp.get("message", {}).get("content") or "")
    except Exception:
        log.exception("normative vision request failed model=%s", model)
        return []

    data = _parse_json_response(raw) or {}
    norms = data.get("norms") or []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in norms:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or "").strip()
        kind = str(item.get("kind") or "ГОСТ").strip()
        num = str(item.get("num") or item.get("number") or "").strip()
        if not ref and num:
            ref = f"{kind} {num}".strip()
        if not ref:
            continue
        parsed_list = extract_normative_refs(ref) or (extract_normative_refs(f"{kind} {num}".strip()) if num else [])
        if parsed_list:
            for parsed in parsed_list:
                key = parsed["ref"].casefold()
                if key in seen:
                    continue
                seen.add(key)
                ctx = str(item.get("context") or parsed.get("context") or parsed["ref"]).strip()
                out.append({**parsed, "context": ctx})
            continue
        key = ref.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "kind": kind,
                "ref": ref,
                "context": str(item.get("context") or ref).strip(),
            }
        )
    if not out and raw.strip():
        out = extract_normative_refs(raw)
    return out


def extract_normatives_page_vision(
    doc: fitz.Document,
    page_index: int,
    *,
    client: ollama.Client | None = None,
    model: str | None = None,
) -> tuple[list[dict[str, str]], str]:
    """Vision одной страницы PDF → список нормативов."""
    client = client or ollama.Client(host=ollama_host(), timeout=vision_timeout_sec())
    model = model or vision_zones_model() or pick_vision_model(client)
    if not model:
        return [], ""

    b64 = zone_to_base64_png(
        doc,
        page_index,
        doc[page_index].rect,
        dpi=normative_vision_dpi(),
        max_side=normative_vision_max_side(),
    )
    if not b64:
        return [], ""

    t0 = time.monotonic()
    refs = _vision_norms_from_b64(client, model, b64)
    log.info(
        "normative vision page %s model=%s %.1fs refs=%s",
        page_index + 1,
        model,
        time.monotonic() - t0,
        len(refs),
    )
    return refs, model


def extract_normatives_document_vision(
    doc: fitz.Document,
    filename: str = "document.pdf",
) -> dict[str, Any]:
    client = ollama.Client(host=ollama_host(), timeout=vision_timeout_sec())
    model = vision_zones_model() or pick_vision_model(client)
    if not model:
        return {"ok": False, "error": "Vision-модель не найдена в Ollama (нужен qwen2.5vl:7b или аналог)"}

    t0 = time.monotonic()
    merged: list[dict[str, str]] = []
    for i in range(doc.page_count):
        refs, _ = extract_normatives_page_vision(doc, i, client=client, model=model)
        merged = merge_normative_refs(merged, refs)

    log.info(
        "normative vision doc done %.1fs refs=%s pages=%s model=%s (%s)",
        time.monotonic() - t0,
        len(merged),
        doc.page_count,
        model,
        filename,
    )
    return {
        "ok": True,
        "filename": filename,
        "page_count": doc.page_count,
        "pipeline": f"normative_vision({model.split(':')[0]})",
        "normative_refs": merged,
        "vision_model": model,
        "source_text_chars": 0,
        "page_texts": [],
        "drawing": None,
    }


def extract_normatives_image_vision(path: str, filename: str | None = None) -> dict[str, Any]:
    import base64
    from pathlib import Path

    p = Path(path)
    client = ollama.Client(host=ollama_host(), timeout=vision_timeout_sec())
    model = vision_zones_model() or pick_vision_model(client)
    if not model:
        return {"ok": False, "error": "Vision-модель не найдена в Ollama"}

    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    t0 = time.monotonic()
    refs = _vision_norms_from_b64(client, model, b64)
    log.info(
        "normative vision image %.1fs refs=%s model=%s (%s)",
        time.monotonic() - t0,
        len(refs),
        model,
        filename or p.name,
    )
    return {
        "ok": True,
        "filename": filename or p.name,
        "page_count": 1,
        "pipeline": f"normative_vision({model.split(':')[0]})",
        "normative_refs": refs,
        "vision_model": model,
        "source_text_chars": 0,
        "page_texts": [],
        "drawing": None,
    }
