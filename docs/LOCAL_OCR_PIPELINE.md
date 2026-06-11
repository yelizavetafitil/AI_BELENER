# Локальный OCR без облака (AMD / CPU)

Пайплайн для точного чтения сканов ГОСТ-чертежей **без утечки** в облако.

## Быстрый старт (Surya + честный отчёт)

```powershell
cd D:\AI_BELENER
copy .env.surya.example .env
docker compose -f docker-compose.yml -f docker-compose.surya.yml --profile surya up -d --build
```

Сайт: http://localhost:8090

Первый запуск Surya скачает модели в Docker volume `surya_models` (2–4 GB, 5–15 мин).

## Этап 1 — честный OCR (Tesseract)

```powershell
copy .env.example .env
# В .env: PDF_REPORT_FAITHFUL=1, PDF_VISION_MODE=off, PDF_VISION_TABLES=0
docker compose up -d --build
docker compose exec web python scripts/validate_faithful.py --dir /workspace
```

Метрика: `ungrounded_count=0` для спецификации (строки BOM есть в OCR зон `spec_*`).

## Точность как в промышленных пайплайнах (Habr / ГОСТ)

Включено в `.env.surya.example` (всё **локально**, без облака):

| Механизм | Переменная | Зачем |
|----------|------------|--------|
| Многоракурсный OCR | `PDF_OCR_MULTIVIEW=1` | Текст под 0°/90°/270° (и др.) — как 7 углов в [статье на Habr](https://habr.com/ru/articles/1033824/) |
| Быстрый режим углов | `PDF_OCR_MULTIVIEW_FAST=1` | Только 3 угла на CPU |
| Два DPI для зон | `PDF_DISCOVER_MULTIDPI=1` | Якоря перечня/штампа на грубом и точном масштабе |
| Честный отчёт | `PDF_REPORT_FAITHFUL=1` | Строки BOM только если есть в OCR `spec_*` |
| Без vision-таблиц | `PDF_VISION_TABLES=0` | Нет галлюцинаций QF1/TL1 |

Полный пайплайн из статьи (6× YOLO, стрелки, Ra) — отдельный этап обучения: `docs/TRAINING.md`.

## Этап 2 — Surya

| Переменная | Значение |
|------------|----------|
| `PDF_OCR_ENGINE` | `surya` |
| `SURYA_OCR_URL` | `http://surya-ocr:8081` |
| `PDF_OCR_FALLBACK_TESS` | `1` |
| `PDF_LOCAL_ONLY` | `1` |
| `PDF_REPORT_FAITHFUL` | `1` |
| `PDF_OCR_MULTIVIEW` | `1` (Tesseract fallback; для Surya см. `PDF_OCR_MULTIVIEW_SURYA`) |

Проверка:

```bash
curl http://localhost:8081/health
docker compose exec web python scripts/benchmark_corpus.py --dir /workspace
```

## Этап 3 — обучение на ваших чертежах

```bash
docker compose exec web python scripts/export_training_crops.py --dir /workspace --out /workspace/data/training
```

Результат:

- `data/training/crops/<имя_pdf>/<zone>.png`
- `data/training/manifest.jsonl` — пары «кроп → baseline OCR»

Дальше (офлайн): разметка в [CVAT](https://github.com/opencv/cvat) / Label Studio, fine-tune PaddleOCR или YOLOv8 для зон.

## Корпус тестовых PDF

В корне проекта (gitignore): BNP-*, GCC-*, VR-*, и др.

## Что не используем

| Проект | Причина |
|--------|---------|
| [Vectra2D](https://github.com/prolincur/Vectra2D) | Облачный сервис |
| [Drawing2CAD](https://github.com/lllssc/Drawing2CAD) | Механика, векторный вход |
| [Deep Vectorization](https://github.com/mohamedelmesawy/Deep-Vectorization-of-Technical-Drawings) | Растр→линии, не BOM |
| Vision-LLM для таблиц | Галлюцинации QF1/TL1 |

## Память (Honor / 16 GB RAM)

| Сервис | Лимит |
|--------|-------|
| web | 6g |
| surya-ocr | 8g |
| db | 1g |

См. также [VM_MEMORY.md](VM_MEMORY.md).
