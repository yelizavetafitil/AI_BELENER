"""
Локальный OCR через Surya (отдельный Docker-сервис).

Включение: PDF_OCR_ENGINE=surya  SURYA_OCR_URL=http://surya-ocr:8081
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from belener.ocr_http import health_get, try_ocr_endpoints

log = logging.getLogger("belener.surya_ocr")

_PROMPT_TABLE = "table"
_PROMPT_STAMP = "stamp"
_PROMPT_DEFAULT = "default"


def surya_ocr_url() -> str:
    return (os.environ.get("SURYA_OCR_URL") or "").strip().rstrip("/")


def surya_ocr_path() -> str:
    return (os.environ.get("SURYA_OCR_PATH") or "/api/ocr").strip() or "/api/ocr"


def surya_ocr_timeout() -> float:
    try:
        return max(30.0, min(float(os.environ.get("SURYA_OCR_TIMEOUT", "240").strip()), 900.0))
    except ValueError:
        return 240.0


def surya_ocr_enabled() -> bool:
    from belener.config import ocr_engine

    eng = ocr_engine()
    return eng in ("surya", "auto") and bool(surya_ocr_url())


def _mode_for_zone(zone: str) -> str:
    z = (zone or "").casefold()
    if z.startswith("stamp"):
        return _PROMPT_STAMP
    if z.startswith(("spec_", "legend", "tables_block", "explication", "table")):
        return _PROMPT_TABLE
    return _PROMPT_DEFAULT


def health_check(*, timeout: float = 5.0) -> bool:
    return health_get(surya_ocr_url(), timeout=timeout)


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    filename: str = "page.png",
    zone: str = "",
    timeout: float | None = None,
) -> str:
    base = surya_ocr_url()
    if not base or not image_bytes:
        return ""
    t = timeout if timeout is not None else surya_ocr_timeout()
    mode = _mode_for_zone(zone)
    paths = [surya_ocr_path()]
    for alt in ("/api/ocr", "/ocr/image", "/ocr/table"):
        if alt not in paths:
            paths.append(alt)
    fields = {"zone": zone or "body", "mode": mode}
    return try_ocr_endpoints(
        base,
        image_bytes,
        paths=paths,
        filename=filename,
        fields=fields,
        timeout=t,
        extra_field_sets=[{}, {"prompt": mode}],
    )


def ocr_pil_image(img: Any, *, zone: str = "", filename: str = "zone.png") -> str:
    import io

    from PIL import Image

    if not isinstance(img, Image.Image):
        return ""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return ocr_image_bytes(buf.getvalue(), filename=filename, zone=zone)


def normalize_surya_table_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.MULTILINE)
    lines: list[str] = []
    for ln in t.split("\n"):
        s = ln.strip()
        if not s or s.startswith("```"):
            continue
        if "|" in s and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            if cells:
                lines.append("\t".join(cells))
                continue
        lines.append(s)
    return "\n".join(lines).strip()
