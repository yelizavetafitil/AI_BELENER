"""
Опциональный локальный OCR через DeepSeek-OCR Web (отдельный сервис).

См. https://github.com/fufankeji/DeepSeek-OCR-Web — Linux + GPU ≥7 GB, без облака.
Включение: DEEPSEEK_OCR_URL=http://host:8002  PDF_OCR_ENGINE=deepseek
Данные не уходят в интернет, если сервис поднят на вашей машине/LAN.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("belener.deepseek_ocr")


def deepseek_ocr_url() -> str:
    return (os.environ.get("DEEPSEEK_OCR_URL") or "").strip().rstrip("/")


def deepseek_ocr_enabled() -> bool:
    engine = (os.environ.get("PDF_OCR_ENGINE") or "tesseract").strip().casefold()
    return engine == "deepseek" and bool(deepseek_ocr_url())


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    filename: str = "page.png",
    timeout: float = 120.0,
) -> str:
    """POST изображения на локальный DeepSeek-OCR API (если настроен)."""
    base = deepseek_ocr_url()
    if not base or not image_bytes:
        return ""
    try:
        import urllib.request

        boundary = "----belenerocr"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + image_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/ocr",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return raw.strip()
    except Exception as exc:
        log.warning("DeepSeek-OCR unavailable: %s", exc)
        return ""
