#!/usr/bin/env python3
"""Проверка: готов ли проект к точному выводу (env, golden, surya)."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _warn(msg: str) -> None:
    print(f"  !!  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")


def main() -> int:
    print("Проверка пайплайна точности\n")
    fails = 0

    env_path = ROOT / ".env"
    if not env_path.is_file():
        _fail("нет .env — выполните: copy .env.accuracy.example .env")
        fails += 1
    else:
        _ok(".env найден")
        text = env_path.read_text(encoding="utf-8", errors="replace")
        for key, want in (
            ("PDF_REPORT_FAITHFUL", "1"),
            ("PDF_LOCAL_ONLY", "1"),
            ("PDF_VISION_MODE", "off"),
            ("PDF_REPORT_FULL_TEXT", "0"),
            ("PDF_CV_ZONE_REFINE", "1"),
            ("PDF_EXTRACT_MODE", "fast"),
            ("PDF_OCR_MULTIVIEW", "0"),
        ):
            if f"{key}={want}" in text.replace(" ", ""):
                _ok(f"{key}={want}")
            else:
                _warn(f"{key} не {want} в .env")
        if "PDF_OCR_ENGINE=tesseract" in text.replace(" ", ""):
            _ok("PDF_OCR_ENGINE=tesseract (быстро)")
        elif "PDF_OCR_ENGINE=surya" in text.replace(" ", ""):
            _warn("PDF_OCR_ENGINE=surya — медленнее; для сканов без слоя можно включить")

    golden = ROOT / "data" / "training" / "golden"
    gfiles = list(golden.glob("*.json")) if golden.is_dir() else []
    gfiles = [g for g in gfiles if g.name != "_template.json"]
    if len(gfiles) >= 2:
        _ok(f"golden: {len(gfiles)} эталон(ов)")
    else:
        _warn("golden < 2 — добавьте JSON в data/training/golden/")
    labels = list((ROOT / "data" / "training" / "labels").glob("*.txt"))
    if len(labels) >= 10:
        _ok(f"labels: {len(labels)} файлов")
    else:
        _warn(f"labels: {len(labels)} — цель 20+ (spec+stamp)")

    surya_url = os.environ.get("SURYA_OCR_URL", "http://localhost:8081")
    try:
        with urllib.request.urlopen(f"{surya_url.rstrip('/')}/health", timeout=5) as r:
            data = json.loads(r.read().decode())
        if data.get("status") == "ok":
            _ok(f"Surya {surya_url}")
        else:
            _warn(f"Surya ответ: {data}")
    except Exception as exc:
        _warn(f"Surya недоступен ({surya_url}): {exc}")

    paddle_url = os.environ.get("PADDLE_OCR_URL", "http://localhost:8082")
    try:
        with urllib.request.urlopen(f"{paddle_url.rstrip('/')}/health", timeout=8) as r:
            pdata = json.loads(r.read().decode())
        if pdata.get("status") == "ok":
            loaded = pdata.get("models_loaded")
            _ok(f"Paddle {paddle_url}" + (" (models loaded)" if loaded else " (loading)"))
        else:
            _warn(f"Paddle ответ: {pdata}")
    except Exception as exc:
        if "PDF_OCR_PADDLE_ZONES=1" in (env_path.read_text(encoding="utf-8") if env_path.is_file() else ""):
            _warn(f"Paddle недоступен ({paddle_url}): {exc}")
        else:
            _ok("Paddle не требуется (PDF_OCR_PADDLE_ZONES выкл)")

    train_list = ROOT / "data" / "training" / "paddle_rec" / "train_list.txt"
    if train_list.is_file():
        n = sum(1 for ln in train_list.read_text(encoding="utf-8").splitlines() if ln.strip())
        if n >= 25:
            _ok(f"paddle train_list: {n} строк")
        else:
            _warn(f"paddle train_list: {n} — цель 50+ после правки labels")
    yolo_best = ROOT / "data" / "training" / "yolo_zones" / "runs" / "detect" / "train" / "weights" / "best.pt"
    if yolo_best.is_file():
        _ok(f"YOLO model: {yolo_best.name}")
    elif env_path.is_file() and "PDF_YOLO_ZONES=1" in env_path.read_text(encoding="utf-8", errors="replace"):
        _warn("PDF_YOLO_ZONES=1 но нет best.pt — train_yolo_zones.py")

    if env_path.is_file():
        for key in ("PDF_REPORT_FAITHFUL=1", "PDF_LOCAL_ONLY=1", "PDF_VISION_MODE=off"):
            if key.replace(" ", "") not in text.replace(" ", ""):
                _warn(f"в .env нет {key.split('=')[0]}")
        _ok("проверка ключей .env (web читает env_file при старте)")

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        import belener.config as cfg  # noqa: WPS433

        for k, v in (
            ("PDF_REPORT_FAITHFUL", "1"),
            ("PDF_LOCAL_ONLY", "1"),
            ("PDF_VISION_MODE", "off"),
            ("PDF_REPORT_FULL_TEXT", "0"),
        ):
            os.environ[k] = v
        if cfg.report_faithful() and cfg.local_only_mode() and cfg.vision_mode() == "off":
            _ok("belener.config (с .env значениями)")
        if not cfg.report_include_full_text_layer():
            _ok("полный текстовый слой PDF выключен")
    except Exception as exc:
        _warn(f"belener.config: {exc}")

    print()
    if fails:
        print("Исправьте FAIL и перезапустите: docker compose … up -d --build")
        return 1
    print("Готово к работе. Веб: http://localhost:8090")
    print("Документация: docs/ACCURACY_PIPELINE.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
