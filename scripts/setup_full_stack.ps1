# Surya + Paddle + подготовка YOLO (без долгого extract PDF)
param(
    [switch]$SkipYoloExport,
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not (Test-Path .env)) {
    Copy-Item .env.full.example .env
    Write-Host "Создан .env из .env.full.example" -ForegroundColor Green
}

Write-Host "=== Docker: web + surya + paddle ===" -ForegroundColor Cyan
docker compose -f docker-compose.yml -f docker-compose.surya.yml --profile surya up -d --build

if ($BuildOnly) { exit 0 }

if (-not $SkipYoloExport) {
    Write-Host "=== YOLO dataset (псевдо-разметка из scan/) ===" -ForegroundColor Cyan
    python scripts/export_yolo_dataset.py --pdf-dir scan
    Write-Host "Обучение YOLO (долго, отдельно): python scripts/train_yolo_zones.py --epochs 40" -ForegroundColor Yellow
}

Write-Host "=== check ===" -ForegroundColor Cyan
python scripts/check_accuracy_setup.py
Write-Host "`nВеб: http://localhost:8090" -ForegroundColor Green
Write-Host "Paddle health: http://localhost:8082/health" -ForegroundColor Green
Write-Host "Surya health:  http://localhost:8081/health" -ForegroundColor Green
