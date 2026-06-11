"""PDF-листы САПР (широкий формат): зоны → OCR + vision по зонам → факты."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from belener.config import (
    accuracy_mode,
    body_min_chars,
    blueprint_extract_enabled,
    stamp_universal_enabled,
    cv_tables_always,
    cv_tables_dpi,
    cv_tables_enabled,
    discover_zones_enabled,
    discover_zones_fast,
    drawing_aspect_min,
    edocr_enabled,
    img2table_enabled,
    extract_text_layer_fast_min,
    layout_ocr_enabled,
    layout_vision_enabled,
    sheet_text_enabled,
    sheet_text_vision_always_scan,
    stamp_block_dpi,
    table_dpi,
    vision_mode,
    vision_postprocess,
    vision_scan_first,
    vision_zones_enabled,
    vision_zones_model,
)
from belener.sheet_text import (
    build_sheet_notes_payload,
    extract_text_layer_pages,
    needs_sheet_text_vision,
    text_layer_non_table_text,
)
from belener.scanned import is_engineering_scan_document, is_scanned_document
from belener.discover import discover_sheet_zones
from belener.ocr import tesseract_available
from belener.parse import (
    discover_table_sections,
    merge_explication_rows,
    merge_legend_rows,
    merge_table_sections,
    finalize_table_sections,
    parse_stamp,
    tables_to_legacy,
    _infer_table_kind,
)
from belener.layout import detect_layout_blocks
from belener.quality import analyze_pdf_quality
from belener.sheet_read import (
    ocr_layout_blocks,
    ocr_sheet,
    ocr_sheet_by_zones,
    parse_table_ocr,
    _best_table_rect,
    _parse_table_zone_text,
    _table_zone_jobs,
)
from belener.stamp_read import finalize_stamp, merge_stamp_sources, read_stamp_frame, enrich_stamp_from_table_text
from belener.vision_zones import extract_layout_blocks_vision, extract_zones_vision, vision_postprocess_sheet
from belener.zones import build_zones

log = logging.getLogger("belener.drawing")

_COMPASS = {"С", "З", "В", "Ю"}


def _signature_names(stamp: dict) -> int:
    from belener.parse import _is_bad_signature_name

    return sum(
        1
        for s in stamp.get("signatures") or []
        if str(s.get("name") or "").strip() not in ("", "—")
        and not _is_bad_signature_name(str(s.get("name")))
    )


def _legend_needs_vision(rows: list) -> bool:
    """Склеенный OCR легенды — отдельный проход vision."""
    if not rows:
        return True
    for r in rows:
        note = str(r.get("note") or "")
        if len(note) > 85 or note.count("|") >= 2:
            return True
    return len(rows) < 2


def _stamp_needs_vision(stamp: dict) -> bool:
    """Штамп с OCR часто даёт «Разроб» / обрезанные фамилии — нужен vision."""
    sigs = [s for s in stamp.get("signatures") or [] if isinstance(s, dict)]
    if not sigs:
        return True
    if _signature_names(stamp) < 2:
        return True
    names = [str(s.get("name") or "").strip() for s in sigs if str(s.get("name") or "").strip() not in ("", "—")]
    if len(names) >= 2 and len(set(n.casefold() for n in names)) == 1:
        return True
    for s in sigs:
        role = str(s.get("role") or "").casefold()
        name = str(s.get("name") or "").strip()
        if "разраб" in role and (len(name) < 6 or name.casefold().startswith("раз")):
            return True
    return False


def _tables_weak(expl: list, legend: list, sections: list) -> bool:
    rows = len(expl) + len(legend)
    if rows >= 2:
        return False
    if sections and any((s.get("rows") or []) for s in sections):
        return False
    return rows < 1


def _should_run_cv_tables(
    expl: list,
    legend: list,
    sections: list,
    table_text: str | None,
) -> bool:
    """Запуск OpenCV-таблиц, если зонный OCR дал мало структуры."""
    if _tables_weak(expl, legend, sections):
        return True
    if len((table_text or "").strip()) < 100:
        return True
    row_count = sum(len(s.get("rows") or []) for s in sections)
    if sections and row_count < 4:
        return True
    return False


def _body_weak(body_text: str) -> bool:
    return len((body_text or "").strip()) < body_min_chars()


def _vision_plan(
    stamp: dict,
    expl: list,
    legend: list,
    sections: list,
    *,
    scanned: bool = False,
    body_text: str = "",
    tess_ok: bool = True,
) -> tuple[bool, bool, bool, bool]:
    if not vision_zones_enabled():
        return False, False, False, False
    mode = vision_mode()
    need_stamp = _stamp_incomplete(stamp) or _signature_names(stamp) < 2
    from belener.config import vision_tables_enabled

    need_tables = _tables_weak(expl, legend, sections) and vision_tables_enabled()
    need_body = _body_weak(body_text)
    if mode == "off":
        return False, False, False, False
    if mode == "always":
        return True, True, True, True
    if scanned and vision_scan_first() and not tess_ok:
        return True, True, True, True
    if not need_stamp and not need_tables and not need_body:
        return False, False, False, False
    return True, need_stamp, need_tables, need_body


def _vision_available() -> bool:
    try:
        import ollama

        from belener.config import ollama_host
        from belener.vision_zones import pick_vision_model

        client = ollama.Client(host=ollama_host(), timeout=8)
        return pick_vision_model(client) is not None
    except Exception:
        return False


def _drawing_heuristic(doc: fitz.Document) -> bool:
    if doc.page_count <= 0:
        return False
    # This application is dedicated to engineering drawings. Do not reject A4/A3
    # portrait sheets, title sheets, or CAD exports solely by aspect ratio.
    if __import__("os").environ.get("PDF_REQUIRE_DRAWING_HEURISTIC", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    if is_engineering_scan_document(doc):
        return True
    page = doc[0]
    text = page.get_text("text").strip()
    r = page.rect
    aspect = r.width / max(r.height, 1.0)
    wide = aspect >= 2.0
    semi = aspect >= drawing_aspect_min()
    if not wide and not semi:
        return False
    compass = sum(1 for c in text if c in _COMPASS)
    weak_text = len(text) < 120 or (compass >= 2 and len(text) < 400)
    if weak_text and (wide or semi):
        return True
    total = sum(len(p.get_text().strip()) for p in doc)
    avg = total / max(doc.page_count, 1)
    return avg < 50 and semi


def is_drawing_document(doc: fitz.Document) -> bool:
    force = __import__("os").environ.get("PDF_FORCE_DRAWING", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if force:
        return True
    return _drawing_heuristic(doc)


def is_sapr_drawing_pdf(path: str) -> bool:
    doc = fitz.open(path)
    try:
        return is_drawing_document(doc)
    finally:
        doc.close()


def _doc_use_text_layer(doc: fitz.Document) -> bool:
    total = sum(len(doc[i].get_text().strip()) for i in range(doc.page_count))
    return total / max(doc.page_count, 1) >= extract_text_layer_fast_min()


def _text_in_rect(page: fitz.Page, rect: fitz.Rect | None) -> str:
    if rect is None:
        return ""
    try:
        return (page.get_text("text", clip=rect) or "").strip()
    except Exception:
        return ""


def _ocr_zone_lengths(ocr: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in ocr.items():
        if isinstance(value, int):
            out[key] = value
        else:
            out[key] = len(str(value or ""))
    return out


def _stamp_incomplete(stamp: dict[str, Any]) -> bool:
    from belener.parse import _looks_like_stamp_section_title, _normalize_stamp_title

    kv = {x.get("field"): x.get("value") for x in stamp.get("kv") or [] if x.get("field")}
    cipher = str(kv.get("Обозначение / шифр") or "").strip()
    org = str(kv.get("Организация") or "").strip()
    scale = str(kv.get("Масштаб") or "").strip()
    if cipher and org and scale and _signature_names(stamp) >= 1:
        return False
    if _signature_names(stamp) < 2:
        return True
    core = ("Организация", "Масштаб", "Лист", "Стадия (обозначение)")
    missing = sum(1 for k in core if not str(kv.get(k) or "").strip())
    if missing >= 3:
        return True
    good_titles = [
        t for t in (stamp.get("titles") or [])
        if _looks_like_stamp_section_title(_normalize_stamp_title(str(t)))
    ]
    return len(good_titles) < 1


def analyze_pdf_document(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    pdf_path: str | None = None,
) -> dict[str, Any]:
    scanned = is_scanned_document(doc)
    vision_ok = _vision_available()
    tess_ok = tesseract_available()
    vision_only = (
        scanned
        and vision_scan_first()
        and layout_vision_enabled()
        and vision_ok
        and vision_zones_enabled()
    )

    if not tess_ok and not vision_ok:
        return {
            "ok": False,
            "error": "Нужен Tesseract (tesseract-ocr + rus) или vision-модель Ollama (qwen2.5vl:7b).",
            "filename": filename,
        }
    if not tess_ok and not vision_only:
        return {
            "ok": False,
            "error": "Tesseract не установлен. Для сканов включите PDF_SCAN_VISION_FIRST=1 и qwen2.5vl в Ollama.",
            "filename": filename,
        }

    if doc.page_count <= 0:
        return {"ok": False, "error": "PDF без страниц", "filename": filename}

    page = doc[0]
    if discover_zones_enabled() and tess_ok:
        zones = discover_sheet_zones(
            doc, 0, page.rect, fast=discover_zones_fast() or not accuracy_mode()
        )
    elif discover_zones_fast() and tess_ok:
        zones = discover_sheet_zones(doc, 0, page.rect, fast=True)
    else:
        zones = build_zones(page.rect)
    from belener.zone_refine import refine_sheet_zones

    zones = refine_sheet_zones(doc, zones, 0, classify_with_ocr=tess_ok)
    from belener.config import yolo_zones_enabled
    from belener.yolo_zones import apply_yolo_zones

    if yolo_zones_enabled():
        zones = apply_yolo_zones(doc, 0, page.rect, zones)
    use_layout = scanned and (layout_ocr_enabled() or layout_vision_enabled()) and not accuracy_mode()
    layout_blocks = detect_layout_blocks(doc, 0) if use_layout else []
    pipeline = "belener_vision_scan" if vision_only else "belener_ocr"
    vision_model = ""
    ocr: dict[str, str] = {}
    sheet_text_ocr = ""
    full_text_pages = [] if scanned else extract_text_layer_pages(doc)
    quality = {} if scanned else analyze_pdf_quality(doc)
    s_dpi = stamp_block_dpi()
    t_dpi = table_dpi()
    t0 = time.monotonic()
    cad_export = not scanned and _doc_use_text_layer(doc)
    sheet_kind = "cad_export" if cad_export else ("scan" if scanned else "hybrid")
    log.info(
        "sheet start %s kind=%s scanned=%s vision_only=%s stamp_dpi=%s table_dpi=%s",
        filename,
        sheet_kind,
        scanned,
        vision_only,
        s_dpi,
        t_dpi,
    )

    from belener.zones import stamp_ocr_rect

    table_zone, table_rect = _best_table_rect(zones)
    stamp_rect = stamp_ocr_rect(zones, page.rect)
    stamp_from_universal = False
    stamp: dict[str, Any] = {}
    ocr_expl: list[dict] = []
    ocr_legend: list[dict] = []
    ocr_sections: list[dict[str, Any]] = []
    zoned_sheet_notes: dict[str, Any] | None = None
    zone_ocr_texts: dict[str, str] = {}

    if vision_only:
        if not stamp_from_universal:
            stamp = parse_stamp("")
        table_text = ""
        table_zone = table_zone or "right_column"
    elif _doc_use_text_layer(doc):
        log.info("text layer fast path %s", filename)
        pipeline = "belener_text_layer"
        table_text_parts: list[str] = []
        for key, rect in _table_zone_jobs(zones):
            zt = _text_in_rect(page, rect)
            if not zt:
                continue
            table_text_parts.append(zt)
            _, _, secs = _parse_table_zone_text(zt, key)
            ocr_sections.extend(secs)
        table_text = "\n\n".join(table_text_parts) or _text_in_rect(page, table_rect)
        stamp_raw = _text_in_rect(page, stamp_rect)
        if sheet_text_enabled():
            sheet_text_ocr, sheet_by_zone = text_layer_non_table_text(page, zones)
            if sheet_text_ocr:
                ocr.update({f"text_layer_{k}": len(v) for k, v in sheet_by_zone.items()})
            log.info("sheet text layer %s chars (%s)", len(sheet_text_ocr), filename)
        if not stamp_from_universal:
            if len(stamp_raw) > 60:
                stamp = finalize_stamp(parse_stamp(stamp_raw))
            else:
                stamp = (
                    read_stamp_frame(doc, stamp_rect, dpi=s_dpi, page_index=0)
                    if stamp_rect
                    else parse_stamp("")
                )
        if not table_text and table_rect is not None:
            from belener.ocr import ocr_region

            table_text = ocr_region(doc, 0, table_rect, dpi=t_dpi, zone=table_zone or "right_column")
        for sec in ocr_sections:
            kind = str(sec.get("kind") or "")
            rows = sec.get("rows") or []
            if kind == "explication":
                ocr_expl.extend(rows)
            elif kind == "legend":
                ocr_legend.extend(rows)
    else:
        zoned = ocr_sheet_by_zones(
            doc,
            zones,
            stamp_dpi=s_dpi,
            table_dpi=t_dpi,
            page_index=0,
        )
        if not stamp_from_universal:
            stamp = zoned["stamp"]
        table_text = zoned["table_text"]
        table_zone = zoned["table_key"]
        sheet_text_ocr = zoned["sheet_notes_text"]
        ocr_expl = list(zoned["expl_rows"])
        ocr_legend = list(zoned["legend_rows"])
        ocr_sections = list(zoned["table_sections"])
        zoned_sheet_notes = zoned.get("sheet_notes")
        ocr.update({str(k): int(v) for k, v in (zoned.get("ocr_zones") or {}).items()})
        zone_ocr_texts = dict(zoned.get("zone_texts") or {})
        if not stamp_from_universal:
            stamp = enrich_stamp_from_table_text(
                stamp,
                table_text,
                *(zone_ocr_texts.get(k) or "" for k in ("spec_left", "spec_right")),
            )
        pipeline = "belener_zoned_ocr"
        if sheet_text_ocr:
            ocr["sheet_notes"] = sheet_text_ocr
        log.info(
            "zoned OCR table=%s legend=%s notes=%s (%s)",
            len(table_text or ""),
            len(zoned.get("legend_text") or ""),
            len(sheet_text_ocr or ""),
            filename,
        )

    if scanned and tess_ok and not vision_only:
        from belener.config import normative_scan_enabled
        from belener.normative_scan import ocr_normative_scan

        if normative_scan_enabled():
            ns = ocr_normative_scan(doc, 0, page.rect, zones)
            if ns:
                zone_ocr_texts["normative_scan"] = ns
                ocr["normative_scan"] = len(ns)

    if scanned and sheet_text_enabled():
        from belener.config import body_ocr_enabled
        from belener.sheet_text import needs_sheet_text_vision, ocr_non_table_text

        if body_ocr_enabled() or needs_sheet_text_vision(sheet_text_ocr):
            tb = time.monotonic()
            from belener.body_filter import body_text_usable, filter_body_text

            body_combined, body_by = ocr_non_table_text(doc, page.rect, zones, page_index=0)
            body_combined = filter_body_text(body_combined)
            log.info(
                "OCR body/notes %.1fs chars=%s usable=%s (%s)",
                time.monotonic() - tb,
                len(body_combined or ""),
                body_text_usable(body_combined),
                filename,
            )
            if body_combined and body_text_usable(body_combined):
                sheet_text_ocr = (
                    f"{sheet_text_ocr}\n\n{body_combined}".strip() if sheet_text_ocr else body_combined
                )
                ocr.update({f"body_{k}": len(v) for k, v in body_by.items()})

    log.info("zones done in %.1fs (%s)", time.monotonic() - t0, filename)
    table_parse_text = table_text
    if not vision_only and table_parse_text and not ocr_sections:
        ocr_expl, ocr_legend, ocr_sections = parse_table_ocr(table_parse_text)
    if table_text:
        ocr[table_zone or "right_column"] = table_text

    expl: list[dict] = list(ocr_expl)
    legend: list[dict] = list(ocr_legend)
    tables: list[dict[str, Any]] = list(ocr_sections)
    expl_title = ""
    leg_title = ""

    tables_weak_early = _tables_weak(expl, legend, tables)

    if scanned and blueprint_extract_enabled() and tess_ok and tables_weak_early:
        from belener.blueprint_extract import blueprint_available, extract_blueprint_page

        if blueprint_available():
            tb = time.monotonic()
            log.info("blueprint extract %s", filename)
            try:
                bp = extract_blueprint_page(doc, 0, zones=zones, dpi=cv_tables_dpi())
                log.info(
                    "blueprint done in %.1fs tables=%s (%s)",
                    time.monotonic() - tb,
                    len(bp.get("tables") or []),
                    filename,
                )
                if bp.get("tables"):
                    if _tables_weak(expl, legend, tables):
                        tables = merge_table_sections(list(bp["tables"]), tables)
                    else:
                        tables = merge_table_sections(tables, list(bp["tables"]))
                    if bp.get("table_text"):
                        table_parse_text = (
                            f"{bp['table_text']}\n\n{table_parse_text}".strip()
                            if table_parse_text
                            else str(bp["table_text"])
                        )
                    ocr["blueprint"] = len(str(bp.get("table_text") or ""))
                    pipeline = f"{pipeline}+blueprint"
            except Exception:
                log.exception("blueprint extract failed %s", filename)

    from belener.config import img2table_when_weak

    run_img2 = img2table_enabled() and pdf_path and tess_ok
    if run_img2 and img2table_when_weak():
        run_img2 = tables_weak_early or _stamp_incomplete(stamp)
    if scanned and run_img2:
        from belener.img2table_extract import extract_img2table_pdf, img2table_available

        if img2table_available():
            ti = time.monotonic()
            log.info("img2table %s", filename)
            try:
                i2 = extract_img2table_pdf(pdf_path, page_index=0)
                log.info(
                    "img2table done in %.1fs tables=%s",
                    time.monotonic() - ti,
                    len(i2.get("tables") or []),
                )
                if i2.get("tables"):
                    if _tables_weak(expl, legend, tables):
                        tables = merge_table_sections(list(i2["tables"]), tables)
                    else:
                        tables = merge_table_sections(tables, list(i2["tables"]))
                    if i2.get("table_text"):
                        table_parse_text = (
                            f"{i2['table_text']}\n\n{table_parse_text}".strip()
                            if table_parse_text
                            else str(i2["table_text"])
                        )
                    ocr["img2table"] = len(str(i2.get("table_text") or ""))
                    pipeline = f"{pipeline}+img2table"
            except Exception:
                log.exception("img2table failed %s", filename)

    if scanned and cv_tables_enabled() and tess_ok:
        from belener.cv_tables import cv_available, extract_cv_tables

        run_cv = cv_tables_always() or _should_run_cv_tables(expl, legend, tables, table_text)
        if cv_available() and run_cv:
            tcv = time.monotonic()
            log.info("CV tables %s (always=%s)", filename, cv_tables_always())
            try:
                stamp_r = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block")
                cv = extract_cv_tables(doc, 0, dpi=cv_tables_dpi(), stamp_rect=stamp_r)
                log.info(
                    "CV tables done in %.1fs sections=%s (%s)",
                    time.monotonic() - tcv,
                    len(cv.get("tables") or []),
                    filename,
                )
                if cv.get("tables"):
                    tables = merge_table_sections(tables, list(cv["tables"]))
                    if cv.get("table_text"):
                        table_parse_text = (
                            f"{table_parse_text}\n\n{cv['table_text']}".strip()
                            if table_parse_text
                            else str(cv["table_text"])
                        )
                    ocr["cv_tables"] = len(str(cv.get("table_text") or ""))
                    if pipeline == "belener_zoned_ocr":
                        pipeline = "belener_zoned_cv"
                    else:
                        pipeline = f"{pipeline}+cv"
            except Exception:
                log.exception("CV tables failed %s", filename)

    if scanned and edocr_enabled() and pdf_path and tess_ok:
        if _tables_weak(expl, legend, tables) or _stamp_incomplete(stamp):
            te = time.monotonic()
            log.info("eDOCr %s", filename)
            try:
                from belener.edocr_client import extract_edocr_pdf

                eo = extract_edocr_pdf(pdf_path)
                log.info("eDOCr done in %.1fs ok=%s", time.monotonic() - te, eo.get("ok"))
                if eo.get("table_text"):
                    table_parse_text = (
                        f"{table_parse_text}\n\n{eo['table_text']}".strip()
                        if table_parse_text
                        else str(eo["table_text"])
                    )
                    ocr["edocr"] = len(str(eo.get("table_text") or ""))
                if eo.get("tables"):
                    tables = merge_table_sections(tables, list(eo["tables"]))
                elif eo.get("table_text"):
                    tables = merge_table_sections(
                        tables, discover_table_sections(str(eo["table_text"]))
                    )
                if eo.get("table_text") and _stamp_needs_vision(stamp):
                    eo_stamp = parse_stamp(str(eo["table_text"]))
                    stamp = merge_stamp_sources(stamp, eo_stamp, table_ocr_text="")
                if eo.get("ok"):
                    pipeline = f"{pipeline}+edocr"
            except Exception:
                log.exception("eDOCr failed %s", filename)

    vision_model = ""
    vision_stamp_data: dict[str, Any] | None = None
    vision_sheet_notes: dict[str, Any] | None = None
    vision_text_blocks: list[dict[str, str]] = []
    ocr_text_blocks: list[dict[str, str]] = []
    need_vision = False

    if scanned and layout_ocr_enabled() and tess_ok and layout_blocks:
        tl = time.monotonic()
        log.info("layout OCR %s blocks=%s", filename, len(layout_blocks))
        try:
            lo = ocr_layout_blocks(
                doc,
                layout_blocks,
                stamp_dpi=s_dpi,
                table_dpi=t_dpi,
                page_index=0,
            )
            log.info("layout OCR done in %.1fs (%s)", time.monotonic() - tl, filename)
            if lo.get("stamp") and _stamp_incomplete(stamp):
                stamp = lo["stamp"]
            if lo.get("tables"):
                tables = merge_table_sections(tables, list(lo["tables"]))
            if lo.get("table_text") and not table_parse_text:
                table_parse_text = str(lo["table_text"])
            if lo.get("text_blocks"):
                ocr_text_blocks = list(lo["text_blocks"])
            if lo.get("ocr_zones"):
                ocr.update({str(k): int(v) for k, v in (lo.get("ocr_zones") or {}).items()})
            pipeline = "belener_layout_ocr"
        except Exception:
            log.exception("layout OCR failed %s", filename)

    if scanned and layout_vision_enabled() and vision_zones_enabled() and vision_ok and layout_blocks:
        tv = time.monotonic()
        log.info("layout vision %s blocks=%s model=%s", filename, len(layout_blocks), vision_zones_model())
        try:
            lv = extract_layout_blocks_vision(doc, layout_blocks)
            vision_model = str(lv.get("vision_model") or "")
            log.info("layout vision done in %.1fs ok=%s (%s)", time.monotonic() - tv, lv.get("ok"), filename)
            if lv.get("ok"):
                pipeline = "belener_layout_vision"
                if vision_model:
                    pipeline += f"({vision_model.split(':')[0]})"
                if lv.get("stamp"):
                    vision_stamp_data = lv["stamp"]
                if lv.get("tables"):
                    tables = merge_table_sections(tables, list(lv["tables"]))
                if lv.get("sheet_notes"):
                    vision_sheet_notes = lv["sheet_notes"]
                if lv.get("text_blocks"):
                    vision_text_blocks = list(lv["text_blocks"])
        except Exception:
            log.exception("layout vision failed %s", filename)

    use_post = scanned and vision_postprocess() and vision_zones_enabled() and vision_ok
    if use_post:
        from belener.report_clean import extract_quality_poor

        poor = extract_quality_poor(stamp, tables, table_text or table_parse_text)
        need_vision, vision_stamp, vision_tables, _ = _vision_plan(
            stamp,
            expl,
            legend,
            tables,
            scanned=scanned,
            body_text=sheet_text_ocr,
            tess_ok=tess_ok,
        )
        from belener.config import report_faithful, vision_stamp_enabled, vision_tables_enabled

        if accuracy_mode() and poor:
            need_vision = True
            vision_stamp = vision_stamp_enabled()
            vision_tables = vision_tables_enabled()
            log.info(
                "vision forced (poor OCR quality) %s stamp=%s tables=%s",
                filename,
                vision_stamp,
                vision_tables,
            )
        if report_faithful():
            vision_stamp = False
        need_sheet_vision = False
        if (
            sheet_text_enabled()
            and scanned
            and zones.rects.get("sheet_notes")
            and sheet_text_ocr
        ):
            from belener.notes_filter import is_technical_requirements_notes

            probe = build_sheet_notes_payload(sheet_text_ocr, None)
            if not is_technical_requirements_notes(probe):
                need_sheet_vision = True
        elif _stamp_needs_vision(stamp) and vision_stamp_enabled():
            need_vision = True
            vision_stamp = True
        if need_vision or need_sheet_vision:
            tv = time.monotonic()
            log.info(
                "vision POST %s stamp=%s tables=%s sheet=%s model=%s",
                filename,
                vision_stamp,
                vision_tables,
                need_sheet_vision,
                vision_zones_model(),
            )
            vz = vision_postprocess_sheet(
                doc,
                zones,
                include_stamp=vision_stamp,
                include_tables=vision_tables,
                include_sheet_text=need_sheet_vision,
            )
            vision_model = str(vz.get("vision_model") or "")
            log.info("vision POST done in %.1fs ok=%s (%s)", time.monotonic() - tv, vz.get("ok"), filename)
            if vz.get("ok"):
                pipeline = "belener_hybrid_post"
                if vision_model:
                    pipeline += f"({vision_model.split(':')[0]})"
                if vision_stamp and vz.get("stamp"):
                    vision_stamp_data = vz["stamp"]
                if vz.get("tables"):
                    from belener.config import vision_tables_enabled

                    if vision_tables_enabled():
                        vz_tables = list(vz["tables"])
                        for t in vz_tables:
                            t = dict(t)
                            t["source"] = "vision"
                            tables = merge_table_sections(tables, [t])
                    else:
                        log.info("vision tables skipped (PDF_VISION_TABLES=0) %s", filename)
                if vz.get("explication"):
                    expl = merge_explication_rows(expl, vz["explication"])
                if vz.get("legend"):
                    legend = merge_legend_rows(legend, vz["legend"])
                from belener.config import vision_tables_enabled

                if (
                    vision_tables_enabled()
                    and _legend_needs_vision(legend)
                    and zones.rects.get("legend")
                    and vision_ok
                ):
                    try:
                        from belener.vision_zones import _vision_legend, pick_vision_model
                        import ollama
                        from belener.config import ollama_host, vision_timeout_sec

                        _vc = ollama.Client(host=ollama_host(), timeout=vision_timeout_sec())
                        _vm = pick_vision_model(_vc) or vision_model
                        if _vm:
                            leg_rows = _vision_legend(
                                doc, zones.rects["legend"], table_dpi(), _vc, _vm
                            )
                            if leg_rows:
                                legend = merge_legend_rows(legend, leg_rows)
                                log.info("vision legend rows=%s (%s)", len(leg_rows), filename)
                    except Exception:
                        log.exception("vision legend failed %s", filename)
                if vz.get("explication_title"):
                    expl_title = str(vz["explication_title"])
                if vz.get("legend_title"):
                    leg_title = str(vz["legend_title"])
                if vz.get("sheet_notes"):
                    vision_sheet_notes = vz["sheet_notes"]
    elif scanned and layout_vision_enabled() and vision_zones_enabled() and vision_ok:
        need_vision, vision_stamp, vision_tables, _ = _vision_plan(
            stamp, expl, legend, tables, scanned=scanned, body_text=sheet_text_ocr, tess_ok=tess_ok
        )
        if need_vision:
            vz = extract_zones_vision(
                doc,
                zones,
                include_stamp=vision_stamp,
                include_tables=vision_tables,
                include_body=False,
            )
            vision_model = str(vz.get("vision_model") or "")
            if vz.get("ok"):
                pipeline = "belener_hybrid"
                if vision_model:
                    pipeline += f"({vision_model.split(':')[0]})"
                if vision_stamp and vz.get("stamp"):
                    vision_stamp_data = vz["stamp"]
                if vz.get("tables"):
                    from belener.config import vision_tables_enabled

                    if vision_tables_enabled():
                        tables = merge_table_sections(tables, list(vz["tables"]))
                if vz.get("explication"):
                    expl = merge_explication_rows(expl, vz["explication"])
                if vz.get("legend"):
                    legend = merge_legend_rows(legend, vz["legend"])

    if vision_stamp_data and vision_stamp_data.get("source") == "stamp_universal":
        stamp = vision_stamp_data
        stamp_from_universal = True
        log.info("stamp from universal vision post (%s)", filename)
    elif not stamp_from_universal:
        stamp = merge_stamp_sources(stamp, vision_stamp_data, table_ocr_text=table_text or "")

    from belener.report_clean import prune_garbage_tables

    discovered = discover_table_sections(table_parse_text) if table_parse_text else []
    combined = merge_table_sections(tables, discovered)
    spec_blob_parts = [
        zone_ocr_texts[k]
        for k in sorted(zone_ocr_texts)
        if k.startswith("spec_") and zone_ocr_texts.get(k)
    ]
    ocr_blob = "\n".join(
        x
        for x in (
            *spec_blob_parts,
            table_text or "",
            table_parse_text or "",
            sheet_text_ocr or "",
            *zone_ocr_texts.values(),
        )
        if x.strip()
    )
    from belener.grounding import filter_tables_by_ocr_grounding

    combined = filter_tables_by_ocr_grounding(combined, ocr_blob)
    combined = prune_garbage_tables(combined)
    kinds_present = {_infer_table_kind(t) for t in combined if (t.get("rows") or [])}
    extra_sections: list[dict[str, Any]] = []
    if expl and "explication" not in kinds_present:
        title = expl_title
        table_number = ""
        for d in discovered:
            if d.get("kind") == "explication":
                title = str(d.get("title") or "").strip() or title
                table_number = str(d.get("table_number") or "").strip() or table_number
                break
        extra_sections.append(
            {
                "title": title,
                "kind": "explication",
                "rows": expl,
                "table_number": table_number,
            }
        )
    if legend and "legend" not in kinds_present:
        title = leg_title
        table_number = ""
        for d in discovered:
            if d.get("kind") == "legend":
                title = str(d.get("title") or "").strip() or title
                table_number = str(d.get("table_number") or "").strip() or table_number
                break
        extra_sections.append(
            {
                "title": title,
                "kind": "legend",
                "rows": legend,
                "table_number": table_number,
            }
        )
    if extra_sections:
        combined = merge_table_sections(combined, extra_sections)

    try:
        tables = finalize_table_sections(combined, table_text)
    except Exception:
        log.exception("finalize_table_sections failed %s", filename)
        tables = combined
    expl_title, expl, leg_title, legend = tables_to_legacy(tables)
    if (
        _stamp_incomplete(stamp)
        and stamp_rect
        and not stamp_rect.is_empty
        and tess_ok
        and str(stamp.get("ocr_source") or "") != "stamp_grid"
    ):
        try:
            ocr_stamp = read_stamp_frame(doc, stamp_rect, dpi=s_dpi, page_index=0)
            if ocr_stamp:
                stamp = merge_stamp_sources(stamp, ocr_stamp, table_ocr_text=table_text or "")
                log.info("stamp OCR fallback (%s)", filename)
        except Exception:
            log.exception("stamp OCR fallback failed %s", filename)

    from belener.config import vision_mode as _vision_mode_cfg

    if (
        _stamp_incomplete(stamp)
        and stamp_universal_enabled()
        and vision_ok
        and _vision_mode_cfg() != "off"
        and stamp_rect
        and not stamp_rect.is_empty
        and not stamp_from_universal
    ):
        try:
            from belener.stamp_universal import extract_stamp_universal

            u_stamp = extract_stamp_universal(doc, stamp_rect, page_index=0)
            if u_stamp:
                stamp = u_stamp
                stamp_from_universal = True
                log.info("stamp universal retry (%s)", filename)
        except Exception:
            log.exception("stamp universal retry failed %s", filename)

    if not stamp_from_universal:
        from belener.parse import apply_stamp_filename_hints

        stamp = finalize_stamp(apply_stamp_filename_hints(stamp, filename))

    from belener.notes_filter import filter_notes_to_tt

    sheet_notes: dict[str, Any] | None = filter_notes_to_tt(zoned_sheet_notes)
    if sheet_text_enabled():
        built = build_sheet_notes_payload(sheet_text_ocr, vision_sheet_notes)
        sheet_notes = filter_notes_to_tt(built) or sheet_notes
    vision_text_blocks = []
    ocr_text_blocks = []

    warnings: list[str] = []
    text_len = len(page.get_text("text").strip())
    if text_len < 120:
        if vision_only or scanned:
            warnings.append("Скан без текстового слоя — данные прочитаны vision/OCR по зонам; сверьте с PDF.")
        else:
            warnings.append("Текстовый слой почти пуст — данные из зон листа; сверьте с PDF.")
    if use_post and vision_model and pipeline != "belener_ocr":
        warnings.append("Данные уточнены vision на постобработке. Сверьте с PDF.")
    elif need_vision and pipeline != "belener_ocr":
        warnings.append("Штамп дополнен vision-моделью. Сверьте с PDF.")
    elif vision_zones_enabled() and vision_mode() == "auto":
        warnings.append("Данные прочитаны OCR. Сверьте с PDF.")
    elif vision_zones_enabled() and vision_mode() != "off":
        warnings.append("Vision-модель не найдена в Ollama. Установите qwen2.5vl:7b.")
    if _stamp_incomplete(stamp):
        warnings.append("Штамп: не все поля рамки прочитаны — сверьте с PDF.")
    if not tables:
        warnings.append("Таблицы на листе не найдены.")
    elif not any((t.get("rows") or []) for t in tables):
        warnings.append("Таблицы найдены, но строки не распознаны — сверьте с PDF.")
    if scanned and layout_blocks:
        warnings.append(f"Лист разбит на блоки: {len(layout_blocks)}; основной режим — быстрый OCR, vision только если включён.")
    for t in tables:
        if (t.get("rows") or []) and not str(t.get("title") or "").strip():
            warnings.append("Заголовок таблицы не прочитан — сверьте с PDF.")
            break
    cipher_val = next((x.get("value", "") for x in stamp.get("kv", []) if "шифр" in x.get("field", "").lower()), "")
    if cipher_val and not re.search(r"[А-Яа-яЁё]\d$", cipher_val):
        warnings.append("Шифр может быть неполным — сверьте с полем «Обозначение» в штампе.")
    if quality.get("issue_count"):
        warnings.append(
            f"Проверка рамок: найдено объектов за границами листа — {quality.get('issue_count')}."
        )

    log.info(
        "analyze done in %.1fs pipeline=%s vision=%s (%s)",
        time.monotonic() - t0,
        pipeline,
        vision_model or "-",
        filename,
    )
    from belener.normative_refs import collect_normative_refs

    full_ocr_text = ocr_blob

    normative_refs = collect_normative_refs(
        {
            "tables": tables,
            "stamp": stamp,
            "sheet_notes": sheet_notes,
            "zone_ocr_texts": zone_ocr_texts,
            "full_text_pages": full_text_pages,
            "text_blocks": [*ocr_text_blocks, *vision_text_blocks],
            "table_text": table_text,
            "body_text": sheet_text_ocr or "",
            "normative_scan_text": zone_ocr_texts.get("normative_scan") or "",
            "full_ocr_text": full_ocr_text,
        }
    )

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": pipeline,
        "filename": filename,
        "page_count": doc.page_count,
        "wide_sheet": zones.wide,
        "ocr_zones": _ocr_zone_lengths(ocr),
        "zone_ocr_texts": zone_ocr_texts,
        "normative_refs": normative_refs,
        "normative_scan_text": zone_ocr_texts.get("normative_scan") or "",
        "full_ocr_text": full_ocr_text,
        "vision_model": vision_model or None,
        "stamp": stamp,
        "full_text_pages": full_text_pages,
        "quality": quality,
        "layout_blocks": [
            {
                "kind": b.kind,
                "label": b.label,
                "bbox": [round(b.rect.x0, 2), round(b.rect.y0, 2), round(b.rect.x1, 2), round(b.rect.y1, 2)],
            }
            for b in layout_blocks
        ],
        "text_blocks": [*ocr_text_blocks, *vision_text_blocks],
        "tables": tables,
        "explication": {"title": expl_title, "rows": expl},
        "legend": {"title": leg_title, "rows": legend, "row_count": len(legend)},
        "sheet_notes": sheet_notes,
        "body_text": sheet_text_ocr or "",
        "table_text": table_text or "",
        "warnings": warnings,
    }


def analyze_pdf_bytes(data: bytes, filename: str = "document.pdf") -> dict[str, Any]:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return analyze_pdf_document(doc, filename)
    finally:
        doc.close()


def analyze_pdf_path(path: str, filename: str | None = None) -> dict[str, Any]:
    p = Path(path)
    return analyze_pdf_bytes(p.read_bytes(), filename or p.name)


def analyze_pdf_path_markdown(path: str, filename: str | None = None) -> str:
    from belener.extract import extract_pdf_path
    from belener.extract_report import extraction_to_markdown

    return extraction_to_markdown(extract_pdf_path(path, filename))
