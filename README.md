# AI_BELENER

Локальное веб-приложение для чтения инженерных чертежей (PDF): OCR, таблицы перечня/легенды, основная надпись. Без обязательной отправки данных в облако.

## Запуск (Docker)

```bash
cp .env.example .env
docker compose up -d --build
```

Веб-интерфейс: http://localhost:8090

Опционально — модели Ollama для vision (если включено в `.env`):

```bash
docker compose exec ollama ollama pull qwen2.5vl:7b
```

## Основные переменные

| Переменная | Описание |
|------------|----------|
| `PDF_REPORT_FAITHFUL=1` | Только OCR/парсер, без подгонки LLM |
| `PDF_VISION_TABLES=0` | Не «додумывать» строки таблиц через vision |
| `PDF_TABLE_DPI=560` | DPI для таблиц на сканах |

См. `.env.example`.

## Структура

- `belener/` — пайплайн извлечения (зоны, OCR, парсинг, отчёт)
- `app.py` — Flask API
- `docker-compose.yml` — web, PostgreSQL, Ollama
