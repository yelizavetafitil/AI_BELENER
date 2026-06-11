#!/usr/bin/env python3
"""
Полный цикл подготовки датасета (локально):
  1) кропы из PDF
  2) черновик labels (OCR)
  3) train_list + manifest
  4) отчёт по целям: 50–100 spec_right, 30 stamp_frame

  python scripts/bootstrap_training_full.py --pdf-dir scan
  python scripts/bootstrap_training_full.py --pdf-dir . --glob "*.pdf" --engine tesseract
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("/app/data/training") if Path("/app/data/training").is_dir() else ROOT / "data" / "training"

TARGET_SPEC = 50
TARGET_STAMP = 30


def _run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def _count_labels() -> tuple[int, int, int]:
    labels = DATA / "labels"
    if not labels.is_dir():
        return 0, 0, 0
    spec = sum(1 for p in labels.glob("*_spec_right.txt"))
    stamp = sum(1 for p in labels.glob("*_stamp_frame.txt"))
    crops_spec = sum(1 for p in (DATA / "crops").glob("*/spec_right.png")) if (DATA / "crops").is_dir() else 0
    return spec, stamp, crops_spec


def _seed_missing_labels(engine: str) -> None:
    """Создать labels из OCR, если есть PNG но нет .txt."""
    py = sys.executable
    script = ROOT / "scripts" / "ocr_training_crops.py"
    if script.is_file():
        _run(
            [
                py,
                str(script),
                "--engine",
                engine,
                "--zones",
                "spec_right",
                "stamp_frame",
                "--out",
                str(DATA.parent / "training" if DATA.name == "training" else DATA),
            ]
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", type=Path, default=ROOT)
    ap.add_argument("--glob", default="*.pdf")
    ap.add_argument("--engine", choices=("tesseract", "surya"), default="tesseract")
    ap.add_argument("--skip-crops", action="store_true")
    ap.add_argument("--skip-ocr", action="store_true")
    ap.add_argument("--target-spec", type=int, default=TARGET_SPEC)
    ap.add_argument("--target-stamp", type=int, default=TARGET_STAMP)
    args = ap.parse_args()

    training_out = DATA
    py = sys.executable

    if not args.skip_crops:
        export = ROOT / "scripts" / "export_training_crops.py"
        pdf_dir = args.pdf_dir
        if not pdf_dir.is_absolute():
            pdf_dir = (ROOT / pdf_dir).resolve()
        code = _run(
            [
                py,
                str(export),
                "--dir",
                str(pdf_dir),
                "--glob",
                args.glob,
                "--out",
                str(training_out.parent if training_out.name == "training" else ROOT / "data"),
                "--no-ocr",
            ]
        )
        if code != 0:
            return code

    if not args.skip_ocr:
        ocr_script = ROOT / "scripts" / "ocr_training_crops.py"
        code = _run(
            [
                py,
                str(ocr_script),
                "--engine",
                args.engine,
                "--zones",
                "spec_right",
                "stamp_frame",
                "spec_left",
                "--out",
                str(training_out.parent if training_out.name == "training" else ROOT / "data"),
            ]
        )
        if code != 0:
            return code

    rebuild = ROOT / "scripts" / "rebuild_train_list.py"
    if rebuild.is_file():
        _run([py, str(rebuild), "--training", str(training_out)])

    spec, stamp, crops_spec = _count_labels()
    train_list = training_out / "paddle_rec" / "train_list.txt"
    n_train = 0
    if train_list.is_file():
        n_train = sum(1 for ln in train_list.read_text(encoding="utf-8").splitlines() if ln.strip())

    print()
    print("=== Статус датасета ===")
    print(f"  spec_right labels:  {spec} / цель {args.target_spec}")
    print(f"  stamp_frame labels: {stamp} / цель {args.target_stamp}")
    print(f"  spec_right crops:   {crops_spec}")
    print(f"  paddle train_list:  {n_train} строк")
    if spec < args.target_spec:
        print(f"  → Добавьте PDF в {args.pdf_dir} и повторите, либо правьте labels вручную.")
    if stamp < args.target_stamp:
        print(f"  → Нужно ещё ~{args.target_stamp - stamp} stamp_frame (разные листы BNP/VR).")
    if spec >= args.target_spec and stamp >= args.target_stamp:
        print("  → Цели по количеству достигнуты. Правьте текст в labels/*.txt, затем:")
        print("     python scripts/train_paddle_rec.py")
    else:
        print("  → После правки labels: python scripts/rebuild_train_list.py")
        print("     python scripts/train_paddle_rec.py")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
