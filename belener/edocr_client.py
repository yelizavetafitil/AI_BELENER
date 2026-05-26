"""Клиент локального сервиса eDOCr (опциональный контейнер, без облака)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import fitz

from belener.config import edocr_timeout_sec, edocr_url

log = logging.getLogger("belener.edocr")


def edocr_available() -> bool:
    return bool(edocr_url())


def extract_edocr_pdf(pdf_path: str | Path, *, page_index: int = 0) -> dict[str, Any]:
    """POST PDF на локальный eDOCr; возвращает таблицы/текст или пустой результат."""
    base = edocr_url()
    if not base:
        return {"ok": False, "tables": [], "table_text": "", "stamp": None}

    import urllib.error
    import urllib.request

    path = Path(pdf_path)
    if not path.is_file():
        return {"ok": False, "error": "file not found"}

    data = path.read_bytes()
    boundary = b"----belener-edocr"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="' + path.name.encode("utf-8") + b'"\r\n'
        b"Content-Type: application/pdf\r\n\r\n"
        + data
        + b"\r\n--" + boundary + b"--\r\n"
    )
    req = urllib.request.Request(
        f"{base}/parse",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=edocr_timeout_sec()) as resp:
            import json

            raw = resp.read().decode("utf-8", errors="replace")
            out = json.loads(raw)
    except urllib.error.URLError as e:
        log.warning("eDOCr unavailable: %s", e)
        return {"ok": False, "tables": [], "table_text": ""}
    except Exception as e:
        log.exception("eDOCr request failed")
        return {"ok": False, "error": str(e)}

    if not isinstance(out, dict):
        return {"ok": False, "tables": [], "table_text": ""}
    out.setdefault("ok", bool(out.get("tables") or out.get("table_text")))
    return out


def extract_edocr_doc(doc: fitz.Document, pdf_path: str, page_index: int = 0) -> dict[str, Any]:
    return extract_edocr_pdf(pdf_path, page_index=page_index)
