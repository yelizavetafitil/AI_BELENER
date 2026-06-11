"""
Детекция зон YOLOv8 (spec_table, stamp, legend) — опционально поверх геометрии build_zones.

Обучение: scripts/export_yolo_dataset.py + scripts/train_yolo_zones.py
Модель: data/training/yolo_zones/runs/detect/train/weights/best.pt
"""

from __future__ import annotations

import logging
import os
from typing import Any

import fitz

from belener.config import yolo_zones_conf, yolo_zones_enabled, yolo_zones_model_path
from belener.zones import SheetZones

log = logging.getLogger("belener.yolo_zones")

# class_id -> ключи rect в SheetZones
YOLO_CLASS_NAMES: tuple[str, ...] = ("spec_table", "stamp", "legend")

_CLASS_TO_ZONE_KEYS: dict[int, tuple[str, ...]] = {
    0: ("spec_right", "spec_left"),
    1: ("stamp_frame", "stamp_block"),
    2: ("legend_table", "legend"),
}

_MODEL: Any = None


def _zone_area_ratio(rect: fitz.Rect, page_rect: fitz.Rect) -> float:
    if rect.is_empty or page_rect.is_empty:
        return 0.0
    return (rect.width * rect.height) / max(page_rect.width * page_rect.height, 1.0)


