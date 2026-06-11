# Подготовка данных этапа 1 (кропы + черновик labels) для всех PDF в scan/
param(
    [string]$ScanDir = "D:\AI_BELENER\scan"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "=== Кропы (без OCR) ===" -ForegroundColor Cyan
docker compose exec web python scripts/export_training_crops.py --dir /workspace/scan --out /app/data/training --no-ocr

Write-Host "=== OCR кропов (Surya) — долго ===" -ForegroundColor Cyan
docker compose exec web python scripts/ocr_training_crops.py --engine surya --zones spec_right spec_left stamp_frame legend_table

Write-Host "=== train_list + manifest ===" -ForegroundColor Cyan
python scripts/rebuild_train_list.py

Write-Host "=== Golden-тесты ===" -ForegroundColor Cyan
python scripts/validate_golden.py

Write-Host "=== Статус датасета ===" -ForegroundColor Cyan
python scripts/bootstrap_training_full.py --pdf-dir scan --skip-crops --skip-ocr

Write-Host "Готово. Правьте labels, затем: python scripts/rebuild_train_list.py ; python scripts/train_paddle_rec.py" -ForegroundColor Green
Write-Host "Paddle: copy .env.paddle.example .env + docker compose -f docker-compose.yml -f docker-compose.paddle.yml --profile paddle up -d" -ForegroundColor Green
