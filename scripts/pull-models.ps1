# Скачать рекомендуемые модели Ollama для БелнипиAI.
# Запуск из корня проекта:
#   .\scripts\pull-models.ps1
#   .\scripts\pull-models.ps1 -Quality
#
# Нужен запущенный контейнер: docker compose up -d

param(
    [switch]$Quality
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Invoke-OllamaPull([string]$tag) {
    Write-Host "`n>>> pull $tag" -ForegroundColor Cyan
    docker compose exec ollama ollama pull $tag
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Не удалось скачать $tag (проверьте имя: ollama.com/library)"
    }
}

# Базовый набор (~15–22 GiB на диске; в RAM одновременно держится 1 модель)
$base = @(
    "qwen2.5vl:7b",   # чертёж PDF / JPG — основная
    "gemma4:e4b",     # DOCX, Excel, быстрый чат
    "gemma4:26b"      # сложные текстовые вопросы
)

# Дополнительно: максимальное качество vision (нужно ≥24 GiB RAM Docker)
$extra = @(
    "qwen2.5vl:32b"        # 21 GB — максимум качества (нужен RAM ≥24 GiB Docker)
)

Write-Host "БелнипиAI — загрузка моделей в контейнер ollama" -ForegroundColor Green
docker compose ps ollama 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Сначала: docker compose up -d" -ForegroundColor Yellow
    exit 1
}

foreach ($m in $base) { Invoke-OllamaPull $m }

if ($Quality) {
    Write-Host "`n--- Режим -Quality: дополнительные vision-модели ---" -ForegroundColor Magenta
    foreach ($m in $extra) { Invoke-OllamaPull $m }
} else {
    Write-Host "`nДля 32B vision: .\scripts\pull-models.ps1 -Quality" -ForegroundColor DarkGray
}

Write-Host "`nГотово. Список:" -ForegroundColor Green
docker compose exec ollama ollama list
Write-Host "`nВ .env по умолчанию: MODEL_DEFAULT=qwen2.5vl:7b" -ForegroundColor DarkGray
