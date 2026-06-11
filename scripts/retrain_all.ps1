# Полный цикл: YOLO зоны + Paddle rec + подсказки .env
# Запуск из корня репозитория:
#   .\scripts\retrain_all.ps1
#   .\scripts\retrain_all.ps1 -SkipYolo -SkipPaddle

param(
    [switch]$SkipYolo,
    [switch]$SkipPaddle,
    [int]$YoloEpochs = 60,
    [int]$PaddleEpochs = 12
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "=== 1/4 YOLO labels (geometry + refine) ===" -ForegroundColor Cyan
python scripts/regenerate_yolo_labels_geometry.py
if (-not $?) { exit 1 }
python scripts/refine_yolo_labels.py
if (-not $?) { exit 1 }

if (-not $SkipYolo) {
    Write-Host "=== 2/4 Обучение YOLO (resume best.pt) ===" -ForegroundColor Cyan
    python -m pip install -q ultralytics 2>$null
    python scripts/train_yolo_zones.py --epochs $YoloEpochs --resume --device auto --batch 4
    if (-not $?) { exit 1 }
} else {
    Write-Host "=== 2/4 YOLO пропущен (-SkipYolo) ===" -ForegroundColor Yellow
}

Write-Host "=== 3/4 Paddle line dataset ===" -ForegroundColor Cyan
python scripts/export_paddle_line_dataset.py
if (-not $?) { exit 1 }

if (-not $SkipPaddle) {
    Write-Host "=== 4/4 Paddle rec fine-tune (Docker) ===" -ForegroundColor Cyan
    $env:PADDLE_TRAIN_EPOCHS = "$PaddleEpochs"
    docker compose -f docker-compose.train.yml --profile train run --rm paddle-train
    if (-not $?) { exit 1 }
} else {
    Write-Host "=== 4/4 Paddle пропущен (-SkipPaddle) ===" -ForegroundColor Yellow
}

$YoloBest = Resolve-Path "data/training/yolo_zones/runs/train/weights/best.pt" -ErrorAction SilentlyContinue
$RecDir = "data/training/paddle_rec/models/rec_finetuned"

Write-Host ""
Write-Host "Готово. Добавьте в .env:" -ForegroundColor Green
Write-Host "  PDF_YOLO_ZONES=1"
if ($YoloBest) { Write-Host "  PDF_YOLO_MODEL=$YoloBest" }
Write-Host "  PADDLE_OCR_URL=http://paddle-ocr:8082"
Write-Host "  PDF_OCR_PADDLE_ZONES=1"
Write-Host "  PADDLE_REC_MODEL_DIR=/models/rec_finetuned"
Write-Host ""
Write-Host "Перезапуск (CPU):" -ForegroundColor Green
Write-Host "  docker compose -f docker-compose.yml -f docker-compose.paddle.yml -f docker-compose.ssd.yml --profile paddle up -d --build"
Write-Host "Перезапуск (GPU):" -ForegroundColor Green
Write-Host "  docker compose -f docker-compose.yml -f docker-compose.paddle.yml -f docker-compose.paddle.gpu.yml -f docker-compose.ssd.yml --profile paddle up -d --build"
