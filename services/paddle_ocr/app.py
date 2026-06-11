#!/usr/bin/env python3
"""Локальный PaddleOCR: det+rec для кропов зон (русский, опционально fine-tuned rec)."""

from __future__ import annotations

import io
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Form, UploadFile
from typing import Annotated
from fastapi.responses import JSONResponse, PlainTextResponse
from PIL import Image

log = logging.getLogger("paddle_ocr")
logging.basicConfig(level=logging.INFO)

_LOCK = threading.Lock()
_OCR: Any = None
_PRELOAD_ERROR = ""
_LOADING = False


def _rec_model_dir() -> str:
    return (os.environ.get("PADDLE_REC_MODEL_DIR") or "").strip()


def _det_model_dir() -> str:
    return (os.environ.get("PADDLE_DET_MODEL_DIR") or "").strip()


def _lang() -> str:
    return (os.environ.get("PADDLE_LANG") or "ru").strip() or "ru"


def _use_gpu() -> bool:
    raw = (os.environ.get("PADDLE_USE_GPU") or os.environ.get("PDF_PADDLE_GPU") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    try:
        import paddle

        return bool(paddle.device.is_compiled_with_cuda()) and paddle.device.cuda.device_count() > 0
    except Exception:
        return False


def _setup_model_dirs() -> None:
    """Кэш моделей только в writable volume /models/paddle (не read-only mount)."""
    from pathlib import Path

    base = Path(os.environ.get("PADDLEOCR_HOME") or "/models/paddle")
    base.mkdir(parents=True, exist_ok=True)
    os.environ["PADDLEOCR_HOME"] = str(base)
    os.environ.setdefault("PADDLE_HOME", str(base))


def _load_ocr() -> Any:
    global _OCR
    if _OCR is not None:
        return _OCR
    with _LOCK:
        if _OCR is not None:
            return _OCR
        _setup_model_dirs()
        from paddleocr import PaddleOCR

        use_gpu = _use_gpu()
        kwargs: dict[str, Any] = {
            "use_angle_cls": True,
            "lang": _lang(),
            "use_gpu": use_gpu,
            "show_log": False,
        }
        if use_gpu:
            try:
                import paddle

                paddle.device.set_device("gpu:0")
            except Exception:
                log.warning("GPU requested but paddle.device.set_device failed", exc_info=True)
        from pathlib import Path

        rec = _rec_model_dir()
        det = _det_model_dir()
        if rec:
            rp = Path(rec)
            if rp.is_dir() and any(rp.iterdir()):
                kwargs["rec_model_dir"] = str(rp)
            else:
                log.warning("PADDLE_REC_MODEL_DIR=%s пуст — базовая модель ru", rec)
        if det and Path(det).is_dir() and any(Path(det).iterdir()):
            kwargs["det_model_dir"] = det
        log.info(
            "Loading PaddleOCR lang=%s rec=%s det=%s gpu=%s",
            _lang(),
            rec or "default",
            det or "default",
            use_gpu,
        )
        _OCR = PaddleOCR(**kwargs)
        log.info("PaddleOCR ready")
        return _OCR


def _lines_from_result(result: Any) -> list[str]:
    if not result:
        return []
    lines: list[str] = []
    block = result[0] if isinstance(result, list) and result else result
    if not block:
        return []
    for item in block:
        if not item or len(item) < 2:
            continue
        text_part = item[1]
        if isinstance(text_part, (list, tuple)) and text_part:
            t = str(text_part[0] or "").strip()
        elif isinstance(text_part, str):
            t = text_part.strip()
        else:
            continue
        if t:
            lines.append(t)
    return lines


def _recognize(img: Image.Image) -> str:
    ocr = _load_ocr()
    rgb = img.convert("RGB")
    arr = np.asarray(rgb)
    try:
        result = ocr.ocr(arr, cls=True)
    except TypeError:
        result = ocr.ocr(arr)
    return "\n".join(_lines_from_result(result))


def _preload() -> None:
    global _PRELOAD_ERROR, _LOADING
    _LOADING = True
    try:
        _load_ocr()
        _PRELOAD_ERROR = ""
    except Exception as exc:
        _PRELOAD_ERROR = str(exc)
        log.exception("PaddleOCR preload failed")
    finally:
        _LOADING = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if (os.environ.get("PADDLE_PRELOAD") or "1").strip().lower() in ("1", "true", "yes"):
        threading.Thread(target=_preload, daemon=True, name="paddle-preload").start()
    yield


app = FastAPI(title="Belener PaddleOCR", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    ready = _OCR is not None and not _PRELOAD_ERROR
    return JSONResponse(
        {
            "status": "ok" if ready else "degraded",
            "service": "paddle_ocr",
            "models_loaded": ready,
            "models_loading": _LOADING and not ready,
            "preload_error": _PRELOAD_ERROR or None,
            "lang": _lang(),
            "rec_model_dir": _rec_model_dir() or None,
            "gpu": _use_gpu(),
        }
    )


@app.post("/api/ocr")
async def api_ocr(
    file: UploadFile = File(...),
    zone: str = Form(default=""),
    mode: str = Form(default=""),
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
        log.exception("Paddle OCR failed zone=%s", zone)
        return PlainTextResponse(str(exc), status_code=500)
    return PlainTextResponse(text or "")


@app.post("/api/ocr/batch")
async def api_ocr_batch(
    files: Annotated[list[UploadFile], File(...)],
    zone: str = Form(default=""),
    mode: str = Form(default=""),
) -> JSONResponse:
    texts: list[str] = []
    for upload in files:
        raw = await upload.read()
        if not raw:
            texts.append("")
            continue
        try:
            img = Image.open(io.BytesIO(raw))
            texts.append(_recognize(img))
        except Exception:
            log.exception("Paddle batch OCR failed zone=%s", zone)
            texts.append("")
    return JSONResponse({"texts": texts, "zone": zone, "count": len(texts)})


@app.post("/ocr/image")
async def ocr_image(
    file: UploadFile = File(...),
    zone: str = Form(default=""),
) -> JSONResponse:
    raw = await file.read()
    if not raw:
        return JSONResponse({"text": "", "error": "empty"}, status_code=400)
    try:
        img = Image.open(io.BytesIO(raw))
        text = _recognize(img)
    except Exception as exc:
        log.exception("Paddle OCR failed zone=%s", zone)
        return JSONResponse({"text": "", "error": str(exc)}, status_code=500)
    return JSONResponse({"text": text, "zone": zone})
