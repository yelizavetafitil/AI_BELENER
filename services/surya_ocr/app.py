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


def _require_gpu() -> bool:
    return (os.environ.get("SURYA_REQUIRE_GPU") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _gpu_info() -> dict[str, Any]:
    try:
        import torch

        if not torch.cuda.is_available():
            return {"cuda": False}
        idx = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(idx)
        return {
            "cuda": True,
            "name": torch.cuda.get_device_name(idx),
            "capability": f"{props.major}.{props.minor}",
            "vram_gb": round(props.total_memory / (1024**3), 1),
            "torch_cuda": torch.version.cuda,
        }
    except Exception as exc:
        return {"cuda": False, "error": str(exc)}


def _torch_device():
    """auto: CUDA если есть, иначе CPU. Явно: cuda / cpu. SURYA_REQUIRE_GPU=1 — без fallback."""
    import torch

    want = (os.environ.get("SURYA_DEVICE") or "auto").strip().lower()
    cuda_ok = bool(torch.cuda.is_available())
    info = _gpu_info()
    if cuda_ok:
        log.info(
            "CUDA ok: %s capability=%s vram=%sG torch_cuda=%s",
            info.get("name"),
            info.get("capability"),
            info.get("vram_gb"),
            info.get("torch_cuda"),
        )

    def _cuda_or_fail(label: str):
        if cuda_ok:
            return torch.device("cuda:0" if label in ("cuda", "gpu", "0", "auto", "") else label)
        msg = (
            f"SURYA_DEVICE={want}, CUDA недоступна. "
            "Нужен образ Dockerfile.gpu (PyTorch cu128) и nvidia-container-toolkit."
        )
        if _require_gpu():
            raise RuntimeError(msg)
        log.warning("%s — fallback CPU", msg)
        return torch.device("cpu")

    if want in ("auto", ""):
        return _cuda_or_fail("auto")
    if want in ("cuda", "gpu", "0"):
        return _cuda_or_fail("cuda")
    if want.startswith("cuda"):
        return _cuda_or_fail(want)
    return torch.device("cpu")


def _move_predictor_to_device(predictor: Any, device) -> None:
    for attr in ("model", "detector", "foundation"):
        obj = getattr(predictor, attr, None)
        if obj is not None and hasattr(obj, "to"):
            try:
                obj.to(device)
            except Exception:
                log.debug("predictor.%s.to failed", attr, exc_info=True)
    to_fn = getattr(predictor, "to", None)
    if callable(to_fn):
        try:
            to_fn(device)
        except Exception:
            log.debug("predictor.to failed", exc_info=True)


def _load_predictors() -> tuple[Any, Any]:
    global _PREDICTORS
    if _PREDICTORS is not None:
        return _PREDICTORS
    with _LOCK:
        if _PREDICTORS is not None:
            return _PREDICTORS
        import torch

        device = _torch_device()
        if device.type == "cuda":
            torch.set_default_device(device)
            torch.cuda.set_device(device)
        log.info("Loading Surya models (device=%s)...", device)
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor

        # device= в конструкторах (новые версии Surya); иначе .to() ниже
        try:
            foundation = FoundationPredictor(device=device)
            det = DetectionPredictor(device=device)
            rec = RecognitionPredictor(foundation, device=device)
        except TypeError:
            foundation = FoundationPredictor()
            det = DetectionPredictor()
            rec = RecognitionPredictor(foundation)
            for p in (foundation, det, rec):
                _move_predictor_to_device(p, device)
        else:
            for p in (foundation, det, rec):
                _move_predictor_to_device(p, device)

        if device.type == "cuda":
            # Smoke: один тензор на GPU — сразу видно, если sm_120 не в сборке
            torch.zeros(1, device=device)
            log.info("GPU smoke OK on %s", torch.cuda.get_device_name(0))

        _PREDICTORS = (det, rec)
        log.info("Surya models ready on %s", device)
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
    device_s = "unknown"
    try:
        device_s = str(_torch_device())
    except Exception as exc:
        device_s = f"error:{exc}"
    return JSONResponse(
        {
            "status": "ok" if not (_require_gpu() and "error:" in device_s) else "degraded",
            "service": "surya_ocr",
            "models_loaded": ready,
            "models_loading": loading,
            "preload_error": _PRELOAD_ERROR or None,
            "device": device_s,
            "gpu": _gpu_info(),
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
