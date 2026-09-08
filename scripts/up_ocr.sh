#!/usr/bin/env bash
# OCR-стек: любая NVIDIA → GPU (cu128), иначе CPU Surya. Без привязки к модели карты.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  if [[ -f .env.gpu.example ]]; then
    cp .env.gpu.example .env
    echo "Created .env from .env.gpu.example"
  elif [[ -f .env.ocr.example ]]; then
    cp .env.ocr.example .env
    echo "Created .env from .env.ocr.example"
  else
    echo "No .env — copy .env.gpu.example to .env first" >&2
    exit 1
  fi
fi

GPU=0
GPU_NAME=""
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)
  if docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi >/dev/null 2>&1; then
    GPU=1
  elif docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
    GPU=1
  fi
fi

if [[ "$GPU" == "1" ]]; then
  echo ">>> NVIDIA GPU detected: ${GPU_NAME:-unknown} — Surya on CUDA"
  docker compose \
    -f docker-compose.yml \
    -f docker-compose.surya.yml \
    -f docker-compose.surya.gpu.yml \
    --profile surya up -d --build
else
  echo ">>> No usable NVIDIA GPU — Surya on CPU (still offloads web)"
  docker compose \
    -f docker-compose.yml \
    -f docker-compose.surya.yml \
    --profile surya up -d --build
fi

echo ">>> Waiting for Surya health..."
for i in $(seq 1 90); do
  if curl -sf http://127.0.0.1:8081/health >/dev/null 2>&1; then
    curl -s http://127.0.0.1:8081/health || true
    echo
    echo "OK: OCR stack is up. Web: http://127.0.0.1:${WEB_PORT:-8090}"
    echo "Watch GPU: watch -n1 nvidia-smi"
    exit 0
  fi
  sleep 5
done
echo "WARN: Surya not healthy yet. Check: docker compose logs -f surya-ocr"
exit 0
