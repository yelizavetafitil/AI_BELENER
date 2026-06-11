#!/usr/bin/env python3
"""
Подготовка и запуск fine-tune PaddleOCR recognition (локально).

Требует: pip install paddlepaddle paddleocr
Опционально: клон PaddleOCR и PADDLEOCR_REPO=/path/to/PaddleOCR

  python scripts/rebuild_train_list.py
  python scripts/train_paddle_rec.py --min-lines 30
"""

from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("/app/data/training") if Path("/app/data/training").is_dir() else ROOT / "data" / "training"
PADDLE_REC = DATA / "paddle_rec"


def _parse_train_line(line: str, training: Path) -> tuple[Path, str] | None:
    line = line.strip()
    if not line or "\t" not in line:
        return None
    path_s, label = line.split("\t", 1)
    p = Path(path_s)
    if not p.is_file():
        for base in (training, DATA, training.parent):
            cand = base / path_s
            if cand.is_file():
                p = cand
                break
    if not p.is_file():
        return None
    return p, label.replace("\\n", "\n")


def _prepare_split(
    train_list: Path,
    out_dir: Path,
    val_ratio: float,
    *,
    training_root: Path,
) -> tuple[Path, Path]:
    lines = [ln for ln in train_list.read_text(encoding="utf-8").splitlines() if ln.strip()]
    pairs: list[tuple[Path, str]] = []
    for ln in lines:
        parsed = _parse_train_line(ln, training_root)
        if parsed:
            pairs.append(parsed)
    if not pairs:
        raise SystemExit("train_list.txt пуст — сначала labels и rebuild_train_list.py")

    random.seed(42)
    random.shuffle(pairs)
    n_val = max(1, int(len(pairs) * val_ratio))
    val_pairs = pairs[:n_val]
    train_pairs = pairs[n_val:]

    img_train = out_dir / "images" / "train"
    img_val = out_dir / "images" / "val"
    img_train.mkdir(parents=True, exist_ok=True)
    img_val.mkdir(parents=True, exist_ok=True)

    def _write(split_pairs: list[tuple[Path, str]], img_dir: Path, list_path: Path) -> None:
        rows: list[str] = []
        for i, (src, label) in enumerate(split_pairs):
            dst = img_dir / f"{i:05d}{src.suffix.lower() or '.png'}"
            if not dst.is_file():
                shutil.copy2(src, dst)
            esc = label.replace("\t", " ").replace("\n", "\\n")
            rows.append(f"{dst.resolve()}\t{esc}")
        list_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    train_txt = out_dir / "train_list.txt"
    val_txt = out_dir / "val_list.txt"
    _write(train_pairs, img_train, train_txt)
    _write(val_pairs, img_val, val_txt)
    return train_txt, val_txt


def _find_pretrained_rec(training: Path, *, fresh: bool = False) -> str:
    import os

    env = (os.environ.get("PADDLE_REC_PRETRAINED") or "").strip()
    if env:
        p = Path(env)
        if p.is_file() and p.suffix == ".pdparams":
            return str(p.with_suffix(""))
        if p.is_dir() or (p.with_suffix(".pdparams")).is_file():
            return str(p if p.is_dir() else p.with_suffix(""))

    if not fresh:
        export = training / "paddle_rec" / "finetune" / "export"
        for name in ("best_accuracy", "iter_epoch_15", "iter_epoch_12", "iter_epoch_6"):
            stem = export / name
            if (export / f"{name}.pdparams").is_file():
                return str(stem)

    for base in (
        Path("/pretrain/cyrillic_PP-OCRv3_rec_train/best_accuracy"),
        Path("/pretrain/cyrillic_PP-OCRv3_rec_train/student"),
        Path("/pretrain/cyrillic_PP-OCRv3_rec_train/latest"),
    ):
        if base.with_suffix(".pdparams").is_file():
            return str(base)
    return ""


