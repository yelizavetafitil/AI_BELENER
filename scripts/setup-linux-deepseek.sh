#!/usr/bin/env bash
# Быстрая установка AI_BELENER + профиль DeepSeek-OCR на Linux
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/yelizavetafitil/AI_BELENER.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/AI_BELENER}"
VLLM_DIR="${VLLM_DIR:-$HOME/Deepseek-OCR-vllm-docker}"

echo "==> Клонирование Belener"
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

if [ ! -f .env ]; then
  cp .env.deepseek.example .env
  echo "Создан .env из .env.deepseek.example"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Установите Docker: https://docs.docker.com/engine/install/"
  exit 1
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "==> GPU обнаружен. Рекомендуется vLLM DeepSeek-OCR:"
  echo "    git clone https://github.com/mbrcic/Deepseek-OCR-vllm-docker.git $VLLM_DIR"
  echo "    cd $VLLM_DIR && docker compose up -d --build"
else
  echo "==> GPU не найден — будет Tesseract fallback (PDF_OCR_FALLBACK_TESS=1)"
fi

echo "==> Запуск Belener (профиль deepseek)"
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d --build

echo ""
echo "Готово:"
echo "  Web UI:  http://127.0.0.1:${WEB_PORT:-8090}"
echo "  Adapter: http://127.0.0.1:${DEEPSEEK_ADAPTER_PORT:-8080}/health"
echo "  Документация: docs/DEPLOY_LINUX_DEEPSEEK.md"
