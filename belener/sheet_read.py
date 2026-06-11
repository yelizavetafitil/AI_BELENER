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
from belener.zones import stamp_ocr_rect

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


def _legend_table_usable(zones, page_rect: fitz.Rect | None = None) -> bool:
    """Зона легенды: достаточный размер и не плоская полоса подписей схемы."""
    rect = zones.rects.get("legend_table")
    if rect is None or rect.height < 55.0:
        return False
    pr = page_rect
    if pr is None:
        for r in zones.rects.values():
            if r is not None and not r.is_empty:
                pr = r
                break
    if pr is None or pr.is_empty:
        return True
    aspect = rect.width / max(rect.height, 1.0)
    if aspect > 2.8 and rect.height < pr.height * 0.18:
        return False
    return True


def _legend_zone_for_tables(zones) -> tuple[str, fitz.Rect] | tuple[None, None]:
    """Левая legend_table приоритетнее правой «legend» из right_column (там часто схема)."""
    leg_tbl = zones.rects.get("legend_table")
    leg = zones.rects.get("legend")
    if leg_tbl is not None and _legend_table_usable(zones):
        if leg is None or leg_tbl.x1 <= leg.x0 + 2:
            return "legend_table", leg_tbl
    if leg is not None and leg.height >= 80.0:
        return "legend", leg
    if leg_tbl is not None and _legend_table_usable(zones):
        return "legend_table", leg_tbl
    return None, None


def _table_zone_jobs(zones) -> list[tuple[str, fitz.Rect]]:
    """OCR табличных зон: unified — одна правая колонка; иначе — по YOLO-зонам."""
    from belener.config import unified_sheet_ocr_enabled

    jobs: list[tuple[str, fitz.Rect]] = []
    seen: set[str] = set()

    if unified_sheet_ocr_enabled():
        for key in ("tables_block", "right_column", "explication"):
            rect = zones.rects.get(key)
            if rect is None or rect.is_empty:
                continue
            sig = f"{key}:{round(rect.x0)}:{round(rect.y0)}:{round(rect.x1)}:{round(rect.y1)}"
            if sig in seen:
                continue
            seen.add(sig)
            jobs.append((key, rect))
            return jobs

    has_bom = bool(zones.rects.get("spec_right") or zones.rects.get("spec_left"))
    if has_bom:
        zone_keys = tuple(
            k for k in ("spec_right", "spec_left") if zones.rects.get(k) is not None
        ) or ("spec_right",)
        leg_key, leg_rect = _legend_zone_for_tables(zones)
        if leg_key and leg_rect is not None:
            zone_keys = (*zone_keys, leg_key)
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
    # tables_block не OCR-им при отдельных spec-зонах — дублирует схему и замедляет пайплайн.
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


def _accept_table_section(sec: dict[str, Any], zone_key: str, zone_text: str) -> bool:
    from belener.config import unified_sheet_ocr_enabled

    kind = str(sec.get("kind") or "table")
    rows = sec.get("rows") or []
    if not rows:
        return False
    if unified_sheet_ocr_enabled() and zone_key in ("tables_block", "right_column", "explication"):
        if kind == "legend":
            from belener.table_quality import legend_ocr_plausible

            return legend_ocr_plausible(zone_text, rows)
        if kind == "specification":
            from belener.table_quality import spec_table_header_present, spec_table_plausible
            from belener.normative_spec import normative_bom_plausible

            if spec_table_plausible(zone_text, rows) or normative_bom_plausible(rows):
                return True
            return spec_table_header_present(zone_text) and len(rows) >= 1
        return True
    if kind == "legend":
        if zone_key in ("spec_right", "spec_left", "tables_block"):
            return False
        from belener.table_quality import legend_ocr_plausible

        return legend_ocr_plausible(zone_text, rows)
    if kind == "specification":
        from belener.table_quality import spec_table_plausible
        from belener.normative_spec import normative_bom_plausible

        if spec_table_plausible(zone_text, rows):
            return True
        return normative_bom_plausible(rows)
    if kind == "explication":
        from belener.table_quality import explication_table_plausible

        return explication_table_plausible(zone_text, rows)
    return True