def _pick_rec_config(repo: Path) -> Path:
    for rel in (
        "configs/rec/PP-OCRv3/multi_language/cyrillic_PP-OCRv3_rec.yml",
        "configs/rec/multi_language/rec_cyrillic_lite_train.yml",
        "configs/rec/PP-OCRv4/en_PP-OCRv4_rec.yml",
    ):
        p = repo / rel
        if p.is_file():
            return p
    return Path()


def _paddle_use_gpu() -> bool:
    import os

    raw = (os.environ.get("PADDLE_USE_GPU") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _try_run_paddle_train(out_dir: Path, epochs: int, *, training_root: Path, fresh: bool = False) -> int:
    import os

    repo = Path((os.environ.get("PADDLEOCR_REPO") or "").strip())
    if not repo.is_dir():
        print(
            "\nАвто-обучение: задайте PADDLEOCR_REPO=путь/к/PaddleOCR (git clone https://github.com/PaddlePaddle/PaddleOCR)\n"
            "Или: docker compose -f docker-compose.train.yml run --rm paddle-train\n",
            file=sys.stderr,
        )
        return 1

    config = _pick_rec_config(repo)
    if not config.is_file():
        print("Не найден yaml конфиг в PaddleOCR configs/rec/", file=sys.stderr)
        return 1

    export_model = out_dir / "export"
    if fresh and export_model.exists():
        shutil.rmtree(export_model, ignore_errors=True)
    export_model.mkdir(parents=True, exist_ok=True)

    max_text = int(os.environ.get("PADDLE_REC_MAX_TEXT_LEN", "80"))
    lr = os.environ.get("PADDLE_REC_LR", "0.0005")
    cmd = [
        sys.executable,
        str(repo / "tools" / "train.py"),
        "-c",
        str(config),
        "-o",
        f"Global.epoch_num={epochs}",
        "Global.print_batch_step=10",
        f"Global.max_text_length={max_text}",
        f"Train.dataset.label_file_list=[{out_dir / 'train_list.txt'}]",
        f"Eval.dataset.label_file_list=[{out_dir / 'val_list.txt'}]",
        f"Train.dataset.data_dir={out_dir / 'images' / 'train'}",
        f"Eval.dataset.data_dir={out_dir / 'images' / 'val'}",
        f"Global.save_model_dir={export_model}",
        f"Global.use_gpu={'true' if _paddle_use_gpu() else 'false'}",
        f"Train.loader.batch_size_per_card={16 if _paddle_use_gpu() else 4}",
        f"Eval.loader.batch_size_per_card={16 if _paddle_use_gpu() else 4}",
        "Train.loader.num_workers=2",
        "Eval.loader.num_workers=2",
        f"Optimizer.lr.learning_rate={lr}",
    ]
    pretrained = _find_pretrained_rec(training_root, fresh=fresh)
    if pretrained:
        cmd.append(f"Global.pretrained_model={pretrained}")
        print(f"pretrained: {pretrained}")
    else:
        print("WARN: pretrained_model не найден — обучение с нуля", file=sys.stderr)
    print("config:", config)
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(repo))


