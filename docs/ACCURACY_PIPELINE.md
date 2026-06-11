# Пайплайн точного вывода (5 этапов)

Чеклист: что **уже в проекте**, что **нужно сделать вам**.

| Этап | Статус в коде | Настройка / действие |
|------|---------------|----------------------|
| **1. Эталон (labels)** | Частично: 2 golden JSON + labels для 1760, ГП9 | Вручную: `data/training/labels/<stem>_spec_right.txt` = весь текст с PNG. Цель 5–10 листов. |
| **2. Зоны** | Да: `zone_refine`, `legend_table`, `spec_left`, `sheet_notes` | `.env`: `PDF_CV_ZONE_REFINE=1`, `PDF_DISCOVER_ZONES_FAST=1` |
| **3. OCR** | Surya + Tesseract; **Paddle HTTP** для `spec_*`/`stamp_*` после fine-tune | `bootstrap_training_full.py` → правка labels → `train_paddle_rec.py`. См. `docs/TRAINING.md`, `.env.paddle.example` |
| **2b. YOLO зон** | Опционально: `export_yolo_dataset.py` + `train_yolo_zones.py` | `PDF_YOLO_ZONES=1` |
| **4. Парсер + отчёт** | `parse_specification`, `parse_legend`, `parse_numbered_notes`, штамп | Веб → markdown без полного текстового слоя |
| **5. Точность vs риск** | Faithful + vision off | `.env.accuracy.example` |

## Два типа чертежей

| Тип | Примеры | Как читает Belener | Время |
|-----|---------|-------------------|-------|
| **CAD export** | 1118-ГП9, GCC-IOT, часть VR | Текстовый слой PDF по зонам (`belener_text_layer`) | ~30 с – 2 мин |
| **Чистый скан** | BNP-1828, BNP 559, … | OCR Tesseract по `spec_right` + штамп | ~2–5 мин |

Определение автоматическое: если на странице ≥ `PDF_TEXT_LAYER_FAST_MIN` символов текста — CAD-путь.

## Быстрый старт (без долгого accuracy)

```powershell
cd D:\AI_BELENER
copy .env.accuracy.example .env
docker compose -f docker-compose.yml -f docker-compose.surya.yml --profile surya up -d
python scripts/check_accuracy_setup.py
docker compose up -d --build web
```

Веб: http://localhost:8090 — ожидайте **минуты, не 15–30**, на лист.

**Отключено для скорости:** `PDF_EXTRACT_MODE=accuracy`, multiview, multidpi, CV-ячейки, stamp_grid, body OCR, полный текст PDF.

**Оставлено для точности:** зоны (`PDF_CV_ZONE_REFINE`), парсер, faithful, legend_table.

## Этап 1 — эталоны

```powershell
# Кропы всех PDF из scan/
docker compose exec web python scripts/export_training_crops.py --dir /workspace/scan --out /app/data/training --no-ocr

# Авто-OCR кропов (черновик для правки)
docker compose exec web python scripts/ocr_training_crops.py --engine surya --zones spec_right spec_left stamp_frame legend_table

# После ручной правки labels:
python scripts/rebuild_train_list.py
python scripts/validate_golden.py
```

Шаблон golden: `data/training/golden/_template.json`

## Этап 2 — зоны

Уже в extract: `belener/zone_refine.py`, `discover.py` (legend_table).

Проверка кропов: `data/training/crops/<pdf>/spec_right.png` — таблица не обрезана.

YOLO (опционально): `docs/TRAINING.md` этап 4.

## Этап 3 — OCR

Без правки labels: Surya в `.env` — максимум из коробки.

С правкой labels → Paddle fine-tune на другой машине.

## Этап 4 — отчёт «как в чате»

Веб собирает: таблицы (спецификация / экспликация / легенда) + штамп + указания (если есть).

Локальный markdown без веба:

```powershell
docker compose exec web python scripts/export_full_page_text.py "BNP_1760" --use-labels --scan /workspace/scan
```

## Этап 5 — без галлюцинаций

| Режим | Переменные |
|-------|------------|
| **Точный (рекомендуется)** | `PDF_REPORT_FAITHFUL=1`, `PDF_LOCAL_ONLY=1`, vision off |
| Полный текст PDF | `PDF_REPORT_FULL_TEXT=1` (не для сметы) |
| Vision-дозаполнение | `PDF_VISION_POST=1` + Ollama (риск выдумки) |

## Ограничения (честно)

- **2 эталона** не покрывают VR, BNP-1828 и др. — нужны ваши `labels` или golden JSON.
- **accuracy** на CPU: 5–20 мин/лист, 16 GB RAM — по одному PDF.
- **100% как ChatGPT vision** без дообучения и без vision — недостижимо; цель — **сверяемый** OCR + парсер.

## Проверка после настройки

```powershell
python scripts/check_accuracy_setup.py
docker compose exec web python scripts/validate_golden.py
docker compose exec web python scripts/validate_faithful.py --dir /workspace/scan --limit 3
```
