# AI_BELENER

Локальное веб-приложение для чтения инженерных чертежей (PDF): OCR, таблицы перечня/легенды, основная надпись. **Без отправки чертежей в облако** при локальном режиме.

## Рекомендуемый старт (Windows / AMD, без NVIDIA)

**Точный вывод** (5 этапов: эталоны, зоны, Surya, парсер, без vision): [docs/ACCURACY_PIPELINE.md](docs/ACCURACY_PIPELINE.md)

```powershell
cd AI_BELENER
copy .env.accuracy.example .env
docker compose -f docker-compose.yml -f docker-compose.surya.yml --profile surya up -d --build
python scripts/check_accuracy_setup.py
```

Альтернатива: [`.env.surya.example`](.env.surya.example) — то же + Surya, см. [LOCAL_OCR_PIPELINE.md](docs/LOCAL_OCR_PIPELINE.md)

Проверка корпуса PDF: `docker compose exec web python scripts/validate_faithful.py --dir /workspace`

## Быстрый старт (Linux + DeepSeek-OCR, GPU)

```bash
git clone https://github.com/yelizavetafitil/AI_BELENER.git
cd AI_BELENER
cp .env.deepseek.example .env
# GPU: поднять vLLM DeepSeek-OCR (см. docs/DEPLOY_LINUX_DEEPSEEK.md)
docker compose -f docker-compose.yml -f docker-compose.deepseek.yml --profile deepseek up -d --build
```

- С нуля (все команды): [docs/INSTALL_UBUNTU_FROM_ZERO.md](docs/INSTALL_UBUNTU_FROM_ZERO.md)  
- Кратко: [docs/DEPLOY_LINUX_DEEPSEEK.md](docs/DEPLOY_LINUX_DEEPSEEK.md)

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
