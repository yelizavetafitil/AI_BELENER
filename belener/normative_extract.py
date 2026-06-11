"""Быстрое извлечение нормативов (ГОСТ/ОСТ/ТУ/…) из PDF и изображений — OCR по страницам/плиткам."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import fitz

from belener.config import body_dpi
from belener.normative_refs import collect_normative_refs, extract_normative_refs

log = logging.getLogger("belener.normative_extract")

_TEXT_LAYER_MIN = 120
_NORMATIVE_HINT = re.compile(
    r"(?i)"
    r"(?:гост|gost)\s*[\dрr]"
    r"|(?:ост|ost)\s*[\d]"
    r"|(?:стп|stp)\s*[\d]"
    r"|(?:рд|rd)\s*[\d]"
    r"|(?:ту|tu)\s*[\d]"
    r"|(?:со|co|so)\s*[\d]"
    r"|(?:стб|stb)\s*[\d]"
    r"|снип"
    r"|\bsp\s*\d"
)


def _page_text(doc: fitz.Document, page_index: int) -> str:
    """Текстовый слой или OCR всей страницы (внутри — плитки для больших листов)."""
    page = doc[page_index]
    layer = (page.get_text("text") or "").strip()
    has_hint = bool(_NORMATIVE_HINT.search(layer))
    has_refs = bool(extract_normative_refs(layer)) if has_hint else False
    if len(layer) >= _TEXT_LAYER_MIN and has_hint and has_refs:
        return layer
    from belener.ocr import ocr_page_full

    dpi = body_dpi()
    t0 = time.monotonic()
    ocr = (ocr_page_full(doc, page_index, dpi=dpi) or "").strip()
    log.info(
        "normative OCR page %s dpi=%s %.1fs chars=%s layer_hint=%s layer_refs=%s",
        page_index + 1,
        dpi,
        time.monotonic() - t0,
        len(ocr),
        has_hint,
        has_refs,
    )
    if ocr and layer and not has_refs:
        return ocr
    return ocr or layer


def _text_in_rect(page: fitz.Page, rect: fitz.Rect | None) -> str:
    if rect is None or rect.is_empty:
        return ""
    try:
        return (page.get_text("text", clip=rect) or "").strip()
    except Exception:
        return ""


def _resolve_normative_zones(doc: fitz.Document, page_index: int):
    from belener.config import accuracy_mode, discover_zones_enabled, discover_zones_fast, yolo_zones_enabled
    from belener.discover import discover_sheet_zones
    from belener.zone_refine import refine_sheet_zones
    from belener.zones import build_zones

    page = doc[page_index]
    try:
        if discover_zones_enabled():
            zones = discover_sheet_zones(
                doc, page_index, page.rect, fast=discover_zones_fast() or not accuracy_mode()
            )
        else:
            zones = build_zones(page.rect)
        zones = refine_sheet_zones(doc, zones, page_index, classify_with_ocr=False)
        if yolo_zones_enabled():
            from belener.yolo_zones import apply_yolo_zones

            zones = apply_yolo_zones(doc, page_index, page.rect, zones)
        return zones
    except Exception:
        log.warning("normative zones failed page=%s", page_index + 1, exc_info=True)
        return build_zones(page.rect)


def _normative_table_jobs(zones) -> list[tuple[str, fitz.Rect]]:
    """Все табличные зоны — без unified early return (эскизы VR/GT)."""
    jobs: list[tuple[str, fitz.Rect]] = []
    seen: set[str] = set()
    for key in (
        "spec_right",
        "spec_left",
        "tables_block",
        "explication",
        "legend_table",
        "legend",
        "right_column",
        "body",
    ):
        rect = zones.rects.get(key)
        if rect is None or rect.is_empty:
            continue
        sig = f"{key}:{round(rect.x0)}:{round(rect.y0)}:{round(rect.x1)}:{round(rect.y1)}"
        if sig in seen:
            continue
        seen.add(sig)
        jobs.append((key, rect))
    if not jobs:
        from belener.sheet_read import _best_table_rect

        k, r = _best_table_rect(zones)
        if r is not None:
            jobs.append((k or "spec_right", r))
    return jobs


def _zone_normative_blobs(
    doc: fitz.Document,
    page_index: int,
    *,
    ocr_if_no_hint: bool = True,
) -> list[str]:
    """Текст табличных зон и ТТ — для эскизов CAD (NanoCAD/AutoCAD)."""
    from belener.config import table_dpi
    from belener.normative_scan import normative_scan_rect, ocr_normative_scan
    from belener.ocr import ocr_region, tesseract_available

    page = doc[page_index]
    zones = _resolve_normative_zones(doc, page_index)
    blobs: list[str] = []
    seen: set[tuple[int, int, int, int]] = set()

    for key, rect in _normative_table_jobs(zones):
        sig = (round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        if sig in seen:
            continue
        seen.add(sig)
        text = _text_in_rect(page, rect)
        if text:
            blobs.append(text)
        if ocr_if_no_hint and tesseract_available() and (not text or not _NORMATIVE_HINT.search(text)):
            try:
                ocr = (ocr_region(doc, page_index, rect, dpi=table_dpi(), zone=key) or "").strip()
            except Exception:
                ocr = ""
            if ocr:
                blobs.append(ocr)

    for key in ("sheet_notes", "legend_table", "legend"):
        rect = zones.rects.get(key)
        if rect is None or rect.is_empty:
            continue
        sig = (round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        if sig in seen:
            continue
        seen.add(sig)
        text = _text_in_rect(page, rect)
        if text:
            blobs.append(text)
        elif ocr_if_no_hint and tesseract_available():
            try:
                ocr = (ocr_region(doc, page_index, rect, dpi=table_dpi(), zone=key) or "").strip()
            except Exception:
                ocr = ""
            if ocr:
                blobs.append(ocr)

    ns = ocr_normative_scan(doc, page_index, page.rect, zones, force=True)
    if ns:
        blobs.append(ns)
    elif ocr_if_no_hint and tesseract_available():
        rect = normative_scan_rect(page.rect, zones)
        if not rect.is_empty:
            try:
                body = (ocr_region(doc, page_index, rect, dpi=table_dpi(), zone="body") or "").strip()
            except Exception:
                body = ""
            if body:
                blobs.append(body)

    return blobs


def _ocr_image_path(path: str) -> str:
    from PIL import Image

    from belener.ocr import ocr_image_multipass

    img = Image.open(path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    dpi = body_dpi()
    if max(w, h) > 2400:
        scale = 2400 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    return (ocr_image_multipass(img, dpi=dpi) or "").strip()


def _refs_from_texts(*texts: str) -> list[dict[str, str]]:
    from belener.normative_refs import merge_normative_refs

    groups = [extract_normative_refs(blob) for blob in texts if (blob or "").strip()]
    return merge_normative_refs(*groups) if groups else []


def extract_normatives_from_document(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    source_path: str | None = None,
    allow_drawing_fallback: bool = True,
) -> dict[str, Any]:
    """Vision (если есть) + OCR → объединённые нормативы."""
    from belener.config import normative_vision_enabled
    from belener.normative_refs import merge_normative_refs

    t0 = time.monotonic()
    page_texts: list[str] = []
    zone_blobs: list[str] = []
    for i in range(doc.page_count):
        page_texts.append(_page_text(doc, i))
        if doc.page_count <= 3:
            zone_blobs.extend(_zone_normative_blobs(doc, i, ocr_if_no_hint=False))
    combined = "\n\n".join(t for t in page_texts if t)
    ocr_refs = _refs_from_texts(combined, *zone_blobs)

    vision_refs: list[dict[str, str]] = []
    pipeline = "normative_page_ocr"
    vision_model = ""

    if normative_vision_enabled():
        from belener.normative_vision import extract_normatives_document_vision, vision_available

        if vision_available():
            vresult = extract_normatives_document_vision(doc, filename)
            if vresult.get("ok") and vresult.get("normative_refs"):
                vision_refs = list(vresult["normative_refs"])
                vision_model = str(vresult.get("vision_model") or "")
                pipeline = str(vresult.get("pipeline") or "normative_vision")
            elif vresult.get("ok") is False:
                log.warning("normative vision unavailable: %s", vresult.get("error"))
        else:
            log.warning("normative vision enabled but Ollama/vision model not ready — OCR only")

    layer = "\n\n".join(
        (doc[i].get_text("text") or "").strip() for i in range(doc.page_count)
    )
    layer_refs = _refs_from_texts(layer) if layer.strip() else []
    refs = merge_normative_refs(vision_refs, ocr_refs, layer_refs)
    if vision_refs:
        pipeline = f"{pipeline}+ocr" if ocr_refs else pipeline

    if not refs and doc.page_count <= 3:
        zone_ocr_blobs: list[str] = []
        for i in range(doc.page_count):
            zone_ocr_blobs.extend(_zone_normative_blobs(doc, i, ocr_if_no_hint=True))
        zone_refs = _refs_from_texts(*zone_ocr_blobs)
        if zone_refs:
            refs = merge_normative_refs(refs, zone_refs)
            pipeline = "normative_zones_ocr" if pipeline == "normative_page_ocr" else f"{pipeline}+zones"

    if not refs and doc.page_count == 1:
        try:
            from belener.config import stamp_block_dpi, table_dpi
            from belener.sheet_read import ocr_sheet_by_zones
            from belener.sheet_text import ocr_non_table_text

            zones = _resolve_normative_zones(doc, 0)
            zoned = ocr_sheet_by_zones(
                doc,
                zones,
                stamp_dpi=stamp_block_dpi(),
                table_dpi=table_dpi(),
                page_index=0,
            )
            sheet_blobs = [
                zoned.get("table_text") or "",
                zoned.get("sheet_notes_text") or "",
                *(zoned.get("zone_texts") or {}).values(),
            ]
            body, _ = ocr_non_table_text(doc, doc[0].rect, zones, page_index=0)
            if body:
                sheet_blobs.append(body)
            zoned_refs = _refs_from_texts(*sheet_blobs)
            if zoned_refs:
                refs = merge_normative_refs(refs, zoned_refs)
                pipeline = "normative_zoned_ocr"
        except Exception:
            log.warning("normative zoned OCR failed", exc_info=True)

    drawing: dict[str, Any] | None = None

    if allow_drawing_fallback and not refs and doc.page_count == 1 and source_path:
        log.info("normative fallback: full drawing pipeline %s", filename)
        from belener.extract import extract_pdf_path

        facts = extract_pdf_path(source_path, filename)
        drawing = facts.get("drawing") if facts.get("ok") else None
        if drawing:
            refs = collect_normative_refs(drawing)
            pipeline = "normative_drawing_fallback"

    log.info(
        "normative extract done %.1fs refs=%s pages=%s (%s)",
        time.monotonic() - t0,
        len(refs),
        doc.page_count,
        filename,
    )
    return {
        "ok": True,
        "filename": filename,
        "page_count": doc.page_count,
        "pipeline": pipeline,
        "normative_refs": refs,
        "vision_model": vision_model or None,
        "source_text_chars": len(combined),
        "page_texts": page_texts,
        "drawing": drawing,
    }


def extract_normatives_from_image_path(path: str, filename: str | None = None) -> dict[str, Any]:
    from belener.config import normative_vision_enabled

    if normative_vision_enabled():
        from belener.normative_vision import extract_normatives_image_vision, vision_available

        if vision_available():
            result = extract_normatives_image_vision(path, filename)
            if result.get("ok"):
                return result

    t0 = time.monotonic()
    p = Path(path)
    text = _ocr_image_path(str(p))
    refs = _refs_from_texts(text)
    log.info(
        "normative image OCR done %.1fs refs=%s (%s)",
        time.monotonic() - t0,
        len(refs),
        filename or p.name,
    )
    return {
        "ok": True,
        "filename": filename or p.name,
        "page_count": 1,
        "pipeline": "normative_image_ocr",
        "normative_refs": refs,
        "source_text_chars": len(text),
        "page_texts": [text],
        "drawing": None,
    }


def extract_normatives_pdf_path(path: str, filename: str | None = None) -> dict[str, Any]:
    p = Path(path)
    path_str = str(p.resolve())
    doc = fitz.open(path_str)
    try:
        return extract_normatives_from_document(
            doc,
            filename or p.name,
            source_path=path_str,
        )
    finally:
        doc.close()


def normative_refs_to_markdown(
    refs: list[dict[str, str]],
    *,
    filename: str = "",
    pipeline: str = "",
    include_context: bool = False,
) -> str:
    lines = ["## Нормативные документы (ГОСТ, ОСТ, СТП, ТУ и др.)", ""]
    if filename:
        lines.append(f"**Файл:** {filename}")
    if pipeline:
        mode = "Vision (Ollama)" if "vision" in pipeline else "OCR"
        lines.append(f"**Режим:** {mode} (`{pipeline}`)")
    lines.append("")

    if not refs:
        lines.append(
            "*Нормативные ссылки не найдены. Для эскизов CAD (NanoCAD/AutoCAD) "
            "проверьте таблицы на листе или задайте полный разбор («все gost» с vision).*"
        )
        lines.append("")
        return "\n".join(lines)

    if include_context:
        lines.extend(["| Тип | Обозначение | Контекст на листе |", "| --- | --- | --- |"])
        for n in refs:
            lines.append(
                f"| {n.get('kind') or '—'} | {n.get('ref') or '—'} | {n.get('context') or '—'} |"
            )
    else:
        lines.extend(["| Тип | Обозначение |", "| --- | --- |"])
        for n in refs:
            lines.append(f"| {n.get('kind') or '—'} | {n.get('ref') or '—'} |")

    lines.append("")
    lines.append(f"*Найдено: {len(refs)}*")
    lines.append("")
    return "\n".join(lines)


def normative_result_to_markdown(result: dict[str, Any], *, include_context: bool = False) -> str:
    return normative_refs_to_markdown(
        list(result.get("normative_refs") or []),
        filename=str(result.get("filename") or ""),
        pipeline=str(result.get("pipeline") or ""),
        include_context=include_context,
    )
