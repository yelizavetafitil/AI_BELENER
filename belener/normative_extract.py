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
    r"|(?:стп|stp)\s*[\d(]"
    r"|(?:рд|rd)\s*[\d(]"
    r"|(?:ту|tu)\s*[\d(]"
    r"|(?:со|co|so)\s*[\d(]"
    r"|(?:стб|stb)\s*[\d]"
    r"|снип"
    r"|\bsp\s*\d"
)

_TT_KINDS = ("ТУ", "СТП", "РД", "СО")
_TT_LAYER_HINTS: dict[str, re.Pattern[str]] = {
    "ТУ": re.compile(r"(?i)(?:\(|^|[\s;])(?:ту|tu)\s*[\d(]"),
    "СТП": re.compile(r"(?i)(?:\(|^|[\s;])(?:стп|stp)\s*[\d(]"),
    "РД": re.compile(r"(?i)(?:\(|^|[\s;])(?:рд|rd)\s*[\d(]"),
    "СО": re.compile(r"(?i)(?:\(|^|[\s;])(?:со|co|so)\s*[\d(]"),
}


def _refs_kinds(refs: list[dict[str, str]]) -> set[str]:
    return {str(r.get("kind") or "") for r in refs if str(r.get("kind") or "")}


def _tt_types_hinted_in_sources(*sources: str) -> set[str]:
    combined = "\n".join(str(t or "") for t in sources if str(t or "").strip())
    if not combined.strip():
        return set()
    return {kind for kind, rx in _TT_LAYER_HINTS.items() if rx.search(combined)}


def _ocr_zone_available() -> bool:
    from belener.ocr import tesseract_available
    from belener.paddle_ocr import paddle_ocr_enabled

    return tesseract_available() or paddle_ocr_enabled()


def _pdf_layer_is_normative(text: str) -> bool:
    """PDF-слой с реальными нормативами (не мусор/метаданные без ГОСТ)."""
    blob = (text or "").strip()
    if len(blob) < 40 or not _NORMATIVE_HINT.search(blob):
        return False
    return bool(extract_normative_refs(blob))


def _missing_tt_types(refs: list[dict[str, str]], *sources: str) -> set[str]:
    hinted = _tt_types_hinted_in_sources(*sources)
    if not hinted:
        return set()
    return hinted - (_refs_kinds(refs) & hinted)


def _needs_sheet_notes_pass(
    refs: list[dict[str, str]],
    doc: fitz.Document,
    layer_parts: list[str],
    zone_blobs: list[str],
    zones_by_page: dict[int, Any] | None = None,
) -> bool:
    if _missing_tt_types(refs, *layer_parts, *zone_blobs):
        return True
    if _refs_kinds(refs) & set(_TT_KINDS):
        return False
    for i in range(doc.page_count):
        zones = (zones_by_page or {}).get(i) or _resolve_normative_zones(doc, i)
        rect = zones.rects.get("sheet_notes")
        if rect is None or rect.is_empty:
            continue
        if not (_text_in_rect(doc[i], rect) or "").strip():
            return True
    return False


def _sheet_notes_blob(
    doc: fitz.Document,
    page_index: int,
    zones: Any | None = None,
) -> tuple[str, str]:
    """Текстовый слой колонки ТТ и OCR отдельно."""
    from belener.config import normative_table_dpi
    from belener.ocr import ocr_region

    zones = zones or _resolve_normative_zones(doc, page_index)
    rect = zones.rects.get("sheet_notes")
    if rect is None or rect.is_empty:
        return "", ""
    page = doc[page_index]
    layer = (_text_in_rect(page, rect) or "").strip()
    ocr = ""
    if _ocr_zone_available():
        try:
            ocr = (
                ocr_region(doc, page_index, rect, dpi=normative_table_dpi(), zone="sheet_notes") or ""
            ).strip()
        except Exception:
            ocr = ""
        if ocr and layer and ocr.casefold() == layer.casefold():
            ocr = ""
    return layer, ocr


def _normative_scan_page(doc: fitz.Document, page_index: int, zones: Any) -> str:
    """Быстрый OCR поля схемы (без штампа/таблицы) — вместо page OCR."""
    from belener.normative_scan import ocr_normative_scan

    t0 = time.monotonic()
    text = (ocr_normative_scan(doc, page_index, doc[page_index].rect, zones, force=True) or "").strip()
    if text:
        log.info(
            "normative scan page %s %.1fs chars=%s",
            page_index + 1,
            time.monotonic() - t0,
            len(text),
        )
    return text


def _page_text(doc: fitz.Document, page_index: int) -> str:
    """OCR всей страницы — только если явно включён PDF_NORMATIVE_PAGE_OCR."""
    from belener.config import normative_full_page_ocr_enabled
    from belener.ocr import ocr_page_full

    page = doc[page_index]
    layer = (page.get_text("text") or "").strip()
    if not normative_full_page_ocr_enabled():
        return layer
    has_hint = bool(_NORMATIVE_HINT.search(layer))
    has_refs = bool(extract_normative_refs(layer)) if has_hint else False
    if len(layer) >= _TEXT_LAYER_MIN and has_hint and has_refs:
        return layer
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


