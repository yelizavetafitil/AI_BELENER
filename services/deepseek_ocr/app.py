"""
Belener DeepSeek-OCR adapter — единый локальный API для belener.

Проксирует запросы на бэкенд vLLM (mbrcic/Deepseek-OCR-vllm-docker и аналоги).
Чертежи не покидают ваш сервер/LAN.

Запуск: DEEPSEEK_BACKEND_URL=http://127.0.0.1:8000  uvicorn app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

log = logging.getLogger("deepseek_ocr_adapter")

app = FastAPI(title="Belener DeepSeek-OCR Adapter", version="1.0.0")

BACKEND = (os.environ.get("DEEPSEEK_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = float(os.environ.get("DEEPSEEK_BACKEND_TIMEOUT", "300"))
DEFAULT_PROMPT = (
    "OCR this engineering drawing. Plain text only, Russian, preserve table structure with TABs."
)


def _parse_backend_json(data: Any) -> str:
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        for key in ("text", "result", "content", "markdown", "output"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


async def _forward_to_backend(
    content: bytes,
    filename: str,
    prompt: str,
) -> str:
    paths = ("/ocr/image", "/api/ocr", "/api/v1/ocr")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for path in paths:
            url = f"{BACKEND}{path}"
            files = {"file": (filename, content, "image/png")}
            data = {"prompt": prompt}
            try:
                resp = await client.post(url, files=files, data=data)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if "json" in ctype:
                    return _parse_backend_json(resp.json())
                return (resp.text or "").strip()
            except httpx.HTTPStatusError as exc:
                log.warning("backend %s -> %s", url, exc.response.status_code)
            except Exception as exc:
                log.warning("backend %s failed: %s", url, exc)
    return ""


@app.get("/health")
async def health() -> JSONResponse:
    backend_ok = False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for path in ("/health", "/"):
                try:
                    r = await client.get(f"{BACKEND}{path}")
                    if r.status_code == 200:
                        backend_ok = True
                        break
                except Exception:
                    continue
    except Exception:
        pass
    return JSONResponse(
        {
            "status": "ok",
            "adapter": True,
            "backend": BACKEND,
            "backend_healthy": backend_ok,
        }
    )


@app.post("/api/ocr")
async def api_ocr(
    file: UploadFile = File(...),
    prompt: str = Form(default=""),
) -> PlainTextResponse:
    raw = await file.read()
    if not raw:
        return PlainTextResponse("", status_code=400)
    text = await _forward_to_backend(
        raw,
        file.filename or "upload.png",
        prompt.strip() or DEFAULT_PROMPT,
    )
    if not text:
        return PlainTextResponse("", status_code=502)
    return PlainTextResponse(text)


@app.post("/ocr/image")
async def ocr_image(
    file: UploadFile = File(...),
    prompt: str = Form(default=""),
) -> JSONResponse:
    raw = await file.read()
    text = await _forward_to_backend(
        raw,
        file.filename or "upload.png",
        prompt.strip() or DEFAULT_PROMPT,
    )
    return JSONResponse({"text": text, "result": text})
