"""Общий HTTP-клиент для локальных OCR-сервисов (Surya, DeepSeek adapter)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("belener.ocr_http")


def parse_ocr_response_body(raw: str) -> str:
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


def post_multipart_ocr(
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


def try_ocr_endpoints(
    base: str,
    image_bytes: bytes,
    *,
    paths: list[str],
    filename: str,
    fields: dict[str, str],
    timeout: float,
    extra_field_sets: list[dict[str, str]] | None = None,
) -> str:
    field_sets = [fields]
    for alt in extra_field_sets or []:
        if alt not in field_sets:
            field_sets.append(alt)
    last_err: Exception | None = None
    for path in paths:
        url = f"{base.rstrip('/')}{path}"
        for flds in field_sets:
            try:
                raw = post_multipart_ocr(
                    url, image_bytes, filename=filename, fields=flds, timeout=timeout
                )
                text = parse_ocr_response_body(raw)
                if text and len(text.strip()) >= 2:
                    return text.strip()
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code in (404, 405):
                    continue
            except Exception as exc:
                last_err = exc
    if last_err:
        log.debug("OCR HTTP all endpoints failed: %s", last_err)
    return ""


def parse_ocr_batch_response_body(raw: str) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return [s] if s else []
        if isinstance(data, dict):
            texts = data.get("texts")
            if isinstance(texts, list):
                return [str(t or "").strip() for t in texts]
            one = parse_ocr_response_body(s)
            return [one] if one else []
    return [s] if s else []


def post_multipart_ocr_batch(
    url: str,
    images: list[tuple[bytes, str]],
    *,
    fields: dict[str, str] | None = None,
    timeout: float = 300.0,
) -> str:
    boundary = "----BelenerOCRBatch9xK2"
    parts: list[bytes] = []
    for name, value in (fields or {}).items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    for i, (image_bytes, filename) in enumerate(images):
        if not image_bytes:
            continue
        fname = filename or f"cell_{i}.png"
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{fname}"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(image_bytes)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def try_ocr_batch_endpoints(
    base: str,
    images: list[tuple[bytes, str]],
    *,
    paths: list[str],
    zone: str,
    timeout: float,
) -> list[str]:
    if not images:
        return []
    fields = {"zone": zone or "spec", "mode": "table" if zone.startswith("spec") else "stamp"}
    last_err: Exception | None = None
    for path in paths:
        url = f"{base.rstrip('/')}{path}"
        try:
            raw = post_multipart_ocr_batch(url, images, fields=fields, timeout=timeout)
            texts = parse_ocr_batch_response_body(raw)
            if texts and len(texts) >= len(images):
                return texts[: len(images)]
            if texts and path != "/api/ocr":
                return texts
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (404, 405):
                continue
        except Exception as exc:
            last_err = exc
    if last_err:
        log.debug("OCR batch HTTP failed: %s", last_err)
    return []


def health_get(base: str, *, timeout: float = 5.0) -> bool:
    if not base:
        return False
    for path in ("/health", "/api/health", "/"):
        try:
            req = urllib.request.Request(f"{base.rstrip('/')}{path}", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False
