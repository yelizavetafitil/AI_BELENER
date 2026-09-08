# OCR-стек: любая NVIDIA → GPU, иначе CPU Surya. Без привязки к модели карты.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".env")) {
  if (Test-Path ".env.gpu.example") {
    Copy-Item ".env.gpu.example" ".env"
    Write-Host "Created .env from .env.gpu.example"
  } elseif (Test-Path ".env.ocr.example") {
    Copy-Item ".env.ocr.example" ".env"
    Write-Host "Created .env from .env.ocr.example"
  } else {
    throw "No .env — copy .env.gpu.example to .env first"
  }
}

$gpu = $false
$gpuName = ""
try {
  $null = Get-Command nvidia-smi -ErrorAction Stop
  & nvidia-smi | Out-Null
  if ($LASTEXITCODE -eq 0) {
    $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
    docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $gpu = $true }
    else {
      docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0) { $gpu = $true }
    }
  }
} catch {
  $gpu = $false
}

if ($gpu) {
  Write-Host ">>> NVIDIA GPU detected: $gpuName — Surya on CUDA"
  docker compose `
    -f docker-compose.yml `
    -f docker-compose.surya.yml `
    -f docker-compose.surya.gpu.yml `
    --profile surya up -d --build
} else {
  Write-Host ">>> No usable NVIDIA GPU — Surya on CPU (still offloads web)"
  docker compose `
    -f docker-compose.yml `
    -f docker-compose.surya.yml `
    --profile surya up -d --build
}

Write-Host ">>> Waiting for Surya health..."
for ($i = 1; $i -le 90; $i++) {
  try {
    $h = Invoke-WebRequest -Uri "http://127.0.0.1:8081/health" -UseBasicParsing -TimeoutSec 5
    if ($h.StatusCode -eq 200) {
      Write-Host $h.Content
      Write-Host "OK: OCR stack is up. Web: http://127.0.0.1:8090"
      Write-Host "Watch GPU: nvidia-smi -l 1"
      exit 0
    }
  } catch {}
  Start-Sleep -Seconds 5
}
Write-Host "WARN: Surya not healthy yet. Check: docker compose logs -f surya-ocr"