def _export_inference(repo: Path, out_dir: Path, deploy_dir: Path) -> int:
    config = _pick_rec_config(repo)
    if not config.is_file():
        return 1
    export_dir = out_dir / "export"
    ckpt: Path | None = None
    for stem in ("best_accuracy", "latest", "best_model"):
        if (export_dir / f"{stem}.pdparams").is_file():
            ckpt = export_dir / stem
            break
        if (export_dir / stem).is_dir():
            ckpt = export_dir / stem
            break
    if ckpt is None:
        print("Нет чекпоинта в", export_dir, file=sys.stderr)
        return 1
    deploy_dir.mkdir(parents=True, exist_ok=True)
    dict_path = repo / "ppocr" / "utils" / "dict" / "cyrillic_dict.txt"
    cmd = [
        sys.executable,
        str(repo / "tools" / "export_model.py"),
        "-c",
        str(config),
        "-o",
        f"Global.character_dict_path={dict_path}",
        f"Global.pretrained_model={ckpt}",
        f"Global.save_inference_dir={deploy_dir}",
    ]
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(repo))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training", type=Path, default=DATA)
    ap.add_argument("--min-lines", type=int, default=25, help="минимум строк в train_list")
    ap.add_argument("--val-ratio", type=float, default=0.1)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--run-train", action="store_true", help="запустить tools/train.py в PADDLEOCR_REPO")
    ap.add_argument("--export", action="store_true", help="export inference в paddle_rec/models/rec_finetuned")
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="не брать чекпоинты прошлого прогона; только официальный pretrained",
    )
    ap.add_argument(
        "--use-lines",
        action="store_true",
        default=None,
        help="line_train_list.txt (после export_paddle_line_dataset.py)",
    )
    args = ap.parse_args()

    line_list = args.training / "paddle_rec" / "line_train_list.txt"
    zone_list = args.training / "paddle_rec" / "train_list.txt"
    use_lines = args.use_lines
    if use_lines is None:
        use_lines = line_list.is_file() and sum(
            1 for ln in line_list.read_text(encoding="utf-8").splitlines() if ln.strip()
        ) >= max(20, args.min_lines // 2)
    train_list = line_list if use_lines and line_list.is_file() else zone_list
    if not train_list.is_file():
        print("Нет train_list — rebuild_train_list.py и export_paddle_line_dataset.py", file=sys.stderr)
        return 1

    n = sum(1 for ln in train_list.read_text(encoding="utf-8").splitlines() if ln.strip())
    min_need = max(15, args.min_lines // 2) if use_lines else args.min_lines
    if n < min_need:
        print(f"Мало данных: {n} < {min_need}. Добавьте labels / line export", file=sys.stderr)
        return 1
    print(f"Источник: {train_list.name} ({n} строк)")

    work = args.training / "paddle_rec" / "finetune"
    work.mkdir(parents=True, exist_ok=True)
    for sub in ("images",):
        shutil.rmtree(work / sub, ignore_errors=True)

    tr, va = _prepare_split(train_list, work, args.val_ratio, training_root=args.training)
    print(f"Подготовлено: train={tr} val={va}")

    # Симлинк для Docker paddle-ocr
    best_hint = work / "export" / "best_accuracy"
    link_readme = work / "README_DEPLOY.txt"
    link_readme.write_text(
        f"""После обучения скопируйте inference-модель rec в:
  data/training/paddle_rec/models/rec_finetuned/
или смонтируйте в Docker:
  PADDLE_REC_MODEL_DIR=/models/rec_finetuned

В .env:
  PADDLE_OCR_URL=http://paddle-ocr:8082
  PDF_OCR_PADDLE_ZONES=1
  PDF_OCR_ENGINE=tesseract
""",
        encoding="utf-8",
    )
    print(link_readme)

    deploy = args.training / "paddle_rec" / "models" / "rec_finetuned"

    if args.run_train:
        rc = _try_run_paddle_train(work, args.epochs, training_root=args.training, fresh=args.fresh)
        if rc != 0:
            return rc
        if args.export:
            import os

            repo = Path((os.environ.get("PADDLEOCR_REPO") or "").strip())
            if deploy.exists():
                shutil.rmtree(deploy, ignore_errors=True)
            return _export_inference(repo, work, deploy)
        return 0

    if args.export:
        import os

        repo = Path((os.environ.get("PADDLEOCR_REPO") or "").strip())
        if not repo.is_dir():
            print("PADDLEOCR_REPO не задан", file=sys.stderr)
            return 1
        if deploy.exists():
            shutil.rmtree(deploy, ignore_errors=True)
        return _export_inference(repo, work, deploy)

    print("\nДатасет готов. Для обучения:")
    print("  set PADDLEOCR_REPO=C:\\path\\PaddleOCR")
    print("  python scripts/train_paddle_rec.py --run-train --epochs 50")
    print("\nИли в Docker (профиль paddle-train в docker-compose.paddle.yml)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
