"""Быстрое чтение листа: OCR по зонам (таблицы одним проходом)."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fitz

from belener.config import body_dpi, cv_cells_enabled, cv_tables_enabled, sheet_text_enabled
from belener.layout import LayoutBlock
from belener.ocr import ocr_region
from belener.parse import (
    _legend_body,
    _parse_block_rows_for_kind,
    _spec_title_from_text,
    clean_table_title,
    discover_table_sections,
    parse_explication,
    parse_legend,
    parse_numbered_notes,
    parse_specification,
    parse_stamp,
    split_text_by_section_anchors,
)
from belener.sheet_text import build_sheet_notes_payload
from belener.stamp_read import read_stamp_frame

log = logging.getLogger("belener.sheet_read")


def _best_table_rect(zones) -> tuple[str, fitz.Rect | None]:
    for key in ("spec_right", "spec_left", "legend_table", "tables_block", "explication", "legend"):
        rect = zones.rects.get(key)
        if rect is not None:
            return key, rect
    return "", None


def _tables_column_rect(zones) -> tuple[str, fitz.Rect | None]:
    col = zones.rects.get("right_column")
    if col is not None:
        return "right_column", col
    expl = zones.rects.get("explication")
    leg = zones.rects.get("legend")
    if expl is not None and leg is not None:
        return "tables_column", expl | leg
    return _best_table_rect(zones)


def _legend_table_usable(zones) -> bool:
    """Маленькая legend_table часто попадает на поле схемы — не использовать."""
    rect = zones.rects.get("legend_table")
    return rect is not None and rect.height >= 55.0


def _table_zone_jobs(zones) -> list[tuple[str, fitz.Rect]]:
    """Отдельный OCR на каждую табличную зону (перечень аппаратуры, легенда, экспликация)."""
    jobs: list[tuple[str, fitz.Rect]] = []
    seen: set[str] = set()
    has_bom = bool(zones.rects.get("spec_right") or zones.rects.get("spec_left"))
    if has_bom:
        zone_keys = ("spec_right",)
        leg_rect = zones.rects.get("legend")
        if leg_rect is not None and leg_rect.height >= 80.0:
            zone_keys = ("spec_right", "legend")
        elif _legend_table_usable(zones):
            zone_keys = ("spec_right", "legend_table")
    else:
        zone_keys = ("tables_block", "explication", "legend", "right_column")
    for key in zone_keys:
        rect = zones.rects.get(key)
        if rect is None:
            continue
        sig = f"{key}:{round(rect.x0)}:{round(rect.y0)}:{round(rect.x1)}:{round(rect.y1)}"
        if sig in seen:
            continue
        seen.add(sig)
        jobs.append((key, rect))
    if not has_bom:
        rect = zones.rects.get("right_column")
        if rect is not None:
            sig = f"right_column:{round(rect.x0)}:{round(rect.y0)}:{round(rect.x1)}:{round(rect.y1)}"
            if sig not in seen:
                jobs.append(("right_column", rect))
    if not jobs:
        k, r = _best_table_rect(zones)
        if r is not None:
            jobs.append((k or "spec_right", r))
    return jobs


def _section_from_block(kind: str, block: str, zone_key: str = "") -> dict[str, Any] | None:
    rows, parsed_kind = _parse_block_rows_for_kind(block, kind)
    if not rows:
        return None
    title = ""
    if parsed_kind == "specification":
        title = _spec_title_from_text(block, zone_key)
    elif parsed_kind == "legend":
        title = clean_table_title("Условные обозначения")
    elif parsed_kind == "explication":
        title = clean_table_title(_spec_title_from_text(block, zone_key))
    return {"kind": parsed_kind, "title": title, "rows": rows}


def _parse_table_zone_text(
    text: str,
    zone_key: str = "",
) -> tuple[list[dict], list[dict], list[dict[str, Any]]]:
    t = (text or "").strip()
    if not t:
        return [], [], []

    sections: list[dict[str, Any]] = []
    leg_rows: list[dict] = []

    if zone_key in ("spec_right", "spec_left", "tables_block"):
        spec = parse_specification(t)
        if spec:
            sections.append(
                {
                    "kind": "specification",
                    "title": _spec_title_from_text(t, zone_key),
                    "rows": spec,
                    "zone": zone_key,
                }
            )
            return [], [], sections

    blocks = split_text_by_section_anchors(t)
    if blocks:
        for kind, block in blocks:
            if zone_key in ("legend_table", "legend") and kind != "legend":
                continue
            if zone_key in ("spec_right", "spec_left") and kind == "explication":
                continue
            sec = _section_from_block(kind, block, zone_key)
            if not sec:
                continue
            if sec["kind"] == "legend":
                leg_rows = sec["rows"]
            if any(s.get("kind") == sec["kind"] for s in sections):
                continue
            sections.append(sec)
        if sections:
            return [], leg_rows, sections

    if zone_key in ("spec_right", "spec_left", "tables_block"):
        spec = parse_specification(t)
        if spec:
            sections.append(
                {
                    "kind": "specification",
                    "title": _spec_title_from_text(t, zone_key),
                    "rows": spec,
                }
            )
            return [], [], sections

    if zone_key in ("legend_table", "legend"):
        leg_src = _legend_body(t) if zone_key == "legend" else t
        leg = parse_legend(leg_src)
        if leg:
            sections.append({"kind": "legend", "title": "Условные обозначения", "rows": leg})
            return [], leg, sections
        return [], [], sections

    for sec in discover_table_sections(t):
        kind = str(sec.get("kind") or "table")
        if kind == "legend" and any(s.get("kind") == "legend" for s in sections):
            continue
        sections.append(sec)

    expl = parse_explication(t) if not any(s.get("kind") == "specification" for s in sections) else []
    return expl, leg_rows, sections


def _parse_notes_zone(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    notes = parse_numbered_notes(t)
    if notes:
        return {
            "title": "Технические требования",
            "sections": [
                {"number": m.group(1), "text": m.group(2)}
                for n in notes
                if (m := re.match(r"^(\d{1,2})\s+(.+)$", n))
            ],
            "full_text": "",
            "source": "ocr_zone",
        }
    return build_sheet_notes_payload(t)


def ocr_sheet_by_zones(
    doc: fitz.Document,
    zones,
    *,
    stamp_dpi: int,
    table_dpi: int,
    page_index: int = 0,
) -> dict[str, Any]:
    """OCR: штамп + одна правая колонка таблиц + ТТ — без дублирующих тяжёлых проходов."""
    stamp_rect = zones.rects.get("stamp_tight") or zones.rects.get("stamp")
    stamp_grid_rect = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block")
    table_jobs = _table_zone_jobs(zones)
    notes_rect = zones.rects.get("sheet_notes")
    eff_table_dpi = min(table_dpi, 480)

    def _stamp() -> dict[str, Any]:
        if stamp_rect is None:
            return parse_stamp("")
        return read_stamp_frame(
            doc,
            stamp_rect,
            dpi=min(stamp_dpi, 500),
            page_index=page_index,
            grid_rect=stamp_grid_rect,
        )

    def _ocr_table_zone(key: str, rect: fitz.Rect) -> str:
        candidates: list[tuple[str, str]] = []

        def _add(label: str, text: str) -> None:
            t = (text or "").strip()
            if t:
                candidates.append((label, t))

        from belener.config import img2table_spec_primary, img2table_zone_enabled
        from belener.table_quality import table_ocr_quality, table_ocr_weak

        spec_zone = key in ("spec_right", "spec_left", "tables_block")
        if spec_zone and img2table_spec_primary():
            try:
                from belener.img2table_extract import extract_img2table_rect, img2table_available

                if img2table_available():
                    i2 = extract_img2table_rect(
                        doc, rect, page_index=page_index, dpi=min(eff_table_dpi + 40, 600)
                    )
                    i2_text = str(i2.get("table_text") or "").strip()
                    if i2_text and table_ocr_quality(i2_text) >= 0.28:
                        log.info("zone %s img2table primary q=%.2f", key, table_ocr_quality(i2_text))
                        return i2_text
            except Exception:
                log.debug("img2table primary %s failed", key, exc_info=True)

        if key in ("spec_right", "spec_left", "tables_block", "legend_table", "legend") and cv_tables_enabled():
            try:
                from belener.cv_tables import cv_available, ocr_table_rect_cells

                if cv_available() and cv_cells_enabled():
                    cell_text = ocr_table_rect_cells(
                        doc, rect, page_index, dpi=min(eff_table_dpi, 560)
                    )
                    _add("cv_cells", cell_text)
            except Exception:
                log.debug("cell OCR for %s skipped", key, exc_info=True)

        hi_dpi = min(eff_table_dpi + 60, 600) if spec_zone else eff_table_dpi
        block_text = ocr_region(doc, page_index, rect, dpi=hi_dpi, zone=key, psm=6 if spec_zone else 4)
        _add("tesseract", block_text)

        best_label, best_text = "", ""
        best_q = -1.0
        for label, text in candidates:
            q = table_ocr_quality(text)
            if q > best_q:
                best_q, best_label, best_text = q, label, text

        if img2table_zone_enabled() and table_ocr_weak(best_text):
            try:
                from belener.img2table_extract import extract_img2table_rect, img2table_available

                if img2table_available():
                    i2 = extract_img2table_rect(
                        doc, rect, page_index=page_index, dpi=eff_table_dpi
                    )
                    i2_text = str(i2.get("table_text") or "").strip()
                    i2_q = table_ocr_quality(i2_text)
                    if i2_text and i2_q > best_q + 0.05:
                        log.info(
                            "zone %s img2table fallback q=%.2f (was %.2f %s)",
                            key,
                            i2_q,
                            best_q,
                            best_label,
                        )
                        return i2_text
            except Exception:
                log.debug("img2table zone %s failed", key, exc_info=True)

        if best_text:
            if best_label:
                log.debug("zone %s OCR via %s q=%.2f", key, best_label, best_q)
            return best_text
        return block_text

    def _one_table(job: tuple[str, fitz.Rect]) -> tuple[str, str, list[dict[str, Any]]]:
        key, rect = job
        t0 = time.monotonic()
        text = _ocr_table_zone(key, rect)
        log.info("OCR table zone %s %.1fs chars=%s", key, time.monotonic() - t0, len(text or ""))
        _, _, sections = _parse_table_zone_text(text, key)
        return key, text, sections

    def _notes() -> str:
        if not sheet_text_enabled() or notes_rect is None:
            return ""
        t0 = time.monotonic()
        text = ocr_region(
            doc,
            page_index,
            notes_rect,
            dpi=min(body_dpi(), 380),
            zone="sheet_notes",
            psm=6,
        )
        log.info("OCR sheet_notes %.1fs chars=%s", time.monotonic() - t0, len(text or ""))
        return text

    table_text_parts: list[str] = []
    table_sections: list[dict[str, Any]] = []
    table_key = table_jobs[0][0] if table_jobs else ""

    with ThreadPoolExecutor(max_workers=min(6, 1 + len(table_jobs))) as pool:
        f_notes = pool.submit(_notes)
        f_tables = [pool.submit(_one_table, job) for job in table_jobs]
        notes_text = f_notes.result()
        for fut in f_tables:
            key, text, sections = fut.result()
            if text:
                table_text_parts.append(text)
                table_sections.extend(sections)
                if not table_key:
                    table_key = key

    stamp = _stamp()

    table_text = "\n\n".join(table_text_parts)
    _, _, parsed = _parse_table_zone_text(table_text, table_key or "")
    if parsed:
        from belener.parse import merge_table_sections

        table_sections = merge_table_sections(table_sections, parsed)
    expl: list[dict] = []
    legend_rows: list[dict] = []
    for sec in table_sections:
        kind = str(sec.get("kind") or "")
        rows = sec.get("rows") or []
        if kind == "explication":
            expl.extend(rows)
        elif kind == "legend":
            legend_rows.extend(rows)

    ocr_zones: dict[str, int] = {}
    if table_text:
        ocr_zones[table_key or "right_column"] = len(table_text)
    if notes_text:
        ocr_zones["sheet_notes"] = len(notes_text)

    return {
        "stamp": stamp,
        "table_text": table_text,
        "table_key": table_key,
        "legend_text": "",
        "sheet_notes_text": notes_text,
        "sheet_notes": _parse_notes_zone(notes_text),
        "expl_rows": expl,
        "legend_rows": legend_rows,
        "table_sections": table_sections,
        "ocr_zones": ocr_zones,
    }


def ocr_sheet(
    doc: fitz.Document,
    zones,
    *,
    stamp_dpi: int,
    table_dpi: int,
    page_index: int = 0,
    page_rect: fitz.Rect | None = None,
) -> tuple[dict[str, Any], str, str, str]:
    z = ocr_sheet_by_zones(
        doc, zones, stamp_dpi=stamp_dpi, table_dpi=table_dpi, page_index=page_index
    )
    return (
        z["stamp"],
        z["table_text"],
        z["table_key"],
        z["sheet_notes_text"],
    )


def parse_table_ocr(table_text: str) -> tuple[list[dict], list[dict], list[dict[str, Any]]]:
    return _parse_table_zone_text(table_text)


def _raw_text_block(title: str, text: str) -> dict[str, str] | None:
    clean = "\n".join(ln.strip() for ln in (text or "").splitlines() if ln.strip())
    if len(clean) < 20:
        return None
    return {"title": title, "text": clean}


def ocr_layout_blocks(
    doc: fitz.Document,
    blocks: list[LayoutBlock],
    *,
    stamp_dpi: int,
    table_dpi: int,
    page_index: int = 0,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "stamp": None,
        "tables": [],
        "table_text": "",
        "text_blocks": [],
        "ocr_zones": {},
    }
    stamp_blocks = [b for b in blocks if b.kind == "stamp"][:1]
    table_blocks = [b for b in blocks if b.kind == "table"][:2]

    def _read_stamp(block: LayoutBlock) -> dict[str, Any]:
        return read_stamp_frame(doc, block.rect, dpi=min(stamp_dpi, 380), page_index=page_index)

    def _read_table(idx_block: tuple[int, LayoutBlock]) -> tuple[int, str, list[dict[str, Any]]]:
        idx, block = idx_block
        if block.rect.width < 48 or block.rect.height < 48:
            return idx, "", []
        text = ocr_region(doc, page_index, block.rect, dpi=min(table_dpi, 400), zone="table", psm=4)
        _, _, sections = _parse_table_zone_text(text)
        return idx, text, sections

    with ThreadPoolExecutor(max_workers=2) as pool:
        stamp_future = pool.submit(_read_stamp, stamp_blocks[0]) if stamp_blocks else None
        table_futures = [pool.submit(_read_table, item) for item in enumerate(table_blocks)]
        if stamp_future is not None:
            result["stamp"] = stamp_future.result()
        for fut in table_futures:
            idx, text, sections = fut.result()
            if text:
                result["ocr_zones"][f"layout_table_{idx + 1}"] = len(text)
            result["tables"].extend(sections)
    result["table_text"] = ""
    return result