def _zone_needs_ocr(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return True
    hints = len(_NORMATIVE_HINT.findall(blob))
    refs = len(extract_normative_refs(blob))
    if hints >= 2 and refs < max(1, int(hints * 0.55)):
        return True
    if _NORMATIVE_HINT.search(blob) and refs == 0:
        return True
    return False


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
    """Табличные зоны для нормативов — без legend/explication/body (лишний OCR)."""
    jobs: list[tuple[str, fitz.Rect]] = []
    seen: set[str] = set()
    for key in ("tables_block", "spec_right", "spec_left"):
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
    zones: Any | None = None,
    ocr_if_no_hint: bool = True,
) -> tuple[list[str], list[str]]:
    """PDF-текст зон (trusted) и OCR (supplement) — отдельно."""
    from belener.config import normative_table_dpi
    from belener.ocr import ocr_region

    page = doc[page_index]
    zones = zones or _resolve_normative_zones(doc, page_index)
    trusted: list[str] = []
    ocr_supp: list[str] = []
    seen: set[tuple[int, int, int, int]] = set()

    def _append_ocr(key: str, rect: fitz.Rect, text: str, *, force: bool = False) -> None:
        if not ocr_if_no_hint or not _ocr_zone_available():
            return
        if not force and not _zone_needs_ocr(text):
            return
        try:
            ocr = (ocr_region(doc, page_index, rect, dpi=normative_table_dpi(), zone=key) or "").strip()
        except Exception:
            ocr = ""
        if ocr and ocr.casefold() not in {(text or "").casefold()}:
            ocr_supp.append(ocr)

    for key, rect in _normative_table_jobs(zones):
        sig = (round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        if sig in seen:
            continue
        seen.add(sig)
        text = _text_in_rect(page, rect)
        if text:
            trusted.append(text)
        _append_ocr(key, rect, text)

    for key in ("sheet_notes",):
        rect = zones.rects.get(key)
        if rect is None or rect.is_empty:
            continue
        sig = (round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        if sig in seen:
            continue
        seen.add(sig)
        text = _text_in_rect(page, rect)
        if text:
            trusted.append(text)
        force = not text or bool(
            _missing_tt_types(_refs_from_texts(text), text, page.get_text("text") or "")
        )
        _append_ocr(key, rect, text, force=force)

    return trusted, ocr_supp


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


def _count_normative_hints(*texts: str) -> int:
    return sum(len(_NORMATIVE_HINT.findall(str(t or ""))) for t in texts)


def _needs_page_ocr(refs: list[dict[str, str]], *source_texts: str) -> bool:
    if not refs:
        return True
    hints = _count_normative_hints(*source_texts)
    if hints >= 4 and len(refs) < max(3, int(hints * 0.55)):
        return True
    return False


def _needs_normative_scan(refs: list[dict[str, str]], *source_texts: str) -> bool:
    if len(refs) >= 12:
        return False
    return _needs_page_ocr(refs, *source_texts)


def extract_normatives_from_document(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    source_path: str | None = None,
    allow_drawing_fallback: bool | None = None,
) -> dict[str, Any]:
    """Нормативы: PDF→скрины зон→OCR (основной путь) или текстовый слой CAD."""
    from belener.config import normative_crops_enabled, normative_drawing_fallback_enabled

    if normative_crops_enabled():
        from belener.normative_crops import extract_normatives_document_crops

        return extract_normatives_document_crops(doc, filename)

    if allow_drawing_fallback is None:
        allow_drawing_fallback = normative_drawing_fallback_enabled()

    return _extract_normatives_legacy(
        doc,
        filename,
        source_path=source_path,
        allow_drawing_fallback=allow_drawing_fallback,
    )


def _extract_normatives_legacy(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    source_path: str | None = None,
    allow_drawing_fallback: bool = False,
) -> dict[str, Any]:
    """Прежний многоступенчатый путь (PDF_NORMATIVE_CROPS=0)."""
    from belener.config import (
        normative_full_page_ocr_enabled,
        normative_vision_enabled,
    )
    from belener.normative_refs import (
        dedupe_normative_year_variants,
        merge_normative_refs,
        merge_page_supplement,
        prune_unconfirmed_variants,
    )

    t0 = time.monotonic()
    layer_parts: list[str] = []
    zone_trusted: list[str] = []
    ocr_supplements: list[str] = []
    zones_by_page: dict[int, Any] = {}
    if doc.page_count <= 3:
        for i in range(doc.page_count):
            zones_by_page[i] = _resolve_normative_zones(doc, i)
    for i in range(doc.page_count):
        layer_parts.append((doc[i].get_text("text") or "").strip())
        if i in zones_by_page:
            zt, zo = _zone_normative_blobs(doc, i, zones=zones_by_page[i], ocr_if_no_hint=True)
            zone_trusted.extend(zt)
            ocr_supplements.extend(zo)

    pdf_sources: list[str] = [t for t in zone_trusted if str(t or "").strip()]
    zone_refs = _refs_from_texts(*pdf_sources) if pdf_sources else []
    if len(zone_refs) < 5:
        for part in layer_parts:
            if part and _pdf_layer_is_normative(part) and part not in pdf_sources:
                pdf_sources.append(part)
    trusted_sources: list[str] = list(pdf_sources)
    refs = _refs_from_texts(*trusted_sources)
    layer_only = [p for p in layer_parts if _pdf_layer_is_normative(p)]
    if layer_only and layer_only != pdf_sources:
        refs = merge_page_supplement(refs, _refs_from_texts(*layer_only), *trusted_sources)
        for part in layer_only:
            if part not in pdf_sources:
                pdf_sources.append(part)
                trusted_sources.append(part)
    pipeline = "normative_layer+zones" if zone_trusted else "normative_layer"
    page_texts: list[str] = []
    ocr_trusted: list[str] = [t for t in ocr_supplements if str(t or "").strip()]

    if ocr_supplements and len(zone_refs) < 4:
        refs = merge_page_supplement(refs, _refs_from_texts(*ocr_supplements), *trusted_sources, *ocr_trusted)
        pipeline = f"{pipeline}+zone_ocr"
    elif ocr_supplements:
        log.info("normative skip zone OCR merge: pdf_layer refs=%s", len(zone_refs))

    missing_tt = _missing_tt_types(refs, *trusted_sources, *layer_parts, *ocr_trusted)
    if (
        _needs_sheet_notes_pass(refs, doc, layer_parts, zone_trusted, zones_by_page)
        and doc.page_count <= 3
    ):
        for i in range(doc.page_count):
            tt_layer, tt_ocr = _sheet_notes_blob(doc, i, zones_by_page.get(i))
            if tt_layer:
                trusted_sources.append(tt_layer)
                pdf_sources.append(tt_layer)
                refs = merge_normative_refs(refs, _refs_from_texts(tt_layer))
            if tt_ocr and not tt_layer:
                ocr_trusted.append(tt_ocr)
                refs = merge_page_supplement(
                    refs, _refs_from_texts(tt_ocr), *trusted_sources, *ocr_trusted
                )
        if missing_tt:
            pipeline = f"{pipeline}+tt"
            log.info("normative TT supplement kinds=%s", sorted(missing_tt))

    scan_sources = (*trusted_sources, *ocr_trusted, *layer_parts)
    if _needs_normative_scan(refs, *scan_sources) and len(zone_refs) < 8:
        for i in range(doc.page_count):
            zones = zones_by_page.get(i) or _resolve_normative_zones(doc, i)
            if normative_full_page_ocr_enabled():
                page_texts.append(_page_text(doc, i))
            else:
                scan = _normative_scan_page(doc, i, zones)
                if scan:
                    page_texts.append(scan)
                    ocr_trusted.append(scan)
        page_refs = _refs_from_texts(*page_texts)
        refs = merge_page_supplement(refs, page_refs, *trusted_sources, *ocr_trusted)
        if page_refs:
            pipeline = f"{pipeline}+scan" if not normative_full_page_ocr_enabled() else f"{pipeline}+page"
    elif _needs_normative_scan(refs, *scan_sources):
        log.info("normative skip scan: pdf_layer refs=%s", len(zone_refs))

    vision_model = ""
    if normative_vision_enabled() and not refs:
        from belener.normative_vision import extract_normatives_document_vision, vision_available

        if vision_available():
            vresult = extract_normatives_document_vision(doc, filename)
            if vresult.get("ok") and vresult.get("normative_refs"):
                refs = merge_normative_refs(list(vresult["normative_refs"]), refs)
                vision_model = str(vresult.get("vision_model") or "")
                pipeline = str(vresult.get("pipeline") or "normative_vision")
            elif vresult.get("ok") is False:
                log.warning("normative vision unavailable: %s", vresult.get("error"))

    refs = dedupe_normative_year_variants(refs, *pdf_sources, *ocr_trusted)
    refs = prune_unconfirmed_variants(refs, *pdf_sources, *ocr_trusted)

    drawing: dict[str, Any] | None = None
    if allow_drawing_fallback and not refs and doc.page_count == 1 and source_path:
        log.info("normative fallback: full drawing pipeline %s", filename)
        from belener.extract import extract_pdf_path

        facts = extract_pdf_path(source_path, filename)
        drawing = facts.get("drawing") if facts.get("ok") else None
        if drawing:
            refs = collect_normative_refs(drawing)
            pipeline = "normative_drawing_fallback"

    combined = "\n\n".join(t for t in page_texts if t) or "\n\n".join(layer_parts)
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
        "page_texts": page_texts or layer_parts,
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
        if "crop" in pipeline or "tile" in pipeline:
            mode = "Тайлы листа (OCR)"
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