def _parse_table_zone_text(
    text: str,
    zone_key: str = "",
) -> tuple[list[dict], list[dict], list[dict[str, Any]]]:
    t = (text or "").strip()
    if not t:
        return [], [], []

    sections: list[dict[str, Any]] = []
    leg_rows: list[dict] = []

    if zone_key in ("tables_block", "right_column", "explication"):
        from belener.config import unified_sheet_ocr_enabled
        from belener.normative_spec import parse_normative_bom_rows

        for sec in discover_table_sections(t):
            kind = str(sec.get("kind") or "table")
            rows = sec.get("rows") or []
            if not rows:
                continue
            item = {
                "kind": kind,
                "title": sec.get("title") or _spec_title_from_text(t, zone_key),
                "rows": rows,
                "zone": zone_key,
            }
            if not _accept_table_section(item, zone_key, t):
                continue
            sections.append(item)
        spec = parse_specification(t)
        if spec and _accept_table_section(
            {"kind": "specification", "rows": spec, "title": ""}, zone_key, t
        ):
            if not any(s.get("kind") == "specification" for s in sections):
                sections.append(
                    {
                        "kind": "specification",
                        "title": _spec_title_from_text(t, zone_key),
                        "rows": spec,
                        "zone": zone_key,
                    }
                )
        norm = parse_normative_bom_rows(t)
        if norm and not any(s.get("kind") == "specification" for s in sections):
            sections.append(
                {
                    "kind": "specification",
                    "title": "Перечень аппаратуры",
                    "rows": norm,
                    "zone": zone_key,
                }
            )
        elif norm and unified_sheet_ocr_enabled():
            for sec in sections:
                if sec.get("kind") == "specification":
                    from belener.spec_table import dedupe_spec_rows

                    sec["rows"] = dedupe_spec_rows(list(sec.get("rows") or []) + norm)
                    break
        if sections:
            return [], [], sections

    if zone_key in ("spec_right", "spec_left"):
        from belener.table_quality import spec_table_plausible

        spec = parse_specification(t)
        if spec and spec_table_plausible(t, spec):
            sections.append(
                {
                    "kind": "specification",
                    "title": _spec_title_from_text(t, zone_key),
                    "rows": spec,
                    "zone": zone_key,
                }
            )
            return [], [], sections
        if spec:
            log.info("zone %s spec dropped (schematic/no header OCR)", zone_key)
            return [], [], sections

    blocks = split_text_by_section_anchors(t)
    if blocks:
        for kind, block in blocks:
            if zone_key in ("legend_table", "legend") and kind != "legend":
                continue
            if zone_key in ("spec_right", "spec_left") and kind in ("explication", "legend"):
                continue
            sec = _section_from_block(kind, block, zone_key)
            if not sec:
                continue
            if not _accept_table_section(sec, zone_key, block):
                continue
            if sec["kind"] == "legend":
                leg_rows = sec["rows"]
            if any(s.get("kind") == sec["kind"] for s in sections):
                continue
            sections.append(sec)
        if sections:
            return [], leg_rows, sections

    if zone_key in ("spec_right", "spec_left", "tables_block"):
        from belener.table_quality import spec_table_plausible

        spec = parse_specification(t)
        if spec and spec_table_plausible(t, spec):
            sections.append(
                {
                    "kind": "specification",
                    "title": _spec_title_from_text(t, zone_key),
                    "rows": spec,
                    "zone": zone_key,
                }
            )
            return [], [], sections

    if zone_key in ("legend_table", "legend"):
        from belener.table_quality import legend_ocr_plausible

        leg_src = _legend_body(t) if zone_key == "legend" else t
        leg = parse_legend(leg_src)
        if leg and legend_ocr_plausible(t, leg):
            sections.append({"kind": "legend", "title": "Условные обозначения", "rows": leg})
            return [], leg, sections
        if leg:
            log.info("zone %s legend dropped (schematic/noise OCR)", zone_key)
        return [], [], sections

    for sec in discover_table_sections(t):
        kind = str(sec.get("kind") or "table")
        if kind == "legend" and any(s.get("kind") == "legend" for s in sections):
            continue
        if not _accept_table_section(sec, zone_key, t):
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
    page_rect = doc[page_index].rect
    stamp_rect = stamp_ocr_rect(zones, page_rect)
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
        from belener.config import ocr_engine
        from belener.paddle_ocr import paddle_ocr_enabled, paddle_zone_match

        is_paddle = paddle_ocr_enabled() and paddle_zone_match(key)
        ocr_label = "paddle" if is_paddle else (ocr_engine() if ocr_engine() in ("surya", "deepseek") else "tesseract")

        if spec_zone and img2table_spec_primary() and not is_paddle:
            try:
                from belener.img2table_extract import extract_img2table_rect, img2table_available

                if img2table_available():
                    i2 = extract_img2table_rect(
                        doc, rect, page_index=page_index, dpi=min(eff_table_dpi + 40, 600)
                    )
                    i2_text = str(i2.get("table_text") or "").strip()
                    if i2_text and table_ocr_quality(i2_text) >= 0.28:
                        _add("img2table", i2_text)
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
                log.warning("cell OCR for %s skipped", key, exc_info=True)

        hi_dpi = min(eff_table_dpi + 60, 600) if spec_zone else eff_table_dpi
        
        # Если cv_cells дал текст — не дублировать тяжёлым OCR всего блока
        run_full = True
        cv_ok = any(
            lbl == "cv_cells" and len(txt) > 15 and table_ocr_quality(txt) >= 0.22
            for lbl, txt in candidates
        )
        if cv_ok or (is_paddle and any(lbl == "cv_cells" for lbl, txt in candidates if len(txt) > 15)):
            run_full = False
            
        block_text = ""
        if run_full:
            log.info("Starting run_full block for %s", key)
            block_text = ocr_region(doc, page_index, rect, dpi=hi_dpi, zone=key, psm=6 if spec_zone else 4)
            _add(ocr_label, block_text)

        best_label, best_text = "", ""
        best_q = -1.0
        for label, text in candidates:
            q = table_ocr_quality(text)
            if q > best_q:
                best_q, best_label, best_text = q, label, text

        if best_text and best_label:
            log.info("zone %s OCR via %s q=%.2f chars=%s", key, best_label, best_q, len(best_text))

        if not is_paddle and img2table_zone_enabled() and table_ocr_weak(best_text or block_text):
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
        for sec in sections:
            sec.setdefault("zone", key)
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
    zone_texts: dict[str, str] = {}

    from belener.config import ocr_engine as _ocr_engine

    # Surya/DeepSeek — последовательно: меньше RAM и стабильнее на CPU.
    _parallel = 1 if _ocr_engine() in ("surya", "deepseek") else min(6, 1 + len(table_jobs))

    with ThreadPoolExecutor(max_workers=_parallel) as pool:
        f_stamp = pool.submit(_stamp)
        f_notes = pool.submit(_notes)
        f_tables = [pool.submit(_one_table, job) for job in table_jobs]
        notes_text = f_notes.result()
        for fut in f_tables:
            key, text, sections = fut.result()
            if text:
                zone_texts[key] = text
                table_text_parts.append(text)
                table_sections.extend(sections)
                if not table_key:
                    table_key = key
        stamp = f_stamp.result()

    table_text = "\n\n".join(table_text_parts)
    _, _, parsed = _parse_table_zone_text(table_text, table_key or "")
    if parsed:
        from belener.parse import merge_table_sections

        table_sections = merge_table_sections(table_sections, parsed)

    from belener.table_quality import spec_table_header_present, table_ocr_quality

    def _spec_score(sec: dict[str, Any]) -> tuple[int, float, int]:
        ztxt = str(zone_texts.get(str(sec.get("zone") or ""), ""))
        return (
            1 if spec_table_header_present(ztxt) else 0,
            table_ocr_quality(ztxt),
            len(sec.get("rows") or []),
        )

    spec_secs = [s for s in table_sections if s.get("kind") == "specification"]
    if len(spec_secs) > 1:
        by_zone: dict[str, dict[str, Any]] = {}
        for sec in spec_secs:
            z = str(sec.get("zone") or "_")
            if z not in by_zone or _spec_score(sec) > _spec_score(by_zone[z]):
                by_zone[z] = sec
        tb = by_zone.get("tables_block")
        if tb and _spec_score(tb)[0]:
            for weak in ("spec_right", "spec_left"):
                w = by_zone.get(weak)
                if w and not _spec_score(w)[0]:
                    by_zone.pop(weak, None)
        table_sections = [s for s in table_sections if s.get("kind") != "specification"]
        table_sections.extend(by_zone.values())
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
        "zone_texts": zone_texts,
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
