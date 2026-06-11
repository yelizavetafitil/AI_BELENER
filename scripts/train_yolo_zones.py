#!/usr/bin/env python3
"""
Обучение YOLOv8 детектора зон (локально).

  pip install ultralytics
  python scripts/export_yolo_dataset.py --pdf-dir scan
  python scripts/train_yolo_zones.py --epochs 80

Модель: data/training/yolo_zones/runs/train/weights/best.pt
В .env: PDF_YOLO_ZONES=1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DS = ROOT / "data" / "training" / "yolo_zones"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DS / "dataset.yaml")
    ap.add_argument("--model", default="yolov8n.pt")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--device", default="auto", help="auto|cpu|0|cuda")
    ap.add_argument("--resume", action="store_true", help="дообучить с best.pt если есть")
    args = ap.parse_args()

    device = args.device
    if device == "auto":
        try:
            import torch

            device = "0" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    if not args.data.is_file():
        print("Нет", args.data, "— сначала export_yolo_dataset.py", file=sys.stderr)
        return 1

    try:
        from ultralytics import YOLO
    except ImportError:
        print("pip install ultralytics", file=sys.stderr)
        return 1

    runs_dir = args.data.parent / "runs"
    best_prev = runs_dir / "train" / "weights" / "best.pt"
    weights = str(best_prev) if args.resume and best_prev.is_file() else args.model
    model = YOLO(weights)
    model.train(
        data=str(args.data.resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        project=str(runs_dir),
        name="train",
        exist_ok=True,
        patience=25,
    )
    best = runs_dir / "train" / "weights" / "best.pt"
    print(f"\nГотово. В .env:\n  PDF_YOLO_ZONES=1\n  PDF_YOLO_MODEL={best.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
