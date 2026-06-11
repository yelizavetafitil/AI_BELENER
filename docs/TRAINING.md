# Обучение OCR и зон (локально, без облака)

## Цели датасета

| Зона | Минимум кропов | Файлы |
|------|----------------|--------|
| `spec_right` | **50–100** | `labels/<stem>_spec_right.txt` + `crops/<stem>/spec_right.png` |
| `stamp_frame` | **30** | `labels/<stem>_stamp_frame.txt` + `crops/<stem>/stamp_frame.png` |

## Быстрый старт (одной командой)

```powershell
cd D:\AI_BELENER
python scripts/bootstrap_training_full.py --pdf-dir scan
```

Шаги внутри: кропы → OCR-черновик в `labels/` → `manifest.jsonl` + `paddle_rec/train_list.txt`.

**Правка:** откройте PNG в `data/training/crops/` и исправьте текст в `data/training/labels/*.txt` (эталон с листа).

```powershell
python scripts/rebuild_train_list.py
```

---

## 1. Кропы

```powershell
docker compose exec web python scripts/export_training_crops.py --dir /workspace/scan --out /app/data/training --no-ocr
```

## 2. Черновик OCR

```powershell
docker compose exec web python scripts/ocr_training_crops.py --engine tesseract --zones spec_right stamp_frame spec_left
```

## 3. Fine-tune PaddleOCR rec

Строковый датасет из golden labels (рекомендуется):

```powershell
python scripts/rebuild_train_list.py
python scripts/export_paddle_line_dataset.py
```

Docker (всё в одном, ~1–3 ч CPU):

```powershell
docker compose -f docker-compose.train.yml --profile train build paddle-train
docker compose -f docker-compose.train.yml --profile train run --rm paddle-train
```

Или полный цикл (YOLO + Paddle):

```powershell
.\scripts\train_all.ps1
```

Локально с клоном [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR):

```powershell
$env:PADDLEOCR_REPO = "C:\PaddleOCR"
python scripts/train_paddle_rec.py --run-train --export --epochs 35
```

Скопируйте inference-модель `rec` в:

`data/training/paddle_rec/models/rec_finetuned/`

(файлы `inference.pdiparams`, `inference.pdmodel` или структура PaddleOCR export).

## 4. HTTP Paddle для зон spec/stamp

```powershell
copy .env.paddle.example .env
docker compose -f docker-compose.yml -f docker-compose.paddle.yml --profile paddle up -d --build
```

В `.env`:

- `PADDLE_OCR_URL=http://paddle-ocr:8082`
- `PDF_OCR_PADDLE_ZONES=1` — Paddle **только** для `spec_*` и `stamp_*`
- `PDF_OCR_ENGINE=tesseract` — остальные зоны без изменений

Проверка: `curl http://localhost:8082/health`

## 5. YOLO зон (опционально)

Псевдо-разметка из геометрии discover (уточните в CVAT при необходимости):

```powershell
python scripts/export_yolo_dataset.py --pdf-dir scan
pip install ultralytics
python scripts/train_yolo_zones.py --epochs 80 --device cpu
```

В `.env`:

```env
PDF_YOLO_ZONES=1
PDF_YOLO_MODEL=data/training/yolo_zones/runs/detect/train/weights/best.pt
```

Классы: `spec_table`, `stamp`, `legend`.

## 6. Проверка

```powershell
python scripts/check_accuracy_setup.py
python scripts/validate_golden.py
docker compose exec web python scripts/benchmark_corpus.py --dir /workspace/scan
```

---

Пока нет fine-tune: `PDF_OCR_ENGINE=surya` или Tesseract + правильные `labels` для парсера.
