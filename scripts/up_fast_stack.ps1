# Поднять полный стек: web + surya + paddle + ollama (vision), всё на SSD G:
param(
    [switch]$PullVision,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

& "$PSScriptRoot\setup_ssd_cache.ps1"

$compose = @(
    "-f", "docker-compose.yml",
    "-f", "docker-compose.surya.yml",
    "-f", "docker-compose.ssd.yml",
    "-f", "docker-compose.fast.yml",
    "--profile", "surya",
    "--profile", "ollama"
)

Write-Host "=== Docker compose up (SSD + vision) ===" -ForegroundColor Cyan
if ($NoBuild) {
    docker compose @compose up -d
} else {
    docker compose @compose up -d --build
}

Write-Host ""
Write-Host "Waiting for Ollama..." -ForegroundColor Yellow
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { break }
    } catch { Start-Sleep -Seconds 3 }
}

$visionModel = "qwen2.5vl:7b"
if ($PullVision -or $env:PDF_VISION_MODEL) {
    if ($env:PDF_VISION_MODEL) { $visionModel = $env:PDF_VISION_MODEL }
    Write-Host "Pulling vision model: $visionModel (on SSD via Ollama volume)..." -ForegroundColor Cyan
    docker compose @compose exec -T ollama ollama pull $visionModel
}

Write-Host ""
Write-Host "=== Ready ===" -ForegroundColor Green
Write-Host "Web:    http://localhost:8090"
Write-Host "Ollama: http://localhost:11434"
Write-Host ""
Write-Host "PDF + question 'все gost'  -> vision qwen2.5vl (точность как PNG)"
Write-Host "PDF + full extract         -> fast OCR (рамка, таблицы, текст)"
Write-Host "Models/cache on: G:/BelenerCache"
