"""
Локальный PaddleOCR (HTTP) — для зон spec_* и stamp_* после fine-tune rec.

Включение: PADDLE_OCR_URL=http://paddle-ocr:8082  PDF_OCR_PADDLE_ZONES=1
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from belener.ocr_http import health_get, try_ocr_batch_endpoints, try_ocr_endpoints

log = logging.getLogger("belener.paddle_ocr")

_HEALTH_CACHE: tuple[float, bool] | None = None
_HEALTH_TTL_SEC = 120.0


def paddle_ocr_url() -> str:
    return (os.environ.get("PADDLE_OCR_URL") or "").strip().rstrip("/")


def paddle_ocr_path() -> str:
    return (os.environ.get("PADDLE_OCR_PATH") or "/api/ocr").strip() or "/api/ocr"


def paddle_ocr_timeout() -> float:
    try:
        return max(20.0, min(float(os.environ.get("PADDLE_OCR_TIMEOUT", "120").strip()), 600.0))
    except ValueError:
        return 120.0


def paddle_zone_match(zone: str) -> bool:
    """Зоны, для которых используем дообученный Paddle вместо Tesseract."""
    z = (zone or "").casefold()
    from belener.config import accuracy_mode, unified_sheet_ocr_enabled

    if unified_sheet_ocr_enabled() and not accuracy_mode():
        # fast unified: один Tesseract/Paddle блок на колонку; Paddle только штамп/spec
        if z in ("tables_block", "right_column", "sheet_notes", "body"):
            return False
    return (
        z.startswith("spec_")
        or z.startswith("stamp")
        or z.startswith("legend")
        or z.startswith("explication")
        or z.startswith("table")
        or z.startswith("sheet_notes")
    )


def paddle_ocr_zones_enabled() -> bool:
    if not paddle_ocr_url():
        return False
    return (os.environ.get("PDF_OCR_PADDLE_ZONES") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def paddle_ocr_enabled() -> bool:
    """URL и флаг зон — без HTTP /health на каждый вызов (ускоряет пакетную OCR ячеек)."""
    return paddle_ocr_zones_enabled() and bool(paddle_ocr_url())


def health_check(*, timeout: float = 8.0) -> bool:
    return paddle_service_ready(timeout=timeout)


def paddle_service_ready(*, timeout: float = 5.0, force: bool = False) -> bool:
    """Paddle готов к OCR (модели загружены, без preload_error)."""
    global _HEALTH_CACHE
    now = time.monotonic()
    if not force and _HEALTH_CACHE is not None:
        ts, ready = _HEALTH_CACHE
        if now - ts < _HEALTH_TTL_SEC:
            return ready

    base = paddle_ocr_url()
    if not base:
        _HEALTH_CACHE = (now, False)
        return False
    try:
        import json
        import urllib.request

        req = urllib.request.Request(f"{base}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if data.get("preload_error"):
            ready = False
        else:
            ready = bool(data.get("models_loaded"))
    except Exception:
        ready = False
    _HEALTH_CACHE = (now, ready)
    return ready


def paddle_batch_size() -> int:
    try:
        return max(4, min(int(os.environ.get("PADDLE_OCR_BATCH_SIZE", "16").strip()), 32))
    except ValueError:
        return 16


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    filename: str = "zone.png",
    zone: str = "",
    timeout: float | None = None,
) -> str:
    base = paddle_ocr_url()
    if not base or not image_bytes:
        return ""
    t = timeout if timeout is not None else paddle_ocr_timeout()
    paths = [paddle_ocr_path()]
    for alt in ("/api/ocr", "/ocr/image"):
        if alt not in paths:
            paths.append(alt)
    return try_ocr_endpoints(
        base,
        image_bytes,
        paths=paths,
        filename=filename,
        fields={"zone": zone or "spec", "mode": "table" if zone.startswith("spec") else "stamp"},
        timeout=t,
    )


def ocr_pil_image(img: Any, *, zone: str = "", filename: str = "zone.png") -> str:
    import io

    from PIL import Image

    if not isinstance(img, Image.Image):
        return ""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return ocr_image_bytes(buf.getvalue(), filename=filename, zone=zone)


def ocr_pil_images_batch(
    images: list[Any],
    *,
    zone: str = "",
    timeout: float | None = None,
) -> list[str]:
    """Пакетный OCR — один HTTP-запрос на N ячеек (точность та же, меньше накладных расходов)."""
    import io

    from PIL import Image

    base = paddle_ocr_url()
    if not base or not images:
        return [""] * len(images)

    payload: list[tuple[bytes, str]] = []
    for i, img in enumerate(images):
        if not isinstance(img, Image.Image):
            payload.append((b"", f"cell_{i}.png"))
            continue
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        payload.append((buf.getvalue(), f"cell_{i}.png"))

    t = timeout if timeout is not None else paddle_ocr_timeout()
    texts = try_ocr_batch_endpoints(
        base,
        payload,
        paths=["/api/ocr/batch"],
        zone=zone,
        timeout=t,
    )
    if not texts or len(texts) < len(images):
        texts = []
        for i, (b, fn) in enumerate(payload):
            if b:
                texts.append(ocr_image_bytes(b, filename=fn, zone=zone, timeout=t))
            else:
                texts.append("")
    if len(texts) < len(images):
        texts.extend([""] * (len(images) - len(texts)))
    return texts[: len(images)]
