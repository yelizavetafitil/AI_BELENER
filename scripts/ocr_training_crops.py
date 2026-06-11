#!/usr/bin/env python3
"""
OCR по уже готовым PNG в data/training/crops → manifest + датасет для разметки/обучения.

  python scripts/ocr_training_crops.py
  python scripts/ocr_training_crops.py --zones spec_right stamp_frame --engine tesseract
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/app/data") if Path("/app/data").is_dir() else ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from belener.config import ocr_lang, ocr_psm_for_zone  # noqa: E402
from belener.ocr import finalize_ocr_text  # noqa: E402


def _ocr_tesseract(img: Image.Image, *, zone: str) -> str:
    import io
    import shutil
    import subprocess

    from belener.ocr import _preprocess_image, tessdata_dir

    if not shutil.which("tesseract") or not tessdata_dir():
        return ""
    img_p = _preprocess_image(img.convert("RGB"), zone=zone)
    buf = io.BytesIO()
    img_p.save(buf, format="PNG")
    proc = subprocess.run(
        [
            "tesseract",
            "stdin",
            "stdout",
            "-l",
            ocr_lang(),
            "--oem",
            "1",
            "--psm",
            str(ocr_psm_for_zone(zone)),
        ],
        input=buf.getvalue(),
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0:
        return ""
    return finalize_ocr_text(proc.stdout.decode("utf-8", errors="replace"))


def _ocr_pil(img: Image.Image, *, zone: str, engine: str) -> str:
    if engine in ("surya", "auto"):
        from belener.surya_ocr import ocr_pil_image, surya_ocr_enabled

        if surya_ocr_enabled():
            raw = ocr_pil_image(img, zone=zone, filename=f"{zone}.png")
            if raw:
                return finalize_ocr_text(raw)
    if engine in ("tesseract", "auto"):
        text = _ocr_tesseract(img, zone=zone)
        if text:
            return text
    return ""


def _paddle_escape(label: str) -> str:
    return label.replace("\t", " ").replace("\n", "\\n").strip()


def _infer_meta(png: Path, crops_root: Path) -> tuple[str, str, str]:
    rel = png.relative_to(crops_root)
    parts = rel.parts
    stem = parts[0] if parts else png.stem
    zone = png.stem
    return stem, zone, str(rel).replace("\\", "/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crops", type=Path, default=DATA_ROOT / "training" / "crops")
    ap.add_argument("--out", type=Path, default=DATA_ROOT / "training")
    ap.add_argument(
        "--zones",
        nargs="*",
        default=("spec_right", "spec_left", "stamp_frame", "tables_block"),
        help="Какие зоны OCR (по умолчанию таблицы+штамп)",
    )
    ap.add_argument(
        "--engine",
        choices=("tesseract", "surya", "auto"),
        default="tesseract",
        help="tesseract — быстро на CPU; surya — точнее, медленно",
    )
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not args.crops.is_dir():
        print("Нет папки кропов:", args.crops, file=sys.stderr)
        return 1

    zone_set = {z.casefold() for z in args.zones}
    pngs = sorted(args.crops.glob("*/*.png"))
    pngs = [p for p in pngs if p.stem.casefold() in zone_set]
    if args.limit:
        pngs = pngs[: args.limit]

    if not pngs:
        print("Нет PNG для зон", list(zone_set), file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    labels_dir = args.out / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    paddle_dir = args.out / "paddle_rec"
    paddle_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict] = []
    paddle_lines: list[str] = []

    for i, png in enumerate(pngs, 1):
        stem, zone, rel_img = _infer_meta(png, args.crops)
        print(f"[{i}/{len(pngs)}] {rel_img} …", flush=True)
        try:
            img = Image.open(png)
            text = _ocr_pil(img, zone=zone, engine=args.engine)
        except Exception as exc:
            print("  skip:", exc, file=sys.stderr)
            text = ""

        label_path = labels_dir / f"{stem}_{zone}.txt"
        label_path.write_text(text or "", encoding="utf-8")

        manifest_rows.append(
            {
                "file": stem,
                "zone": zone,
                "image": f"crops/{stem}/{zone}.png",
                "ocr_baseline": (text or "")[:12000],
                "label_file": str(label_path.relative_to(args.out)).replace("\\", "/"),
                "chars": len(text or ""),
            }
        )
        if text and len(text.strip()) >= 3:
            abs_img = png.resolve()
            paddle_lines.append(f"{abs_img}\t{_paddle_escape(text)}")

    manifest = args.out / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for row in manifest_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    train_list = paddle_dir / "train_list.txt"
    train_list.write_text("\n".join(paddle_lines) + ("\n" if paddle_lines else ""), encoding="utf-8")

    # README для человека
    readme = args.out / "README_TRAINING.txt"
    readme.write_text(
        """Датасет из кропов (авто-OCR, нужна ручная правка для обучения).

Файлы:
  manifest.jsonl     — все кропы + ocr_baseline
  labels/<stem>_<zone>.txt — текст по каждому кропу
  paddle_rec/train_list.txt — формат PaddleOCR rec (путь TAB текст)

ВАЖНО: для дообучения модели исправьте labels/*.txt в Label Studio/CVAT,
затем пересоберите train_list.txt. Обучение только на авто-OCR без правок
закрепляет ошибки.

Дообучение PaddleOCR (офлайн, отдельная машина):
  pip install paddlepaddle paddleocr
  см. https://github.com/PaddlePaddle/PaddleOCR/blob/main/doc/doc_en/recognition_en.md

YOLO зон: разметка bbox на полных страницах, классы spec_table, stamp, legend.
""",
        encoding="utf-8",
    )

    with_text = sum(1 for r in manifest_rows if (r.get("chars") or 0) > 20)
    print(f"\nГотово: {len(manifest_rows)} кропов, с текстом (>20 симв.): {with_text}")
    print(f"  {manifest}")
    print(f"  {train_list} ({len(paddle_lines)} строк)")
    print(f"  {readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
