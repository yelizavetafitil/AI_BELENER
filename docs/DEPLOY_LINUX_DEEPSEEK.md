# Linux: AI_BELENER + DeepSeek-OCR (полностью локально)

Чертежи обрабатываются **только на вашем сервере**. Облачные API не используются, если вы не включите их вручную.

## Что нужно

| Компонент | Минимум |
|-----------|---------|
| ОС | Ubuntu 22.04+ / Debian 12 |
| GPU | NVIDIA 8–12 GB VRAM (DeepSeek-OCR) |
| RAM | **24 GB на ВМ** (32 GB с запасом; см. [VM_MEMORY.md](VM_MEMORY.md)) |
| Диск | ~40–60 GB (модель + Docker) |
| ПО | Docker, Docker Compose, NVIDIA Container Toolkit |

Полная пошаговая инструкция с нуля: **[INSTALL_UBUNTU_FROM_ZERO.md](INSTALL_UBUNTU_FROM_ZERO.md)**

## Быстрый старт (если Docker и GPU уже стоят)

```bash
# 1. Клонировать проект
git clone https://github.com/yelizavetafitil/AI_BELENER.git
cd AI_BELENER

# 2. Настроить окружение (локальный режим + DeepSeek)
cp .env.deepseek.example .env

# 3. GPU: DeepSeek-OCR (vLLM) — один раз скачать модель (~6–8 GB)
git clone https://github.com/mbrcic/Deepseek-OCR-vllm-docker.git ~/Deepseek-OCR-vllm-docker
cd ~/Deepseek-OCR-vllm-docker
# см. README репозитория: hf download deepseek-ai/DeepSeek-OCR ...
docker compose up -d --build
cd ~/AI_BELENER

# 4. Belener + адаптер OCR
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d --build

# 5. Проверка
curl -s http://127.0.0.1:8080/health | jq .
curl -s http://127.0.0.1:8090/ | head
```

Веб-интерфейс: **http://SERVER_IP:8090**

### Память ВМ (кратко)

| ВМ Ubuntu | Когда |
|-----------|--------|
| **24 GB RAM** | DeepSeek + Belener (рекомендуется) |
| **32 GB RAM** | если на хосте мало свободной памяти |
| **8 GB RAM** | только Tesseract, без GPU-модели |

Подробно: [docs/VM_MEMORY.md](VM_MEMORY.md)

## Архитектура (всё локально)

```text
Браузер → web:8090 (Flask)
              ↓ HTTP (внутр. сеть Docker)
         deepseek-ocr:8080 (адаптер Belener)
              ↓ HTTP (LAN / host.docker.internal)
         vLLM DeepSeek-OCR :8000 (GPU)
```

Параллельно при сбое DeepSeek: **Tesseract** в контейнере `web` (`PDF_OCR_FALLBACK_TESS=1`).

## Установка NVIDIA + Docker (если ещё нет)

```bash
# Драйвер (пример Ubuntu)
sudo apt update
sudo apt install -y nvidia-driver-535

# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
nvidia-smi
```

## DeepSeek-OCR на GPU (vLLM)

Рекомендуемый готовый стек: [mbrcic/Deepseek-OCR-vllm-docker](https://github.com/mbrcic/Deepseek-OCR-vllm-docker)

```bash
git clone https://github.com/mbrcic/Deepseek-OCR-vllm-docker.git
cd Deepseek-OCR-vllm-docker

# Скачать веса (нужен huggingface-cli, один раз)
pip install -U huggingface_hub
huggingface-cli download deepseek-ai/DeepSeek-OCR --local-dir ./models/DeepSeek-OCR

docker compose up -d --build
curl -s http://127.0.0.1:8000/health
```

Если vLLM на **другой машине** в LAN, в `.env` проекта Belener:

```env
DEEPSEEK_BACKEND_URL=http://192.168.1.50:8000
```

## Переменные приватности (важно)

| Переменная | Значение | Смысл |
|------------|----------|--------|
| `PDF_LOCAL_ONLY=1` | вкл | Vision и LLM-отчёт выключены |
| `PDF_REPORT_FAITHFUL=1` | вкл | Только OCR/парсер, без подгонки |
| `PDF_VISION_MODE=off` | выкл | Нет отправки изображений в Ollama |
| `PDF_OCR_ENGINE=deepseek` | DeepSeek | Основной OCR |
| `PDF_OCR_FALLBACK_TESS=1` | вкл | Резерв Tesseract |

Файл `.env` **не коммитьте** (уже в `.gitignore`).

## Только Tesseract (без GPU)

```bash
cp .env.example .env
# PDF_OCR_ENGINE=tesseract
# PDF_LOCAL_ONLY=1
docker compose up -d --build
```

## Обновление с GitHub

```bash
cd ~/AI_BELENER
git pull
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d --build
```

## Диагностика

```bash
# Адаптер
curl http://127.0.0.1:8080/health

# Логи
docker compose logs -f web
docker compose logs -f deepseek-ocr

# Тест OCR (нужен PNG)
curl -s -X POST http://127.0.0.1:8080/api/ocr -F "file=@test.png"
```

## Безопасность

- Не открывайте порты 8090/8080/8000 в интернет без VPN/файрвола.
- PDF удаляются после обработки (временные файлы в контейнере).
- Данные БД — только метаданные сессий на вашем `postgres` volume.
- Не задавайте `PDF_VISION_MODE=always` и `PDF_REPORT_LLM=1`, если нужна строгая локальность.
