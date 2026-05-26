"""
Локальный OCR через DeepSeek-OCR (отдельный сервис в LAN/на том же сервере).

Поддерживаемые API (без облака):
- belener-adapter:  POST /api/ocr  (multipart file)
- mbrcic/vLLM:      POST /ocr/image
- daibitx/WebUI:    POST /api/ocr

Включение: PDF_OCR_ENGINE=deepseek  DEEPSEEK_OCR_URL=http://deepseek-ocr:8080
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("belener.deepseek_ocr")

_PROMPT_TABLE = (
    "OCR this engineering drawing table fragment. "
    "Output plain text only: preserve rows and columns (use TAB between columns). "
    "Russian text, designations like UCT3.1, QF1, 3ТТ. No commentary."
)
_PROMPT_STAMP = (
    "OCR the title block (штамп) of an engineering drawing. "
    "Plain text only: names, dates, drawing number, organization. Russian. No commentary."
)
_PROMPT_DEFAULT = (
    "OCR all visible text on this engineering drawing fragment. "
    "Plain text only, Russian where applicable. Preserve numbers and designations."
)


def deepseek_ocr_url() -> str:
    return (os.environ.get("DEEPSEEK_OCR_URL") or "").strip().rstrip("/")


def deepseek_ocr_path() -> str:
    return (os.environ.get("DEEPSEEK_OCR_PATH") or "/api/ocr").strip() or "/api/ocr"


def deepseek_ocr_timeout() -> float:
    try:
        return max(30.0, min(float(os.environ.get("DEEPSEEK_OCR_TIMEOUT", "180").strip()), 600.0))
    except ValueError:
        return 180.0


def deepseek_ocr_enabled() -> bool:
    from belener.config import ocr_engine

    return ocr_engine() in ("deepseek", "auto") and bool(deepseek_ocr_url())


def _prompt_for_zone(zone: str) -> str:
    z = (zone or "").casefold()
    if z.startswith("stamp"):
        return _PROMPT_STAMP
    if z.startswith(("spec_", "legend", "tables_block", "explication", "table")):
        return _PROMPT_TABLE
    return _PROMPT_DEFAULT


def _parse_response_body(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("{") or s.startswith("["):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return s
        if isinstance(data, dict):
            for key in ("text", "result", "content", "markdown", "output", "ocr_text"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            if "data" in data and isinstance(data["data"], dict):
                inner = data["data"]
                for key in ("text", "result", "content"):
                    if isinstance(inner.get(key), str):
                        return str(inner[key]).strip()
        if isinstance(data, list) and data:
            parts = [str(x).strip() for x in data if str(x).strip()]
            if parts:
                return "\n".join(parts)
    return s


def _post_multipart(
    url: str,
    image_bytes: bytes,
    *,
    filename: str = "zone.png",
    fields: dict[str, str] | None = None,
    timeout: float = 180.0,
) -> str:
    boundary = "----BelenerOCR7MA4YWxk"
    parts: list[bytes] = []
    for name, value in (fields or {}).items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(image_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _try_endpoints(
    base: str,
    image_bytes: bytes,
    *,
    filename: str,
    prompt: str,
    timeout: float,
) -> str:
    paths = [deepseek_ocr_path()]
    for alt in ("/api/ocr", "/ocr/image", "/api/v1/ocr"):
        if alt not in paths:
            paths.append(alt)
    field_sets: list[dict[str, str]] = [
        {"prompt": prompt},
        {"mode": "free", "prompt": prompt},
        {},
    ]
    last_err: Exception | None = None
    for path in paths:
        url = f"{base}{path}"
        for fields in field_sets:
            try:
                raw = _post_multipart(
                    url,
                    image_bytes,
                    filename=filename,
                    fields=fields,
                    timeout=timeout,
                )
                text = _parse_response_body(raw)
                if text and len(text.strip()) >= 2:
                    return text.strip()
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code in (404, 405):
                    continue
            except Exception as exc:
                last_err = exc
    if last_err:
        log.debug("DeepSeek-OCR all endpoints failed: %s", last_err)
    return ""


def health_check(*, timeout: float = 5.0) -> bool:
    base = deepseek_ocr_url()
    if not base:
        return False
    for path in ("/health", "/api/health", "/"):
        try:
            req = urllib.request.Request(f"{base}{path}", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    filename: str = "page.png",
    zone: str = "",
    timeout: float | None = None,
) -> str:
    """POST изображения на локальный DeepSeek-OCR API."""
    base = deepseek_ocr_url()
    if not base or not image_bytes:
        return ""
    t = timeout if timeout is not None else deepseek_ocr_timeout()
    prompt = _prompt_for_zone(zone)
    try:
        return _try_endpoints(
            base,
            image_bytes,
            filename=filename,
            prompt=prompt,
            timeout=t,
        )
    except Exception as exc:
        log.warning("DeepSeek-OCR unavailable: %s", exc)
        return ""


def ocr_pil_image(img: Any, *, zone: str = "", filename: str = "zone.png") -> str:
    import io

    from PIL import Image

    if not isinstance(img, Image.Image):
        return ""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return ocr_image_bytes(buf.getvalue(), filename=filename, zone=zone)


def normalize_deepseek_table_text(text: str) -> str:
    """Табы/маркеры markdown → строки для parse_specification."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*]\s+", "", t, flags=re.MULTILINE)
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
