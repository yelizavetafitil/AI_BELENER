# AI_BELENER

Локальное веб-приложение для чтения инженерных чертежей (PDF): OCR, таблицы перечня/легенды, основная надпись. **Без отправки чертежей в облако** при локальном режиме.

## Быстрый старт (Linux + DeepSeek-OCR, GPU)

```bash
git clone https://github.com/yelizavetafitil/AI_BELENER.git
cd AI_BELENER
cp .env.deepseek.example .env
# GPU: поднять vLLM DeepSeek-OCR (см. docs/DEPLOY_LINUX_DEEPSEEK.md)
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d --build
```

Подробно: [docs/DEPLOY_LINUX_DEEPSEEK.md](docs/DEPLOY_LINUX_DEEPSEEK.md)

Или: `bash scripts/setup-linux-deepseek.sh`

## Запуск без GPU (только Tesseract)

```bash
cp .env.example .env
docker compose up -d --build
```

Веб-интерфейс: http://localhost:8090

## Приватность

| Переменная | Описание |
|------------|----------|
| `PDF_LOCAL_ONLY=1` | Vision и LLM-отчёт выключены |
| `PDF_REPORT_FAITHFUL=1` | Только OCR/парсер |
| `PDF_OCR_ENGINE=deepseek` | Локальный DeepSeek-OCR |
| `PDF_VISION_TABLES=0` | Vision не «додумывает» таблицы |

См. `.env.deepseek.example`.

## Структура

- `belener/` — пайплайн извлечения
- `services/deepseek_ocr/` — адаптер API DeepSeek-OCR
- `docker-compose.deepseek.yml` — профиль `deepseek`
