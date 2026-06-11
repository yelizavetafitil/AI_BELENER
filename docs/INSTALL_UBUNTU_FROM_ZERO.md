# Ubuntu с нуля: AI_BELENER + DeepSeek-OCR (локально)

Все команды — под копирование в терминал Ubuntu 22.04/24.04.  
Чертежи **не уходят в облако** (при `PDF_LOCAL_ONLY=1` в `.env`).

**ВМ:** 24 GB RAM, 60 GB диск, 4 CPU, 8 GB swap — см. [VM_MEMORY.md](VM_MEMORY.md).

---

## Шаг 0. Обновление системы и swap (рекомендуется)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ca-certificates

# Swap 8 GB (если ещё нет)
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 8G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi
free -h
```

---

## Шаг 1. NVIDIA (если есть GPU)

```bash
# Драйвер (перезагрузка может понадобиться)
sudo apt install -y nvidia-driver-535
sudo reboot
```

После перезагрузки:

```bash
nvidia-smi
```

---

## Шаг 2. Docker + Docker Compose

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
newgrp docker

docker --version
docker compose version
```

---

## Шаг 3. NVIDIA Container Toolkit (для GPU в Docker)

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

Без GPU этот шаг пропустите — будет только Tesseract (шаг 6 всё равно поднимется).

---

## Шаг 4. DeepSeek-OCR на GPU (vLLM)

```bash
cd ~
git clone https://github.com/mbrcic/Deepseek-OCR-vllm-docker.git
cd Deepseek-OCR-vllm-docker
```

Скачать модель (~6–8 GB, один раз):

```bash
sudo apt install -y python3-pip python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -U huggingface_hub

# при необходимости: huggingface-cli login
huggingface-cli download deepseek-ai/DeepSeek-OCR --local-dir ./models/DeepSeek-OCR
```

Запуск vLLM (порт **8000**):

```bash
cd ~/Deepseek-OCR-vllm-docker
docker compose up -d --build
```

Проверка (подождите 1–3 мин загрузки модели):

```bash
curl -s http://127.0.0.1:8000/health
docker compose logs -f --tail 50
```

Оставьте этот терминал или запустите в фоне — сервис должен работать постоянно.

---

## Шаг 5. Проект Belener

```bash
cd ~
git clone https://github.com/yelizavetafitil/AI_BELENER.git
cd AI_BELENER

cp .env.deepseek.example .env
```

Если vLLM на **этой же** машине — в `.env` уже указано:

```text
DEEPSEEK_BACKEND_URL=http://host.docker.internal:8000
```

Если vLLM на другом сервере — отредактируйте:

```bash
nano .env
# DEEPSEEK_BACKEND_URL=http://IP_СЕРВЕРА_С_GPU:8000
```

---

## Шаг 6. Запуск Belener + адаптер DeepSeek

```bash
cd ~/AI_BELENER

docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d --build
```

Первый запуск 5–15 минут (сборка образа `web`).

Проверка:

```bash
docker compose ps
curl -s http://127.0.0.1:8080/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/
```

---

## Шаг 7. Открыть в браузере

С этой же машины:

```text
http://127.0.0.1:8090
```

С другого ПК в сети (замените IP):

```text
http://192.168.x.x:8090
```

Загрузите PDF — обработка локальная.

---

## Полезные команды

```bash
# Логи
cd ~/AI_BELENER
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml logs -f web
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml logs -f deepseek-ocr

# Память
docker stats --no-stream

# Остановить
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek down

# Запустить снова
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d

# Обновить код с GitHub
git pull
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d --build
```

---

## Без GPU (только Tesseract)

```bash
cd ~/AI_BELENER
cp .env.example .env
# в .env: PDF_OCR_ENGINE=tesseract
docker compose up -d --build
```

Шаг 4 (vLLM) не нужен.

---

## Если что-то не работает

| Проблема | Действие |
|----------|----------|
| `8080/health` — backend_healthy: false | Запустите шаг 4, проверьте `curl :8000/health` |
| Нет места на диске | `docker system df`, `docker system prune -a` |
| Мало RAM | `free -h`, swap, см. [VM_MEMORY.md](VM_MEMORY.md) |
| OCR пустой | `docker compose logs web`, проверьте `PDF_OCR_ENGINE=deepseek` в `.env` |