def _yolo_spec_plausible(rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
    """Перечень — узкая колонка, не поле схемы."""
    if rect.is_empty or page_rect.is_empty:
        return False
    if _zone_area_ratio(rect, page_rect) > 0.28:
        return False
    if rect.width > page_rect.width * 0.52 and rect.height > page_rect.height * 0.38:
        return False
    return True


def _yolo_legend_plausible(rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
    if rect.is_empty or page_rect.is_empty:
        return False
    aspect = rect.width / max(rect.height, 1.0)
    if aspect > 2.5 and rect.height < page_rect.height * 0.20:
        return False
    if _zone_area_ratio(rect, page_rect) > 0.12 and aspect > 2.0:
        return False
    return True


def _load_model() -> Any:
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    path = yolo_zones_model_path()
    if not path:
        return None
    try:
        from ultralytics import YOLO
    except ImportError:
        log.warning("ultralytics not installed — YOLO zones disabled")
        return None
    _MODEL = YOLO(path)
    log.info("YOLO zones model loaded: %s", path)
    return _MODEL


def preload_yolo_model() -> None:
    """Загрузить YOLO при старте веба — первый PDF без ~90 с cold start."""
    if not yolo_zones_enabled():
        return
    try:
        _load_model()
    except Exception:
        log.warning("YOLO preload failed", exc_info=True)


def _page_image(doc: fitz.Document, page_index: int, dpi: int = 150) -> Any:
    from PIL import Image

    page = doc[page_index]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), page.rect, zoom


def _predict_zone_rects_once(
    doc: fitz.Document,
    page_index: int,
    *,
    dpi: int = 150,
) -> dict[str, fitz.Rect]:
    model = _load_model()
    if model is None:
        return {}
    img, page_rect, zoom = _page_image(doc, page_index, dpi=dpi)
    conf = yolo_zones_conf()
    try:
        results = model.predict(img, conf=conf, verbose=False)
    except Exception:
        log.exception("YOLO predict failed")
        return {}
    if not results:
        return {}
    r0 = results[0]
    boxes = getattr(r0, "boxes", None)
    if boxes is None:
        return {}
    out: dict[str, fitz.Rect] = {}
    spec_cands: list[tuple[str, fitz.Rect]] = []
    names = getattr(r0, "names", None) or {}
    for box in boxes:
        cls_id = int(box.cls.item()) if hasattr(box.cls, "item") else int(box.cls)
        xyxy = box.xyxy[0].tolist() if hasattr(box.xyxy, "__getitem__") else box.xyxy
        x0, y0, x1, y1 = xyxy
        # пиксели → PDF points
        rx0 = page_rect.x0 + x0 / zoom
        ry0 = page_rect.y0 + y0 / zoom
        rx1 = page_rect.x0 + x1 / zoom
        ry1 = page_rect.y0 + y1 / zoom
        rect = fitz.Rect(rx0, ry0, rx1, ry1)
        cy = (rect.y0 + rect.y1) / 2
        top_band = page_rect.y0 + page_rect.height * 0.28
        # spec_table в верхней части листа — чаще легенда, не перечень
        if cls_id == 0 and cy < top_band and rect.height < page_rect.height * 0.30:
            out["legend_table"] = rect
            continue
        keys = _CLASS_TO_ZONE_KEYS.get(cls_id)
        if not keys and isinstance(names, dict):
            name = str(names.get(cls_id, "")).casefold()
            if "spec" in name:
                keys = ("spec_right",)
            elif "stamp" in name:
                keys = ("stamp_frame",)
            elif "legend" in name:
                keys = ("legend_table",)
        if not keys:
            continue
        if cls_id == 0 and len(keys) > 1:
            cx = (rect.x0 + rect.x1) / 2
            mid = page_rect.x0 + page_rect.width * 0.5
            key = "spec_right" if cx >= mid else "spec_left"
            if _yolo_spec_plausible(rect, page_rect):
                spec_cands.append((key, rect))
            continue
        if cls_id == 0 and not _yolo_spec_plausible(rect, page_rect):
            continue
        if cls_id == 2 and not _yolo_legend_plausible(rect, page_rect):
            continue
        if cls_id == 0:
            spec_cands.append((keys[0], rect))
            continue
        out[keys[0]] = rect
    for key in ("spec_left", "spec_right"):
        cands = [r for k, r in spec_cands if k == key]
        if not cands:
            continue
        out[key] = min(cands, key=lambda r: r.width * r.height)
    return out


def _multiscale_enabled() -> bool:
    return (os.environ.get("PDF_YOLO_MULTISCALE") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def predict_zone_rects(
    doc: fitz.Document,
    page_index: int,
    *,
    dpi: int = 150,
) -> dict[str, fitz.Rect]:
    """Детекции → словарь zone_key → rect (PDF points)."""
    if not yolo_zones_enabled():
        return {}
    out = _predict_zone_rects_once(doc, page_index, dpi=dpi)
    if not _multiscale_enabled():
        return out

    # Дорогой второй/третий прогон только когда не найдены ключевые зоны.
    need_stamp = "stamp_frame" not in out
    need_legend = "legend_table" not in out and "legend" not in out
    need_spec = "spec_right" not in out and "spec_left" not in out
    if not (need_stamp or need_legend or need_spec):
        return out

    for alt_dpi in (90, 220):
        extra = _predict_zone_rects_once(doc, page_index, dpi=alt_dpi)
        if need_spec:
            for k in ("spec_right", "spec_left"):
                if k in extra and k not in out:
                    out[k] = extra[k]
            need_spec = "spec_right" not in out and "spec_left" not in out
        if need_stamp and "stamp_frame" in extra:
            out["stamp_frame"] = extra["stamp_frame"]
            need_stamp = False
        if need_legend:
            if "legend_table" in extra:
                out["legend_table"] = extra["legend_table"]
                need_legend = False
            elif "legend" in extra:
                out["legend"] = extra["legend"]
                need_legend = False
        if not (need_stamp or need_legend or need_spec):
            break

    return out


def merge_yolo_into_zones(
    zones: SheetZones,
    yolo_rects: dict[str, fitz.Rect],
    page_rect: fitz.Rect | None = None,
) -> SheetZones:
    if not yolo_rects:
        return zones
    merged = dict(zones.rects)
    pr = page_rect or next(iter(merged.values()), None)
    min_spec_h = (pr.height * 0.38) if pr is not None else 0.0
    for key, rect in yolo_rects.items():
        if rect.is_empty:
            continue
        out = rect
        if key in ("spec_right", "spec_left") and pr is not None and min_spec_h > 0:
            prev = zones.rects.get(key)
            if prev is not None and not prev.is_empty and _yolo_spec_plausible(prev, pr):
                if prev.height > out.height and _yolo_spec_plausible(out, pr):
                    out = (out | prev) & pr
            elif prev is not None and not prev.is_empty and not _yolo_spec_plausible(out, pr):
                out = prev
            if out.height < min_spec_h and _yolo_spec_plausible(out, pr):
                for extra in ("tables_block", "right_column"):
                    tb = zones.rects.get(extra)
                    if tb is not None and not tb.is_empty:
                        out = (out | tb) & pr
                        break
        if key in ("spec_right", "spec_left") and pr is not None and not _yolo_spec_plausible(out, pr):
            prev = zones.rects.get(key)
            if prev is not None and not prev.is_empty:
                out = prev
            else:
                continue
        if key in ("legend", "legend_table") and pr is not None and out.height > pr.height * 0.32:
            prev = zones.rects.get("legend_table") or zones.rects.get("legend")
            if prev is not None and not prev.is_empty and prev.height < out.height:
                out = prev
            else:
                continue
        if key == "stamp_frame" and pr is not None:
            prev = zones.rects.get("stamp_frame")
            if out.height > pr.height * 0.38 or out.width > pr.width * 0.52:
                if prev is not None and not prev.is_empty:
                    out = prev
                else:
                    bw = pr.width * 0.45
                    bh = pr.height * 0.32
                    out = fitz.Rect(pr.x1 - bw, pr.y1 - bh, pr.x1, pr.y1) & pr
        merged[key] = out
        if key == "stamp_frame":
            merged["stamp_block"] = out
    return SheetZones(rects=merged, wide=zones.wide)


def apply_yolo_zones(
    doc: fitz.Document,
    page_index: int,
    page_rect: fitz.Rect,
    zones: SheetZones,
) -> SheetZones:
    if not yolo_zones_enabled():
        return zones
    if not yolo_zones_model_path():
        log.debug("YOLO zones включён, best.pt нет — зоны из discover/refine (см. train_yolo_zones.py)")
        return zones
    try:
        yolo_rects = predict_zone_rects(doc, page_index)
        return merge_yolo_into_zones(zones, yolo_rects, page_rect)
    except Exception:
        log.exception("apply_yolo_zones failed")
        return zones
