# Память: ВМ Ubuntu и Docker

## Сколько RAM выделить ВМ Ubuntu

| Сценарий | RAM на ВМ | vCPU | Диск | Swap |
|----------|-----------|------|------|------|
| **Минимум** (Tesseract, без GPU-модели) | **8 GB** | 2 | 40 GB | 4 GB |
| **Рекомендуется** (DeepSeek-OCR + Belener) | **24 GB** | 4 | 60 GB | 8 GB |
| **С запасом** (стабильно, без ERROR_NO_SYSTEM_RESOURCES) | **32 GB** | 4–6 | 80 GB | 8 GB |

### Почему не 8 GB для DeepSeek

| Компонент | RAM (пик) |
|-----------|-------------|
| Ubuntu + Docker | ~1.5 GB |
| Belener `web` (OCR PDF) | 2–4 GB |
| PostgreSQL + адаптер | ~0.5 GB |
| vLLM DeepSeek-OCR (на GPU, часть в RAM) | 4–8 GB |
| **Запас 20–30%** | обязателен |

GPU с 8–12 GB VRAM снимает часть нагрузки с RAM, но **не всю** — оставляйте **24 GB** на ВМ.

### Хост Windows (VirtualBox / Hyper-V)

| Параметр | Значение |
|----------|----------|
| RAM хоста | **32 GB+** (если ВМ 24 GB) |
| RAM ВМ | **24 GB** (не 8) |
| Процессоры ВМ | **4** |
| Видеопамять ВМ | 128 MB (достаточно; GPU — passthrough или на хосте) |

Ошибка `WHvSetupPartition / ERROR_NO_SYSTEM_RESOURCES` — нехватка RAM у **хоста** или конфликт Hyper-V. Закройте лишние программы, увеличьте RAM ВМ, включите swap в Ubuntu.

---

## Настройки памяти в Docker (уже в compose)

В `.env` / `.env.deepseek.example`:

```env
WEB_MEM_LIMIT=6g
WEB_MEM_RESERVATION=1536m
DB_MEM_LIMIT=1024m
DB_MEM_RESERVATION=256m
DEEPSEEK_ADAPTER_MEM_LIMIT=512m
DEEPSEEK_ADAPTER_MEM_RESERVATION=128m
```

- **mem_limit** — максимум для контейнера (защита от «съедания» всей ВМ).
- **mem_reservation** — резерв; Docker старается не отдавать эту память другим контейнерам.

Ollama **не стартует** без `--profile ollama` (экономия ~2–8 GB).

---

## Swap в Ubuntu (рекомендуется)

```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

---

## Проверка после запуска

```bash
docker stats --no-stream
free -h
```
