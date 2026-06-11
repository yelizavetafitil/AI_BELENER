# Полный цикл обучения (Windows): датасет → YOLO → Paddle rec → подсказка по перезапуску
param(
    [int]$YoloEpochs = 80,
    [int]$PaddleEpochs = 6,
    [string]$PdfDir = "scan"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "== rebuild_train_list =="
python scripts/rebuild_train_list.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== export_paddle_line_dataset =="
python scripts/export_paddle_line_dataset.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== export_yolo_dataset =="
python scripts/export_yolo_dataset.py --pdf-dir $PdfDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== train_yolo_zones ($YoloEpochs epochs) =="
python scripts/train_yolo_zones.py --epochs $YoloEpochs --device cpu --batch 2 --imgsz 1280
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== paddle-train docker (epochs=$PaddleEpochs) =="
$env:PADDLE_TRAIN_EPOCHS = "$PaddleEpochs"
docker compose -f docker-compose.train.yml --profile train build paddle-train
docker compose -f docker-compose.train.yml --profile train run --rm paddle-train
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Готово. Перезапуск веб-стека:"
Write-Host "  docker compose -f docker-compose.yml -f docker-compose.surya.yml --profile surya up -d --build"
Write-Host "Проверка: curl.exe -s http://localhost:8082/health"
