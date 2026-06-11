# Дообучение YOLO + Paddle rec

## Одной командой (Windows)

```powershell
.\scripts\retrain_all.ps1
```

Шаги: геометрия YOLO-labels → refine → YOLOv8n (resume) → line dataset → Paddle rec в Docker.

## По отдельности

### YOLO зоны (spec / stamp / legend)

```powershell
python scripts/regenerate_yolo_labels_geometry.py
python scripts/refine_yolo_labels.py
python scripts/train_yolo_zones.py --epochs 60 --resume --device auto
```

Модель: `data/training/yolo_zones/runs/train/weights/best.pt`

В `.env`:

```
PDF_YOLO_ZONES=1
PDF_YOLO_MODEL=/app/data/training/yolo_zones/runs/train/weights/best.pt
```

### Paddle recognition (строки эталонов)

```powershell
python scripts/export_paddle_line_dataset.py
docker compose -f docker-compose.train.yml --profile train run --rm paddle-train
```

Модель: `data/training/paddle_rec/models/rec_finetuned/` (уже смонтирована в `paddle-ocr`).

### Paddle GPU (NVIDIA + WSL2/Linux)

```powershell
docker compose -f docker-compose.yml -f docker-compose.paddle.yml -f docker-compose.paddle.gpu.yml -f docker-compose.ssd.yml --profile paddle up -d --build
```

Проверка: `curl http://localhost:8082/health` → `"gpu": true`

## Перезапуск веб-стека

```powershell
docker compose -f docker-compose.yml -f docker-compose.paddle.yml -f docker-compose.ssd.yml --profile paddle up -d --build web
```
