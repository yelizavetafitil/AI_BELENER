#!/usr/bin/env python3
"""
Belener Surya-OCR — локальный сервис распознавания (CPU/GPU).

Модели кэшируются в /models (volume). После первой загрузки работает офлайн.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from PIL import Image

log = logging.getLogger("surya_ocr")
logging.basicConfig(level=logging.INFO)

_LOCK = threading.Lock()
_PREDICTORS: tuple[Any, Any] | None = None
_PRELOAD_ERROR: str = ""
_MODELS_LOADING: bool = False


def _max_side() -> int:
    try:
        return max(800, min(int(os.environ.get("SURYA_MAX_SIDE", "2048").strip()), 4096))
    except ValueError:
        return 2048


def _langs() -> list[str]:
    raw = (os.environ.get("SURYA_LANGS") or "ru,en").strip()
    return [x.strip() for x in raw.split(",") if x.strip()] or ["ru", "en"]


def _resize(img: Image.Image) -> Image.Image:
    w, h = img.size
    ms = _max_side()
    if max(w, h) <= ms:
        return img
    scale = ms / float(max(w, h))
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)


def _load_predictors() -> tuple[Any, Any]:
    global _PREDICTORS
    if _PREDICTORS is not None:
        return _PREDICTORS
    with _LOCK:
        if _PREDICTORS is not None:
            return _PREDICTORS
        log.info("Loading Surya models (device=%s)...", os.environ.get("SURYA_DEVICE", "cpu"))
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor

        foundation = FoundationPredictor()
        det = DetectionPredictor()
        rec = RecognitionPredictor(foundation)
        _PREDICTORS = (det, rec)
        log.info("Surya models ready")
        return _PREDICTORS


def _recognize(img: Image.Image) -> str:
    det, rec = _load_predictors()
    img = _resize(img.convert("RGB"))
    langs = _langs()
    kwargs: dict[str, Any] = {"det_predictor": det}
    try:
        preds = rec([img], langs=[langs], **kwargs)
    except TypeError:
        try:
            preds = rec([img], langs=langs, **kwargs)
        except TypeError:
            preds = rec([img], **kwargs)

    lines: list[str] = []
    for pred in preds or []:
        for tl in getattr(pred, "text_lines", []) or []:
            t = getattr(tl, "text", None)
            if t is None and isinstance(tl, dict):
                t = tl.get("text")
            if t and str(t).strip():
                lines.append(str(t).strip())
    return "\n".join(lines)


def _preload_in_background() -> None:
    global _PRELOAD_ERROR, _MODELS_LOADING
    _MODELS_LOADING = True
    try:
        _load_predictors()
        _PRELOAD_ERROR = ""
        log.info("Surya background preload finished")
    except Exception as exc:
        _PRELOAD_ERROR = str(exc)
        log.error("Surya preload failed (will retry on first request): %s", exc)
    finally:
        _MODELS_LOADING = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if (os.environ.get("SURYA_PRELOAD") or "1").strip().lower() in ("1", "true", "yes"):
        threading.Thread(target=_preload_in_background, daemon=True, name="surya-preload").start()
        log.info("Surya preload started in background (health stays available)")
    yield


app = FastAPI(title="Belener Surya-OCR", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    ready = _PREDICTORS is not None
    loading = _MODELS_LOADING and not ready
    return JSONResponse(
        {
            "status": "ok",
            "service": "surya_ocr",
            "models_loaded": ready,
            "models_loading": loading,
            "preload_error": _PRELOAD_ERROR or None,
            "device": os.environ.get("SURYA_DEVICE", "cpu"),
            "max_side": _max_side(),
            "langs": _langs(),
        }
    )


@app.post("/api/ocr")
async def api_ocr(
    file: UploadFile = File(...),
    zone: str = Form(default=""),
    mode: str = Form(default=""),
    prompt: str = Form(default=""),
) -> PlainTextResponse:
    raw = await file.read()
    if not raw:
        return PlainTextResponse("", status_code=400)
    try:
        img = Image.open(io.BytesIO(raw))
    except Exception:
        return PlainTextResponse("", status_code=400)
    try:
        text = _recognize(img)
    except Exception as exc:
        log.exception("Surya OCR failed zone=%s mode=%s", zone, mode or prompt)
        return PlainTextResponse(str(exc), status_code=500)
    return PlainTextResponse(text or "")


@app.post("/ocr/image")
async def ocr_image(
    file: UploadFile = File(...),
    zone: str = Form(default=""),
    mode: str = Form(default=""),
) -> JSONResponse:
    raw = await file.read()
    if not raw:
        return JSONResponse({"text": "", "error": "empty"}, status_code=400)
    img = Image.open(io.BytesIO(raw))
    text = _recognize(img)
    return JSONResponse({"text": text, "zone": zone, "mode": mode})


@app.post("/ocr/table")
async def ocr_table(file: UploadFile = File(...), zone: str = Form(default="spec")) -> JSONResponse:
    raw = await file.read()
    img = Image.open(io.BytesIO(raw))
    text = _recognize(img)
    # Табличный режим: пробуем TAB между группами пробелов в длинных строках
    out_lines: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if "\t" in s:
            out_lines.append(s)
        elif "  " in s:
            parts = [p.strip() for p in s.split("  ") if p.strip()]
            if len(parts) >= 2:
                out_lines.append("\t".join(parts))
                continue
        out_lines.append(s)
    return JSONResponse({"text": "\n".join(out_lines), "zone": zone, "mode": "table"})
